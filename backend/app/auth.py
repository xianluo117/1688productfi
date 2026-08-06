import hashlib
import hmac
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
    return URLSafeTimedSerializer(settings.session_secret, salt="admin-session")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    return password_hash.verify(password, stored_hash)


def create_session_token(user: AdminUser, settings: Settings) -> str:
    return get_serializer(settings).dumps({"user_id": user.id, "username": user.username})


def get_current_admin(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminUser:
    token = request.cookies.get("admin_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = get_serializer(settings).loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired") from exc
    user = db.get(AdminUser, payload.get("user_id"))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Administrator not found")
    return user


def require_ingest_token(
    authorization: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API token")
    supplied = authorization.removeprefix("Bearer ").strip()
    expected_digest = hashlib.sha256(settings.ingest_api_token.encode()).digest()
    supplied_digest = hashlib.sha256(supplied.encode()).digest()
    if not hmac.compare_digest(expected_digest, supplied_digest):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")


def bootstrap_admin(db: Session, settings: Settings) -> None:
    user = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
    if user:
        return
    db.add(AdminUser(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
    db.commit()
