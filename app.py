"""FastAPI dashboard — read-only view of bot data.

Reads from the same JSON files the bot uses:
- data/session.json (via store.py)
- data/tugas_tracker.json (via tracker.py)

No write endpoints — login/logout stays via Telegram only.
"""

import os
import json
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

from store import (
    _load as load_session,
    get_current_semester,
    get_attendance_map,
    get_course_schedule,
    has_credentials,
)
from tracker import (
    _load as load_tracker,
    get_all_assignments,
    get_pending_assignments,
    get_submitted_assignments,
    get_grades_snapshot,
    get_submission_history,
    get_stats,
)


app = FastAPI(title="SPADA Bot Dashboard", version="1.0.0")

templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)


# ── API endpoints (JSON) ──────────────────────────────────────

@app.get("/api/status")
async def api_status():
    """Bot status overview."""
    session = load_session()
    tracker = load_tracker()
    schedule = get_course_schedule()
    return {
        "logged_in": has_credentials(),
        "semester": get_current_semester(),
        "courses_in_schedule": len(schedule),
        "total_assignments": len(tracker.get("assignments", [])),
        "pending_assignments": len([a for a in tracker.get("assignments", []) if a["status"] == "pending"]),
        "submitted_assignments": len([a for a in tracker.get("assignments", []) if a["status"] == "submitted"]),
        "grades_count": len(tracker.get("grades", [])),
        "server_time": datetime.now().isoformat(),
    }


@app.get("/api/schedule")
async def api_schedule():
    """Course schedule."""
    return get_course_schedule()


@app.get("/api/assignments")
async def api_assignments(status: Optional[str] = None):
    """Assignments filtered by status (pending/submitted/all)."""
    if status == "pending":
        return get_pending_assignments()
    elif status == "submitted":
        return get_submitted_assignments()
    return get_all_assignments()


@app.get("/api/grades")
async def api_grades():
    """Current grades."""
    return get_grades_snapshot()


@app.get("/api/submissions")
async def api_submissions():
    """Submission history."""
    return get_submission_history()


@app.get("/api/stats")
async def api_stats():
    """Overall stats."""
    return get_stats()


# ── HTML pages ─────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    session = load_session()
    tracker = load_tracker()
    schedule = get_course_schedule()
    assignments = get_all_assignments()
    grades = get_grades_snapshot()
    stats = get_stats()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "logged_in": has_credentials(),
        "semester": get_current_semester(),
        "schedule": schedule,
        "assignments": assignments,
        "grades": grades,
        "stats": stats,
        "now": datetime.now(),
    })


@app.get("/jadwal", response_class=HTMLResponse)
async def page_jadwal(request: Request):
    """Schedule page."""
    schedule = get_course_schedule()
    return templates.TemplateResponse("jadwal.html", {
        "request": request,
        "schedule": schedule,
    })


@app.get("/tugas", response_class=HTMLResponse)
async def page_tugas(request: Request):
    """Assignments page."""
    assignments = get_all_assignments()
    return templates.TemplateResponse("tugas.html", {
        "request": request,
        "assignments": assignments,
    })


@app.get("/nilai", response_class=HTMLResponse)
async def page_nilai(request: Request):
    """Grades page."""
    grades = get_grades_snapshot()
    return templates.TemplateResponse("nilai.html", {
        "request": request,
        "grades": grades,
    })


@app.get("/absensi", response_class=HTMLResponse)
async def page_absensi(request: Request):
    """Attendance log page."""
    tracker = load_tracker()
    submissions = tracker.get("submissions", [])
    return templates.TemplateResponse("absensi.html", {
        "request": request,
        "submissions": submissions,
    })


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
