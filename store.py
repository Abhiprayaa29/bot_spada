"""Persistent session store — credentials, detected semester, attendance map, schedule.

All data lives in data/session.json so the bot survives restarts without
hardcoding anything in config.py.
"""

import os
import json
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")

_DEFAULTS = {
    "username": "",
    "password": "",
    "current_semester": "",
    "bima_semester": "",       # semester from BIMA (more accurate)
    "bima_courses": [],        # enrolled courses from BIMA [{name, id}]
    "attendance_map": {},      # {course_name: attendance_id}
    "course_schedule": {},     # {course_name: {day, start, end}}
}


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> dict:
    _ensure_dir()
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                data = json.load(f)
            # Merge with defaults in case file is old
            merged = {**_DEFAULTS, **data}
            return merged
        except (json.JSONDecodeError, IOError):
            pass
    return dict(_DEFAULTS)


def _save(data: dict):
    _ensure_dir()
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Credentials ──────────────────────────────────────────────

def has_credentials() -> bool:
    s = _load()
    return bool(s.get("username") and s.get("password"))


def get_credentials() -> tuple[str, str]:
    s = _load()
    return s.get("username", ""), s.get("password", "")


def save_credentials(username: str, password: str):
    s = _load()
    s["username"] = username
    s["password"] = password
    _save(s)


def clear_credentials():
    s = _load()
    s["username"] = ""
    s["password"] = ""
    _save(s)


# ── Semester ─────────────────────────────────────────────────

def get_current_semester() -> str:
    return _load().get("current_semester", "")


def save_current_semester(semester: str):
    s = _load()
    s["current_semester"] = semester
    _save(s)


# ── BIMA Semester ────────────────────────────────────────────

def get_bima_semester() -> str:
    return _load().get("bima_semester", "")


def save_bima_semester(semester: str):
    s = _load()
    s["bima_semester"] = semester
    _save(s)


# ── BIMA Courses ──────────────────────────────────────────────

def get_bima_courses() -> list[dict]:
    """Return enrolled courses from BIMA: [{name, id}, ...]"""
    return _load().get("bima_courses", [])


def save_bima_courses(courses: list[dict]):
    s = _load()
    s["bima_courses"] = courses
    _save(s)


def get_bima_course_names() -> set[str]:
    """Return just the course names from BIMA for quick matching."""
    return {c["name"] for c in get_bima_courses() if c.get("name")}


# ── Attendance Map ───────────────────────────────────────────

def get_attendance_map() -> dict:
    return _load().get("attendance_map", {})


def save_attendance_map(amap: dict):
    s = _load()
    s["attendance_map"] = amap
    _save(s)


# ── Course Schedule ──────────────────────────────────────────

def get_course_schedule() -> dict:
    return _load().get("course_schedule", {})


def save_course_schedule(schedule: dict):
    s = _load()
    s["course_schedule"] = schedule
    _save(s)


def update_course_schedule_entry(course_name: str, day: str, start: str, end: str):
    s = _load()
    if "course_schedule" not in s:
        s["course_schedule"] = {}
    s["course_schedule"][course_name] = {"day": day, "start": start, "end": end}
    _save(s)
