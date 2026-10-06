"""Add-on settings: /data/options.json written by HA + environment variables."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(os.environ.get("BUDGET_DATA_DIR", "/data"))
STATIC_DIR = Path(os.environ.get("BUDGET_STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "budget.db"
BACKUP_DIR = DATA_DIR / "backups"

# Fixed IP the HA Supervisor ingress proxy uses to connect to the add-on
INGRESS_IP = "172.30.32.2"
SUPERVISOR_URL = "http://supervisor"


@dataclass
class Settings:
    ollama_url: str = ""
    telegram_bot_token: str = ""
    ollama_model: str = "qwen3-vl:8b-instruct"
    notify_services: list[str] = field(default_factory=list)
    allowed_users: list[str] = field(default_factory=list)
    telegram_auto_delete_hours: int = 24
    notify_time: str = "09:00"
    timezone: str = "UTC"
    # Development: without ingress, sign in as this user ("id:Name")
    dev_user: str | None = None
    supervisor_token: str | None = None

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ollama_url)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _ha_timezone() -> str | None:
    """Home Assistant's configured time zone (used when the add-on option is empty)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        import httpx

        r = httpx.get(f"{SUPERVISOR_URL}/core/api/config", headers={"Authorization": f"Bearer {token}"}, timeout=5)
        tz = r.json().get("time_zone")
        ZoneInfo(tz)
        return tz
    except Exception:
        return None


def load_settings() -> Settings:
    opts: dict = {}
    options_file = DATA_DIR / "options.json"
    if options_file.exists():
        opts = json.loads(options_file.read_text())
    s = Settings(
        ollama_url=opts.get("ollama_url") or os.environ.get("OLLAMA_URL", ""),
        telegram_bot_token=opts.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        ollama_model=opts.get("ollama_model") or os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b-instruct"),
        notify_services=[x for x in opts.get("notify_services", []) if x],
        allowed_users=[x for x in opts.get("allowed_users", []) if x],
        telegram_auto_delete_hours=int(opts.get("telegram_auto_delete_hours", 24)),
        notify_time=opts.get("notify_time", "09:00"),
        timezone=opts.get("timezone") or os.environ.get("TZ") or _ha_timezone() or "UTC",
        dev_user=os.environ.get("BUDGET_DEV_USER"),
        supervisor_token=os.environ.get("SUPERVISOR_TOKEN"),
    )
    return s


settings = load_settings()
