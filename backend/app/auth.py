import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import AdminUser


password_hash = PasswordHash.recommended()


def get_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="user-session")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    return password_hash.verify(password, stored_hash)


def create_session_token(user: AdminUser, settings: Settings) -> str:
    return get_serializer(settings).dumps({"user_id": user.id, "username": user.username})


def hash_ingest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_ingest_token(user: AdminUser) -> str:
    token = f"zoo_{secrets.token_urlsafe(32)}"
    user.ingest_token_hash = hash_ingest_token(token)
    user.ingest_token_prefix = token[:12]
    user.ingest_token_updated_at = datetime.now(timezone.utc)
    return token


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminUser:
    token = request.cookies.get("user_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = get_serializer(settings).loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired") from exc
    user = db.get(AdminUser, payload.get("user_id"))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    return user


def require_admin(user: Annotated[AdminUser, Depends(get_current_user)]) -> AdminUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def get_ingest_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> AdminUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API token")
    supplied = authorization.removeprefix("Bearer ").strip()
    supplied_hash = hash_ingest_token(supplied)
    user = db.scalar(select(AdminUser).where(AdminUser.ingest_token_hash == supplied_hash))
    if not user or not user.ingest_token_hash or not hmac.compare_digest(user.ingest_token_hash, supplied_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    return user


def bootstrap_admin(db: Session, settings: Settings) -> None:
    user = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
    if user:
        user.role = "admin"
        user.is_active = True
        db.commit()
        return
    db.add(
        AdminUser(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role="admin",
            is_active=True,
        )
    )
    db.commit()
