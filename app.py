"""FastAPI dashboard — read-only view of bot data.

Reads from the same JSON files the bot uses:
- data/session.json (via store.py)
- data/tugas_tracker.json (via tracker.py)

No write endpoints — login/logout stays via Telegram only.

HTML is rendered via Python string helpers to avoid Jinja2/Python 3.14 cache bug.
"""

import os
from typing import Optional
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

from store import (
    get_current_semester,
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

# ── HTML helpers ────────────────────────────────────────────────

NAV = """
<nav class="nav">
    <div class="nav-brand">SPADA</div>
    <div class="nav-links">
        <a href="/" class="{active_dashboard}">Dashboard</a>
        <a href="/jadwal" class="{active_jadwal}">Jadwal</a>
        <a href="/tugas" class="{active_tugas}">Tugas</a>
        <a href="/nilai" class="{active_nilai}">Nilai</a>
        <a href="/absensi" class="{active_absensi}">Absensi</a>
    </div>
</nav>
"""

STYLE = """
:root {
    --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
    --text: #f1f5f9; --text2: #94a3b8; --accent: #38bdf8;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
    --radius: 12px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
.nav { background: var(--surface); padding: 12px 16px; display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 100; border-bottom: 1px solid var(--surface2); }
.nav-brand { font-weight: 700; font-size: 18px; color: var(--accent); }
.nav-links { display: flex; gap: 4px; flex-wrap: wrap; }
.nav-links a { color: var(--text2); text-decoration: none; padding: 6px 12px; border-radius: 8px; font-size: 14px; }
.nav-links a:hover, .nav-links a.active { background: var(--surface2); color: var(--text); }
.container { max-width: 1200px; margin: 0 auto; padding: 20px 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 24px; }
.stat-card { background: var(--surface); border-radius: var(--radius); padding: 16px; }
.stat-card .label { color: var(--text2); font-size: 13px; margin-bottom: 4px; }
.stat-card .value { font-size: 28px; font-weight: 700; }
.stat-card .value.green { color: var(--green); }
.stat-card .value.yellow { color: var(--yellow); }
.stat-card .value.accent { color: var(--accent); }
.section { background: var(--surface); border-radius: var(--radius); padding: 20px; margin-bottom: 16px; }
.section h2 { font-size: 16px; margin-bottom: 16px; color: var(--text2); font-weight: 500; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; padding: 8px 12px; color: var(--text2); font-weight: 500; border-bottom: 1px solid var(--surface2); }
td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.05); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 600; }
.badge.pending { background: rgba(234,179,8,0.15); color: var(--yellow); }
.badge.submitted { background: rgba(34,197,94,0.15); color: var(--green); }
.empty { text-align: center; color: var(--text2); padding: 40px 20px; font-size: 14px; }
.day-group { margin-bottom: 16px; }
.day-group h3 { color: var(--accent); font-size: 14px; margin-bottom: 8px; padding-left: 4px; }
.course-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; background: rgba(255,255,255,0.03); border-radius: 8px; margin-bottom: 6px; font-size: 14px; }
.course-item .time { color: var(--accent); font-weight: 600; font-size: 13px; }
.course-card { background: var(--surface); border-radius: var(--radius); padding: 16px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
.course-card .info h3 { font-size: 15px; margin-bottom: 4px; }
.course-card .info .meta { font-size: 12px; color: var(--text2); }
.course-card .time { text-align: right; }
.course-card .time .hours { color: var(--accent); font-weight: 700; font-size: 16px; }
.course-card .time .room { font-size: 12px; color: var(--text2); margin-top: 2px; }
.task-card { background: var(--surface); border-radius: var(--radius); padding: 14px 16px; margin-bottom: 8px; }
.task-card .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.task-card .header h3 { font-size: 14px; font-weight: 600; }
.task-card .meta { font-size: 12px; color: var(--text2); }
.log-card { background: var(--surface); border-radius: var(--radius); padding: 14px 16px; margin-bottom: 8px; }
.log-card .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.log-card .header h3 { font-size: 14px; }
.log-card .meta { font-size: 12px; color: var(--text2); }
.grade { font-weight: 700; color: var(--green); }
"""


def _page(active: str, body: str) -> str:
    """Wrap body in full HTML page."""
    nav = NAV.format(
        active_dashboard="active" if active == "dashboard" else "",
        active_jadwal="active" if active == "jadwal" else "",
        active_tugas="active" if active == "tugas" else "",
        active_nilai="active" if active == "nilai" else "",
        active_absensi="active" if active == "absensi" else "",
    )
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{active.title()} - SPADA</title>
    <style>{STYLE}</style>
</head>
<body>
    {nav}
    <div class="container">{body}</div>
</body>
</html>"""


def _group_schedule_by_day(schedule: dict) -> list[dict]:
    """Group schedule dict into sorted day lists."""
    day_order = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    grouped = defaultdict(list)
    for name, info in schedule.items():
        day = info.get("day", "")
        grouped[day].append({"name": name, **info})
    return [{"day": d, "courses": grouped[d]} for d in day_order if d in grouped]


def _schedule_html(schedule_days: list[dict], compact: bool = False) -> str:
    """Render schedule as HTML."""
    if not schedule_days:
        return '<div class="empty">Belum ada jadwal. Gunakan /setjadwal di bot Telegram.</div>'
    parts = []
    for dg in schedule_days:
        parts.append(f'<div class="day-group"><h3>{dg["day"]}</h3>')
        for c in dg["courses"]:
            if compact:
                parts.append(f'''<div class="course-item">
                    <div><strong>{c["name"]}</strong><div style="font-size:12px;color:var(--text2)">{c.get("room", "")}</div></div>
                    <div class="time">{c.get("start", "")} – {c.get("end", "")}</div>
                </div>''')
            else:
                parts.append(f'''<div class="course-card">
                    <div class="info"><h3>{c["name"]}</h3><div class="meta">{c.get("dosen", "")}</div></div>
                    <div class="time"><div class="hours">{c.get("start", "")} – {c.get("end", "")}</div><div class="room">{c.get("room", "")}</div></div>
                </div>''')
        parts.append('</div>')
    return "\n".join(parts)


# ── API endpoints (JSON) ──────────────────────────────────────

@app.get("/api/status")
async def api_status():
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
    }


@app.get("/api/schedule")
async def api_schedule():
    return get_course_schedule()


@app.get("/api/assignments")
async def api_assignments(status: Optional[str] = None):
    if status == "pending":
        return get_pending_assignments()
    elif status == "submitted":
        return get_submitted_assignments()
    return get_all_assignments()


@app.get("/api/grades")
async def api_grades():
    return get_grades_snapshot()


@app.get("/api/submissions")
async def api_submissions():
    return get_submission_history()


@app.get("/api/stats")
async def api_stats():
    return get_stats()


# ── HTML pages ─────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    schedule = get_course_schedule()
    grouped = _group_schedule_by_day(schedule)
    assignments = get_all_assignments()[:10]
    grades = get_grades_snapshot()
    stats = get_stats()

    if not has_credentials():
        body = '<div class="section"><div class="empty">Belum login. Login melalui bot Telegram dulu ya.</div></div>'
        return _page("dashboard", body)

    # Stats cards
    stats_html = f'''<div class="grid">
        <div class="stat-card"><div class="label">Semester</div><div class="value accent">{get_current_semester() or "—"}</div></div>
        <div class="stat-card"><div class="label">Mata Kuliah</div><div class="value">{len(schedule)}</div></div>
        <div class="stat-card"><div class="label">Tugas Pending</div><div class="value yellow">{stats["pending"]}</div></div>
        <div class="stat-card"><div class="label">Tugas Selesai</div><div class="value green">{stats["submitted"]}</div></div>
    </div>'''

    # Schedule section
    sched_html = f'<div class="section"><h2>Jadwal</h2>{_schedule_html(grouped, compact=True)}</div>'

    # Assignments table
    if assignments:
        rows = "".join(
            f'<tr><td>{a["course"]}</td><td>{a["title"][:40]}{"..." if len(a["title"]) > 40 else ""}</td>'
            f'<td>{a["due_date"][:10] if a.get("due_date") else "—"}</td>'
            f'<td><span class="badge {a["status"]}">{"Selesai" if a["status"] == "submitted" else "Pending"}</span></td></tr>'
            for a in assignments
        )
        assign_html = f'<div class="section"><h2>Tugas</h2><table><thead><tr><th>Mata Kuliah</th><th>Judul</th><th>Deadline</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>'
    else:
        assign_html = '<div class="section"><h2>Tugas</h2><div class="empty">Belum ada tugas terdeteksi.</div></div>'

    # Grades table
    if grades:
        rows = "".join(f'<tr><td>{g["course"]}</td><td><strong>{g.get("grade") or "—"}</strong></td></tr>' for g in grades)
        grade_html = f'<div class="section"><h2>Nilai</h2><table><thead><tr><th>Mata Kuliah</th><th>Nilai</th></tr></thead><tbody>{rows}</tbody></table></div>'
    else:
        grade_html = '<div class="section"><h2>Nilai</h2><div class="empty">Belum ada nilai terdeteksi.</div></div>'

    return _page("dashboard", stats_html + sched_html + assign_html + grade_html)


@app.get("/jadwal", response_class=HTMLResponse)
async def page_jadwal():
    schedule = get_course_schedule()
    grouped = _group_schedule_by_day(schedule)
    return _page("jadwal", f'<h2 style="margin-bottom:16px;color:var(--text2)">Jadwal Kuliah</h2>{_schedule_html(grouped)}')


@app.get("/tugas", response_class=HTMLResponse)
async def page_tugas():
    assignments = get_all_assignments()
    if not assignments:
        return _page("tugas", '<div class="empty">Belum ada tugas terdeteksi.</div>')
    cards = "".join(
        f'<div class="task-card"><div class="header"><h3>{a["title"]}</h3>'
        f'<span class="badge {a["status"]}">{"Selesai" if a["status"] == "submitted" else "Pending"}</span></div>'
        f'<div class="meta">{a["course"]}{"" if not a.get("due_date") else f" • Deadline: {a['due_date'][:10]}"}</div></div>'
        for a in assignments
    )
    return _page("tugas", cards)


@app.get("/nilai", response_class=HTMLResponse)
async def page_nilai():
    grades = get_grades_snapshot()
    if not grades:
        return _page("nilai", '<div class="empty">Belum ada nilai terdeteksi.</div>')
    rows = "".join(f'<tr><td>{g["course"]}</td><td class="grade">{g.get("grade") or "—"}</td></tr>' for g in grades)
    table = f'<table><thead><tr><th>Mata Kuliah</th><th>Nilai</th></tr></thead><tbody>{rows}</tbody></table>'
    return _page("nilai", table)


@app.get("/absensi", response_class=HTMLResponse)
async def page_absensi():
    tracker = load_tracker()
    submissions = tracker.get("submissions", [])
    if not submissions:
        return _page("absensi", '<div class="empty">Belum ada riwayat absensi/pengumpulan.</div>')
    cards = "".join(
        f'<div class="log-card"><div class="header"><h3>{s["title"]}</h3>'
        f'<span style="color:var(--green);font-size:12px;font-weight:600;">✓ Terkirim</span></div>'
        f'<div class="meta">{s["course"]} • {s.get("submitted_at", "")[:10]}</div></div>'
        for s in reversed(submissions)
    )
    return _page("absensi", cards)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8088, reload=False)
