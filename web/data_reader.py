"""Safe JSON file reader for bot data files."""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")
TRACKER_FILE = os.path.join(DATA_DIR, "tugas_tracker.json")


def _read_json(path: str, default):
    """Read a JSON file safely, returning default on any error."""
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return default


def read_session() -> dict:
    """Read data/session.json. Returns dict with schedule, semester, etc."""
    defaults = {
        "username": "",
        "password": "",
        "current_semester": "",
        "attendance_map": {},
        "course_schedule": {},
    }
    data = _read_json(SESSION_FILE, defaults)
    merged = {**defaults, **data}
    # Mask password
    if merged.get("password"):
        merged["password"] = "***"
    return merged


def read_tracker() -> dict:
    """Read data/tugas_tracker.json."""
    defaults = {
        "assignments": [],
        "submissions": [],
        "grades": [],
        "daily_log": [],
    }
    data = _read_json(TRACKER_FILE, defaults)
    merged = {**defaults, **data}
    return merged


def get_bot_status() -> dict:
    """Read both files and return a summary status dict."""
    session = _read_json(SESSION_FILE, {})
    tracker = _read_json(TRACKER_FILE, {"assignments": [], "grades": [], "submissions": []})
    assignments = tracker.get("assignments", [])
    return {
        "logged_in": bool(session.get("username") and session.get("password")),
        "semester": session.get("current_semester", ""),
        "student_name": session.get("student_name", ""),
        "courses_count": len(session.get("course_schedule", {})),
        "assignments_pending": len([a for a in assignments if a.get("status") == "pending"]),
        "assignments_submitted": len([a for a in assignments if a.get("status") == "submitted"]),
        "grades_count": len(tracker.get("grades", [])),
        "submissions_count": len(tracker.get("submissions", [])),
    }
