"""Environment loading and validation.

Every secret comes from environment variables (never from the repo). Locally we
also read a `.env` file if one exists, without overriding real env vars.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default) or default
    return value.strip().strip('"').strip("'")


# name -> (required?, plain description shown on the setup page)
VARIABLES: dict[str, tuple[bool, str]] = {
    "DATABASE_URL": (True, "Neon pooled connection string (postgresql://...-pooler...?sslmode=require)"),
    "CRON_SECRET": (True, "Long random string. Vercel Cron sends it as 'Authorization: Bearer ...' to the job routes"),
    "GEMINI_API_KEY": (False, "Google AI Studio key. Without it, reports use the plain fallback template"),
    "GEMINI_MODEL": (False, "Gemini Flash model id (default gemini-3.8-flash)"),
    "GNEWS_API_KEY": (False, "gnews.io key. Without it, news comes from RSS feeds only"),
    "RESEND_API_KEY": (False, "Resend key. Without it, reports are generated but not emailed"),
    "REPORT_EMAIL_TO": (False, "Where the Saturday report is emailed"),
    "REPORT_EMAIL_FROM": (False, "Sender, e.g. Mausam <onboarding@resend.dev>"),
    "APP_BASE_URL": (False, "Public URL of this app, used for links in emails"),
    "INVESTING_PLATFORM_URL": (False, "Where Meera actually invests (default MF Central)"),
    "TZ_DISPLAY": (False, "Display time zone (default Asia/Kolkata)"),
    "DB_SCHEMA": (False, "Optional Postgres schema name if sharing a database with another app"),
}


class Config:
    def __init__(self) -> None:
        self.DATABASE_URL = _env("DATABASE_URL")
        self.CRON_SECRET = _env("CRON_SECRET")
        self.GEMINI_API_KEY = _env("GEMINI_API_KEY")
        self.GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.8-flash")
        self.GNEWS_API_KEY = _env("GNEWS_API_KEY")
        self.RESEND_API_KEY = _env("RESEND_API_KEY")
        self.REPORT_EMAIL_TO = _env("REPORT_EMAIL_TO")
        self.REPORT_EMAIL_FROM = _env("REPORT_EMAIL_FROM", "Mausam <onboarding@resend.dev>")
        self.APP_BASE_URL = _env("APP_BASE_URL", "http://localhost:5000").rstrip("/")
        self.INVESTING_PLATFORM_URL = _env("INVESTING_PLATFORM_URL", "https://www.mfcentral.com")
        self.TZ_DISPLAY = _env("TZ_DISPLAY", "Asia/Kolkata")
        self.DB_SCHEMA = _env("DB_SCHEMA")
        self.GITHUB_REPO = _env("GITHUB_REPO", "Anusha-Karwa/mausam")

    def missing_required(self) -> list[str]:
        return [name for name, (required, _) in VARIABLES.items() if required and not getattr(self, name, "")]

    def status(self) -> list[dict]:
        """Which variables are set, for the setup and status pages. Never exposes values."""
        return [
            {"name": name, "required": required, "description": desc, "set": bool(getattr(self, name, ""))}
            for name, (required, desc) in VARIABLES.items()
        ]


config = Config()
