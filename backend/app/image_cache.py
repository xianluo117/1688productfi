import ipaddress
import mimetypes
import socket
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import Settings


ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def validate_public_image_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("unsupported image URL")
    default_port = 443 if parsed.scheme == "https" else 80
    for address in socket.getaddrinfo(parsed.hostname, parsed.port or default_port, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("image URL resolves to a non-public address")


async def cache_product_image(offer_id: str, image_url: str, settings: Settings, namespace: str = "1688") -> str:
    validate_public_image_url(image_url)
    settings.image_storage_path.mkdir(parents=True, exist_ok=True)

    async def validate_outgoing_request(request: httpx.Request) -> None:
        # HTTPX invokes request hooks for the initial request and every redirect,
        # preventing a public image URL from redirecting into a private network.
        validate_public_image_url(str(request.url))

    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=5,
        timeout=20,
        event_hooks={"request": [validate_outgoing_request]},
    ) as client:
        async with client.stream("GET", image_url, headers={"User-Agent": "Mozilla/5.0"}) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            extension = ALLOWED_IMAGE_TYPES.get(content_type)
            if not extension:
                extension = mimetypes.guess_extension(content_type or "")
            if extension not in ALLOWED_IMAGE_TYPES.values():
                raise ValueError("unsupported image content type")

            safe_namespace = "".join(character for character in namespace if character.isalnum() or character in {"-", "_"})
            url_version = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:12]
            filename = f"{safe_namespace}-{offer_id}-{url_version}{extension}"
            target = settings.image_storage_path / filename
            temporary = Path(f"{target}.tmp")
            size = 0
            try:
                with temporary.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > settings.max_image_bytes:
                            raise ValueError("image exceeds download limit")
                        output.write(chunk)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return filename
