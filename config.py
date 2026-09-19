"""Configuration for SPADA Telegram Bot.

Credentials, semester, attendance map, and schedule are loaded dynamically
from store.py (data/session.json).  Nothing is hardcoded here.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

from store import (
    get_credentials,
    get_current_semester,
    get_attendance_map,
    get_course_schedule,
)

load_dotenv()


@dataclass
class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # SPADA
    SPADA_BASE_URL: str = "https://spada.upnyk.ac.id"

    # Scheduler
    REMINDER_CHECK_INTERVAL_MINUTES: int = 30
    AUTO_ATTENDANCE_ENABLED: bool = True
    ATTENDANCE_WINDOW_MINUTES: int = 5

    # ── Dynamic fields (reloaded via helper methods) ──────────

    def _creds(self) -> tuple[str, str]:
        return get_credentials()

    @property
    def SPADA_USERNAME(self) -> str:
        return self._creds()[0]

    @property
    def SPADA_PASSWORD(self) -> str:
        return self._creds()[1]

    @property
    def CURRENTSEMESTER(self) -> str:
        return get_current_semester()

    @property
    def ATTENDANCE_MAP(self) -> dict:
        return get_attendance_map()

    @property
    def COURSE_SCHEDULE(self) -> dict:
        return get_course_schedule()


config = Config()
