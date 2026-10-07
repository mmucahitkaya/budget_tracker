"""Push notifications to the HA Companion app via the Supervisor API."""
import logging
import re

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import SUPERVISOR_URL, settings
from .models import SentNotice

log = logging.getLogger(__name__)


def send(title: str, message: str) -> None:
    from . import telegram

    message = re.sub(r"</?(b|i|code)>", "", message)  # notifications are plain text

    telegram.broadcast(title, message)
    if not settings.supervisor_token or not settings.notify_services:
        log.info("Notification (not sent, not configured): %s — %s", title, message)
        return
    headers = {"Authorization": f"Bearer {settings.supervisor_token}"}
    for service in settings.notify_services:
        name = service.removeprefix("notify.")
        try:
            httpx.post(
                f"{SUPERVISOR_URL}/core/api/services/notify/{name}",
                headers=headers,
                # Tapping the notification opens the Budget panel in the HA app
                json={"title": title, "message": message, "data": {"group": "budget_tracker", "url": panel_url()}},
                timeout=10,
            ).raise_for_status()
        except Exception as e:
            log.warning("Could not send notification (%s): %s", name, e)


_panel_url: str | None = None


def panel_url() -> str:
    """Home Assistant path of this add-on's panel (the slug includes the repository hash, so ask Supervisor)."""
    global _panel_url
    if _panel_url is None:
        slug = "local_budget_tracker"
        try:
            r = httpx.get(f"{SUPERVISOR_URL}/addons/self/info", headers={"Authorization": f"Bearer {settings.supervisor_token}"}, timeout=5)
            slug = r.json()["data"]["slug"]
        except Exception as e:
            log.debug("Add-on slug not available: %s", e)
        # The sidebar panel path also works for non-admin users (/hassio/... is admin-only)
        _panel_url = f"/{slug}"
    return _panel_url


def send_once(db: Session, key: str, title: str, message: str) -> bool:
    """Sends only if nothing was sent before with the same key."""
    db.add(SentNotice(key=key))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    send(title, message)
    return True
