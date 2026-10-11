"""Authentication and tokens (Phase 10).

Passwords and driver PINs: PBKDF2-SHA256 (stdlib only, no extra deps).
Tokens: HMAC-signed bearer strings with an expiry - stateless, so the API
stays simple to deploy. The secret comes from DISPATCH_SECRET.

The authenticated username is also pushed into a request-scoped context
variable that audit.append_audit reads, so every audit row written while
handling a request records WHO did it - no more anonymous "dispatcher".
"""
import base64
import hashlib
import hmac
import os
import secrets
from contextvars import ContextVar

# seconds a token stays valid (8 hours - a working shift)
TOKEN_TTL_SECONDS = 8 * 3600
SECRET = os.environ.get("DISPATCH_SECRET", "dev-secret-change-me")

# set by the auth dependency; audit.append_audit falls back to this as actor_id
current_actor: ContextVar[str | None] = ContextVar("current_actor",
                                                    default=None)


def hash_secret(plain: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, 600_000)
    return f"pbkdf2$600000${salt.hex()}${digest.hex()}"


def verify_secret(plain: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
    return hmac.compare_digest(digest.hex(), hash_hex)


def make_token(username: str, role: str, driver_id: int | None = None) -> str:
    payload = f"{username}|{role}|{driver_id or ''}|{TOKEN_TTL_SECONDS}"
    expires = int(__import__("time").time()) + TOKEN_TTL_SECONDS
    body = f"{username}|{role}|{driver_id or ''}|{expires}"
    raw = body.encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + sig


def parse_token(token: str) -> dict | None:
    """Return {username, role, driver_id, expires} or None when invalid."""
    try:
        body_b64, sig = token.rsplit(".", 1)
        raw = base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4))
        expected = hmac.new(SECRET.encode(), raw,
                            hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        username, role, driver_id, expires = raw.decode().split("|")
        if int(expires) < __import__("time").time():
            return None
        return {"username": username, "role": role,
                "driver_id": int(driver_id) if driver_id else None,
                "expires": int(expires)}
    except Exception:
        return None


BEARER = "Bearer "


def token_from_header(authorization: str | None) -> dict | None:
    if not authorization or not authorization.startswith(BEARER):
        return None
    return parse_token(authorization[len(BEARER):])
