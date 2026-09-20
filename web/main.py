"""FastAPI app — SPADA Bot Dashboard (read-only)."""

import os
import sys
from datetime import datetime

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Ensure root is importable for pairing_store
_root = os.path.dirname(os.path.dirname(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from web.auth import COOKIE_NAME, create_session, verify_session, destroy_session
from web.pairing import (
    create_pairing_token,
    is_token_valid,
    get_token_status,
    generate_qr_base64,
    generate_qr_png,
    cleanup_expired,
)
from web.data_reader import (
    read_session, read_tracker, get_bot_status,
    read_bima_schedule, read_bima_grades, is_bima_connected,
)

app = FastAPI(title="SPADA Bot Dashboard")

# Mount static files
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# ── Helpers ──────────────────────────────────────────────────

WEEKDAYS_ID = {
    "Senin": 0, "Selasa": 1, "Rabu": 2, "Kamis": 3,
    "Jumat": 4, "Sabtu": 5, "Minggu": 6,
}

WEEKDAYS_ORDER = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def _get_chat_id(request: Request) -> int | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return verify_session(token)


def _nav(active: str) -> str:
    links = [
        ("dashboard", "/", "Dashboard"),
        ("jadwal", "/jadwal", "Jadwal"),
        ("tugas", "/tugas", "Tugas"),
        ("nilai", "/nilai", "Nilai"),
        ("absensi", "/absensi", "Absensi"),
    ]
    items = ""
    for key, href, label in links:
        cls = ' class="active"' if key == active else ""
        items += f'<a href="{href}"{cls}>{label}</a>'
    return (
        '<nav class="nav">'
        '<span class="nav-brand">SPADA Bot</span>'
        f'<div class="nav-links">{items}</div>'
        '<a href="/logout" class="nav-logout">Logout</a>'
        '</nav>'
    )


def _page(active: str, body: str, title: str = "") -> str:
    t = title or active.capitalize()
    return (
        '<!DOCTYPE html><html lang="id"><head>'
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<title>{t} - SPADA Bot</title>'
        '<link rel="stylesheet" href="/static/style.css">'
        '</head><body>'
        f'{_nav(active)}'
        f'<div class="container">{body}</div>'
        '</body></html>'
    )


# ── Pairing Routes ───────────────────────────────────────────

@app.get("/pair", response_class=HTMLResponse)
async def pair_page(request: Request):
    chat_id = _get_chat_id(request)
    if chat_id:
        return RedirectResponse("/dashboard", status_code=302)

    cleanup_expired()
    body = (
        '<div class="pair-container">'
        '<div class="pair-card">'
        '<h1>Hubungkan ke Telegram</h1>'
        '<p class="pair-desc">Scan QR code ini dari bot Telegram untuk mengakses dashboard.</p>'
        '<div id="qr-box" class="qr-box"><div class="spinner"></div></div>'
        '<div id="pair-status" class="pair-status">Menunggu scan...</div>'
        '<ol class="pair-steps">'
        '<li>Buka bot Telegram</li>'
        '<li>Kirim foto QR code ini ke bot</li>'
        '<li>Tunggu konfirmasi</li>'
        '</ol>'
        '</div></div>'
        '<script>'
        'let token = null; let pollTimer = null;'
        'async function startPair() {'
        '  const r = await fetch("/pair/start", {method:"POST"});'
        '  const d = await r.json();'
        '  token = d.token;'
        '  document.getElementById("qr-box").innerHTML ='
        '    \'<img src="/pair/qr/\' + token + \'" alt="QR Code" width="256" height="256">\';'
        '  pollTimer = setInterval(pollStatus, 2000);'
        '}'
        'async function pollStatus() {'
        '  if (!token) return;'
        '  const r = await fetch("/pair/status/" + token);'
        '  const d = await r.json();'
        '  if (d.status === "linked") {'
        '    clearInterval(pollTimer);'
        '    document.getElementById("pair-status").textContent = "Berhasil! Redirecting...";'
        '    document.getElementById("pair-status").className = "pair-status success";'
        '    window.location.href = "/pair/complete/" + token;'
        '  } else if (d.status === "expired") {'
        '    clearInterval(pollTimer);'
        '    document.getElementById("pair-status").textContent = "QR expired. Memuat baru...";'
        '    document.getElementById("pair-status").className = "pair-status error";'
        '    setTimeout(startPair, 1500);'
        '  }'
        '}'
        'startPair();'
        '</script>'
    )
    return HTMLResponse(_page("pair", body, "Pairing"))


@app.post("/pair/start")
async def pair_start():
    cleanup_expired()
    token = create_pairing_token()
    qr_b64 = generate_qr_base64(token)
    return {"token": token, "qr_base64": qr_b64}


@app.get("/pair/qr/{token}")
async def pair_qr(token: str):
    png = generate_qr_png(token)
    return Response(content=png, media_type="image/png")


@app.get("/pair/status/{token}")
async def pair_status(token: str):
    status = get_token_status(token)
    if not status:
        return {"status": "not_found"}
    if status["consumed"] or status["status"] == "linked":
        return {"status": "linked", "chat_id": status["chat_id"]}
    if not is_token_valid(token):
        return {"status": "expired"}
    return {"status": "pending"}


@app.get("/pair/complete/{token}")
async def pair_complete(token: str):
    """Create session cookie after QR pairing, then redirect to dashboard."""
    status = get_token_status(token)
    if not status or not (status["consumed"] or status["status"] == "linked"):
        return RedirectResponse("/pair", status_code=302)

    chat_id = status["chat_id"]
    session_token = create_session(chat_id)
    return RedirectResponse(
        "/dashboard",
        status_code=302,
        headers={
            "Set-Cookie": (
                f"{COOKIE_NAME}={session_token}; "
                f"Max-Age={7 * 24 * 3600}; Path=/; HttpOnly; SameSite=Lax"
            )
        },
    )


# ── Dashboard Routes (require session) ───────────────────────

def _require_auth(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        raise HTTPException(status_code=303, headers={"Location": "/pair"})
    return chat_id


def _require_auth_redirect(request: Request) -> int | None:
    """Return chat_id or None if not authenticated (for HTML pages)."""
    return _get_chat_id(request)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if _get_chat_id(request):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/pair", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        return RedirectResponse("/pair", status_code=302)

    status = get_bot_status()
    session = read_session()
    tracker = read_tracker()

    # Stats cards
    courses_count = status["courses_count"]
    pending = status["assignments_pending"]
    submitted = status["assignments_submitted"]
    grades_count = status["grades_count"]
    bima_connected = status["bima_connected"]
    bima_jadwal_count = status["bima_jadwal_count"]
    bima_nilai_count = status["bima_nilai_count"]

    body = f'''
    <h1 class="page-title">Dashboard</h1>
    <div class="grid">
      <div class="stat-card"><div class="label">SPADA</div><div class="value {"green" if status["logged_in"] else "red"}">{"Online" if status["logged_in"] else "Offline"}</div></div>
      <div class="stat-card"><div class="label">BIMA</div><div class="value {"green" if bima_connected else "red"}">{"Connected" if bima_connected else "Offline"}</div></div>
      <div class="stat-card"><div class="label">Semester</div><div class="value accent">{status["semester"] or "-"}</div></div>
      <div class="stat-card"><div class="label">Mata Kuliah</div><div class="value">{courses_count}</div></div>
      <div class="stat-card"><div class="label">Tugas Pending</div><div class="value {"yellow" if pending > 0 else "green"}">{pending}</div></div>
      <div class="stat-card"><div class="label">Sudah Submit</div><div class="value green">{submitted}</div></div>
      <div class="stat-card"><div class="label">Nilai</div><div class="value accent">{grades_count}</div></div>
      <div class="stat-card"><div class="label">BIMA Jadwal</div><div class="value accent">{bima_jadwal_count}</div></div>
    </div>
    '''
    # Today's schedule — merge manual + BIMA
    schedule = session.get("course_schedule", {})
    today_name = datetime.now().strftime("%A")
    today_map = {"Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
                 "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu"}
    today_id = today_map.get(today_name, "")
    today_classes: list[dict] = []
    for name, info in schedule.items():
        if info.get("day") == today_id:
            today_classes.append({"name": name, "time": f"{info.get('start','')}-{info.get('end','')}", "room": info.get("room",""), "source": "Manual"})
    bima_sched = read_bima_schedule()
    for j in bima_sched:
        if j.get("day") == today_id:
            today_classes.append({"name": j.get("name","?"), "time": j.get("time",""), "room": j.get("room",""), "source": "BIMA"})

    body += '<div class="section"><h2>Jadwal Hari Ini</h2>'
    if today_classes:
        body += '<div class="list">'
        for c in sorted(today_classes, key=lambda x: x.get("time", "")):
            src_cls = "tag-green" if c["source"] == "BIMA" else ""
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{c["name"]}</span>
              <span class="item-meta">{c["time"]}</span></div>
              <div class="item-right">
                <span class="item-tag {src_cls}">{c["source"]}</span>
                <span class="item-tag">{c["room"]}</span>
              </div></div>'''
        body += '</div>'
    else:
        body += '<p class="empty">Tidak ada kelas hari ini.</p>'
    body += '</div>'

    # Pending assignments
    assignments = tracker.get("assignments", [])
    pending_list = [a for a in assignments if a.get("status") == "pending"]
    if pending_list:
        body += '<div class="section"><h2>Tugas Pending</h2><div class="list">'
        for a in sorted(pending_list, key=lambda x: x.get("due_date", "9999")):
            due = a.get("due_date", "-")
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{a.get("title","")}</span>
              <span class="item-meta">{a.get("course","")}</span></div>
              <span class="item-tag tag-yellow">{due}</span></div>'''
        body += '</div></div>'

    # Recent grades
    grades = tracker.get("grades", [])
    if grades:
        body += '<div class="section"><h2>Nilai Terkini</h2><div class="list">'
        for g in sorted(grades, key=lambda x: x.get("course", "")):
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{g.get("course","")}</span></div>
              <span class="item-tag tag-green">{g.get("grade","-")}</span></div>'''
        body += '</div></div>'

    return HTMLResponse(_page("dashboard", body, "Dashboard"))


@app.get("/jadwal", response_class=HTMLResponse)
async def jadwal_page(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        return RedirectResponse("/pair", status_code=302)

    session = read_session()
    schedule = session.get("course_schedule", {})
    bima_jadwal = read_bima_schedule()

    body = '<h1 class="page-title">Jadwal Kuliah</h1>'

    has_data = schedule or bima_jadwal
    if not has_data:
        body += '<p class="empty">Belum ada jadwal. Gunakan /setjadwal atau /bima di Telegram.</p>'
        return HTMLResponse(_page("jadwal", body, "Jadwal"))

    # Group by day — merge manual + BIMA
    by_day: dict[str, list] = {}

    # Manual schedule (course_schedule from session.json)
    for name, info in schedule.items():
        day = info.get("day", "Lainnya")
        by_day.setdefault(day, []).append({
            "name": name,
            "time": f"{info.get('start','')}-{info.get('end','')}" if info.get("start") else "",
            "room": info.get("room", ""),
            "dosen": info.get("dosen", ""),
            "source": "Manual",
        })

    # BIMA schedule
    for j in bima_jadwal:
        day = j.get("day", "Lainnya")
        name = j.get("name", "?")
        by_day.setdefault(day, []).append({
            "name": name,
            "time": j.get("time", ""),
            "room": j.get("room", ""),
            "dosen": j.get("dosen", ""),
            "source": "BIMA",
        })

    body += '<div class="sections">'
    for day in WEEKDAYS_ORDER:
        classes = by_day.get(day, [])
        if not classes:
            continue
        body += f'<div class="section"><h2>{day}</h2><div class="list">'
        for c in sorted(classes, key=lambda x: x.get("time", "")):
            src = c["source"]
            src_cls = "tag-green" if src == "BIMA" else ""
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{c["name"]}</span>
              <span class="item-meta">{c["time"]}</span></div>
              <div class="item-right">
                <span class="item-tag {src_cls}">{src}</span>
                <span class="item-tag">{c["room"]}</span>
                <span class="item-meta">{c["dosen"]}</span>
              </div></div>'''
        body += '</div></div>'
    body += '</div>'

    return HTMLResponse(_page("jadwal", body, "Jadwal"))


@app.get("/tugas", response_class=HTMLResponse)
async def tugas_page(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        return RedirectResponse("/pair", status_code=302)

    tracker = read_tracker()
    assignments = tracker.get("assignments", [])

    body = '<h1 class="page-title">Tugas</h1>'

    if not assignments:
        body += '<p class="empty">Belum ada tugas terdeteksi. Gunakan /sync di Telegram.</p>'
        return HTMLResponse(_page("tugas", body, "Tugas"))

    # Pending first (sorted by due_date), then submitted
    pending = sorted([a for a in assignments if a.get("status") == "pending"],
                     key=lambda x: x.get("due_date", "9999"))
    submitted = sorted([a for a in assignments if a.get("status") == "submitted"],
                       key=lambda x: x.get("submitted_at", ""), reverse=True)

    if pending:
        body += '<div class="section"><h2>Belum Submit</h2><div class="list">'
        for a in pending:
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{a.get("title","")}</span>
              <span class="item-meta">{a.get("course","")}</span></div>
              <span class="item-tag tag-yellow">{a.get("due_date","-")}</span></div>'''
        body += '</div></div>'

    if submitted:
        body += '<div class="section"><h2>Sudah Submit</h2><div class="list">'
        for a in submitted:
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{a.get("title","")}</span>
              <span class="item-meta">{a.get("course","")}</span></div>
              <span class="item-tag tag-green">{a.get("submitted_at","-")[:10]}</span></div>'''
        body += '</div></div>'

    return HTMLResponse(_page("tugas", body, "Tugas"))


@app.get("/nilai", response_class=HTMLResponse)
async def nilai_page(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        return RedirectResponse("/pair", status_code=302)

    tracker = read_tracker()
    grades = tracker.get("grades", [])
    bima_nilai = read_bima_grades()

    body = '<h1 class="page-title">Nilai</h1>'

    has_data = grades or bima_nilai
    if not has_data:
        body += '<p class="empty">Belum ada nilai tercatat. Gunakan /sync atau /bima di Telegram.</p>'
        return HTMLResponse(_page("nilai", body, "Nilai"))

    # BIMA grades section (primary — more accurate)
    if bima_nilai:
        body += '<div class="section"><h2>📊 Nilai BIMA (Akademik)</h2><div class="list">'
        for g in sorted(bima_nilai, key=lambda x: x.get("name", "")):
            name = g.get("name", "?")
            grade = g.get("grade", "-")
            score = g.get("score", "")
            display = grade if grade and grade != "-" else str(score) if score else "-"
            try:
                gval = float(display)
                if gval >= 80:
                    tag_cls = "tag-green"
                elif gval >= 60:
                    tag_cls = "tag-yellow"
                else:
                    tag_cls = "tag-red"
            except (ValueError, TypeError):
                tag_cls = ""
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{name}</span>
              <span class="item-meta">{g.get("status","")}</span></div>
              <span class="item-tag {tag_cls}">{display}</span></div>'''
        body += '</div></div>'

    # SPADA grades section (supplementary)
    if grades:
        body += '<div class="section"><h2>📝 Nilai SPADA (Tugas)</h2><div class="list">'
        for g in sorted(grades, key=lambda x: x.get("course", "")):
            grade = g.get("grade", "-")
            try:
                gval = float(grade)
                if gval >= 80:
                    tag_cls = "tag-green"
                elif gval >= 60:
                    tag_cls = "tag-yellow"
                else:
                    tag_cls = "tag-red"
            except (ValueError, TypeError):
                tag_cls = ""
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{g.get("course","")}</span>
              <span class="item-meta">Updated: {(g.get("updated_at",""))[:10]}</span></div>
              <span class="item-tag {tag_cls}">{grade}</span></div>'''
        body += '</div></div>'

    return HTMLResponse(_page("nilai", body, "Nilai"))


@app.get("/absensi", response_class=HTMLResponse)
async def absensi_page(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        return RedirectResponse("/pair", status_code=302)

    session = read_session()
    tracker = read_tracker()
    amap = session.get("attendance_map", {})
    daily_log = tracker.get("daily_log", [])

    body = '<h1 class="page-title">Riwayat Absensi</h1>'

    # Attendance map
    if amap:
        body += '<div class="section"><h2>Attendance IDs</h2><div class="list">'
        for course, aid in sorted(amap.items()):
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{course}</span></div>
              <span class="item-tag">{aid}</span></div>'''
        body += '</div></div>'

    # Daily log
    if daily_log:
        body += '<div class="section"><h2>Riwayat Briefing</h2><div class="list">'
        for log in sorted(daily_log, key=lambda x: x.get("date", ""), reverse=True)[:20]:
            body += f'''<div class="list-item">
              <div class="item-main"><span class="item-title">{log.get("date","")}</span>
              <span class="item-meta">{log.get("summary","")[:100]}</span></div></div>'''
        body += '</div></div>'

    if not amap and not daily_log:
        body += '<p class="empty">Belum ada data absensi.</p>'

    return HTMLResponse(_page("absensi", body, "Absensi"))


# ── API Endpoints (JSON) ─────────────────────────────────────

@app.get("/api/status")
async def api_status(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        raise HTTPException(status_code=401)
    return get_bot_status()


@app.get("/api/schedule")
async def api_schedule(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        raise HTTPException(status_code=401)
    return read_session().get("course_schedule", {})


@app.get("/api/assignments")
async def api_assignments(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        raise HTTPException(status_code=401)
    return read_tracker().get("assignments", [])


@app.get("/api/grades")
async def api_grades(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        raise HTTPException(status_code=401)
    return read_tracker().get("grades", [])


@app.get("/api/submissions")
async def api_submissions(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        raise HTTPException(status_code=401)
    return read_tracker().get("submissions", [])


@app.get("/api/daily_log")
async def api_daily_log(request: Request):
    chat_id = _get_chat_id(request)
    if not chat_id:
        raise HTTPException(status_code=401)
    return read_tracker().get("daily_log", [])


# ── Logout ───────────────────────────────────────────────────

@app.post("/logout")
async def logout_post(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        destroy_session(token)
    return RedirectResponse("/pair", status_code=302,
                            headers={"Set-Cookie": f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly"})


@app.get("/logout")
async def logout_get(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        destroy_session(token)
    return RedirectResponse("/pair", status_code=302,
                            headers={"Set-Cookie": f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly"})


# ── Entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.main:app", host="0.0.0.0", port=8088, reload=False)
