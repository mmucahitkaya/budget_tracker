"""Identity: the HA ingress proxy forwards the signed-in HA user via headers."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import INGRESS_IP, settings
from .db import get_db
from .models import User


def _header_text(value: str) -> str:
    """HTTP headers are decoded as latin-1; fix non-ASCII names that were sent as UTF-8."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _identity(request: Request) -> tuple[str, str, str]:
    """(HA user id, display name, username)"""
    if settings.dev_user:
        uid, _, name = settings.dev_user.partition(":")
        # In development, can be overridden via header to try a second user
        uid = request.headers.get("x-dev-user-id", uid)
        name = _header_text(request.headers.get("x-dev-user-name", "")) or name or uid
        return uid, name, name
    client_ip = request.client.host if request.client else ""
    if client_ip != INGRESS_IP:
        raise HTTPException(403, "Only accessible through Home Assistant ingress")
    uid = request.headers.get("x-remote-user-id")
    if not uid:
        raise HTTPException(401, "HA user not found")
    username = _header_text(request.headers.get("x-remote-user-name") or "")
    display = _header_text(request.headers.get("x-remote-user-display-name") or "") or username or "User"
    return uid, display, username


def is_allowed(display: str, username: str) -> bool:
    """If allowed_users is empty, everyone is allowed; otherwise the HA username or display name must be in the list."""
    allowed = {a.strip().casefold() for a in settings.allowed_users if a.strip()}
    if not allowed:
        return True
    return display.casefold() in allowed or (bool(username) and username.casefold() in allowed)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid, name, username = _identity(request)
    if not is_allowed(name, username):
        raise HTTPException(403, "You do not have access to this app")
    user = db.scalar(select(User).where(User.ha_user_id == uid))
    if user is None:
        user = User(ha_user_id=uid, name=name, username=username)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # On first load, parallel requests may try to create the same user at the same time
            db.rollback()
            user = db.scalar(select(User).where(User.ha_user_id == uid))
    elif user.name != name or (username and user.username != username):
        user.name = name
        user.username = username or user.username
        db.commit()
    return user
