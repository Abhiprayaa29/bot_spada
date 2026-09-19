"""Local assignment tracker - JSON-based history for tugas, submissions, grades."""

import os
import json
from datetime import datetime
from typing import Optional


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TRACKER_FILE = os.path.join(DATA_DIR, "tugas_tracker.json")


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> dict:
    _ensure_data_dir()
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    return {"assignments": [], "submissions": [], "grades": [], "daily_log": []}


def _save(data: dict):
    _ensure_data_dir()
    with open(TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---- Assignments ----

def add_assignment(course: str, title: str, url: str, due_date: str = "") -> dict:
    """Add or update an assignment in the tracker."""
    data = _load()

    # Check if already exists
    for a in data["assignments"]:
        if a["url"] == url:
            a["course"] = course
            a["title"] = title
            a["due_date"] = due_date
            a["updated_at"] = datetime.now().isoformat()
            _save(data)
            return a

    entry = {
        "course": course,
        "title": title,
        "url": url,
        "due_date": due_date,
        "status": "pending",
        "submitted_at": None,
        "screenshot_path": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    data["assignments"].append(entry)
    _save(data)
    return entry


def mark_submitted(url: str, screenshot_path: str = "") -> bool:
    """Mark an assignment as submitted."""
    data = _load()
    for a in data["assignments"]:
        if a["url"] == url:
            a["status"] = "submitted"
            a["submitted_at"] = datetime.now().isoformat()
            if screenshot_path:
                a["screenshot_path"] = screenshot_path
            a["updated_at"] = datetime.now().isoformat()
            _save(data)

            # Also log the submission
            data["submissions"].append({
                "course": a["course"],
                "title": a["title"],
                "url": url,
                "submitted_at": a["submitted_at"],
                "screenshot_path": screenshot_path,
            })
            _save(data)
            return True
    return False


def get_pending_assignments() -> list[dict]:
    """Get all pending (not submitted) assignments."""
    data = _load()
    return [a for a in data["assignments"] if a["status"] == "pending"]


def get_submitted_assignments() -> list[dict]:
    """Get all submitted assignments."""
    data = _load()
    return [a for a in data["assignments"] if a["status"] == "submitted"]


def get_all_assignments() -> list[dict]:
    """Get all tracked assignments."""
    return _load()["assignments"]


def get_submission_history() -> list[dict]:
    """Get full submission history."""
    return _load()["submissions"]


# ---- Grades ----

def update_grades(grades: list[dict]) -> list[dict]:
    """Update grades and detect new ones. Returns list of new grades."""
    data = _load()
    existing = {g["course"]: g for g in data["grades"]}
    new_grades = []

    for g in grades:
        course = g.get("course", "")
        grade_val = g.get("grade", "")
        if not course:
            continue

        old = existing.get(course)
        if not old or old.get("grade") != grade_val:
            entry = {
                "course": course,
                "grade": grade_val,
                "first_seen": old["first_seen"] if old else datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            existing[course] = entry
            if old and old.get("grade") != grade_val:
                new_grades.append(entry)

    data["grades"] = list(existing.values())
    _save(data)
    return new_grades


def get_grades_snapshot() -> list[dict]:
    """Get current grades from tracker."""
    return _load()["grades"]


# ---- Daily Log ----

def log_daily_summary(summary: str):
    """Log a daily briefing."""
    data = _load()
    data["daily_log"].append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "summary": summary,
        "timestamp": datetime.now().isoformat(),
    })
    # Keep last 30 days
    data["daily_log"] = data["daily_log"][-30:]
    _save(data)


def get_stats() -> dict:
    """Get overall stats."""
    data = _load()
    all_a = data["assignments"]
    pending = [a for a in all_a if a["status"] == "pending"]
    submitted = [a for a in all_a if a["status"] == "submitted"]
    return {
        "total": len(all_a),
        "pending": len(pending),
        "submitted": len(submitted),
        "grades_count": len(data["grades"]),
        "submissions_count": len(data["submissions"]),
    }
