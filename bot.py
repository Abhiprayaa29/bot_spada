"""Telegram Bot for SPADA LMS — flexible login, auto-detect semester, reminders, auto-attendance."""

import os
import asyncio
import logging
import tempfile
import time as _time
from datetime import datetime, timedelta, time as dtime
from telegram import Update, Bot, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from config import config
from store import (
    has_credentials, save_credentials, clear_credentials,
    get_current_semester, save_current_semester,
    save_attendance_map, get_attendance_map,
    save_course_schedule, get_course_schedule,
    update_course_schedule_entry,
)
from spada import SpadaScraper
from bima import load_bima_jadwal, load_bima_nilai, load_schedule, save_schedule, parse_schedule_input

# ── Jadwal check helper ─────────────────────────────────────

def _has_jadwal() -> bool:
    """True if user has input jadwal (either via web or manual)."""
    bima = load_schedule()          # data/bima_schedule.json
    manual = get_course_schedule()  # data/session.json → course_schedule
    return bool(bima) or bool(manual)
from tracker import (
    add_assignment, mark_submitted, get_pending_assignments,
    get_submitted_assignments, get_all_assignments, get_submission_history,
    update_grades, get_grades_snapshot, log_daily_summary, get_stats,
    get_assignment_by_index, delete_assignment_by_index, edit_assignment_by_index,
)

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Global scraper (lazily initialised after login) ──────────
_scraper: SpadaScraper | None = None


def _get_scraper() -> SpadaScraper:
    """Return the active scraper, creating one from stored creds if needed.
    If re-login fails, credentials are cleared so the user must /login again."""
    global _scraper
    if _scraper is None or not _scraper.logged_in:
        if has_credentials():
            from store import get_credentials
            u, p = get_credentials()
            _scraper = SpadaScraper(config.SPADA_BASE_URL, u, p)
            if not _scraper.login():
                logger.warning("Stored credentials invalid — clearing")
                clear_credentials()
                _scraper = None
    return _scraper


def _require_login(update: Update) -> bool:
    """Send a nudge if the user is not logged in and return False."""
    if not has_credentials():
        update.message.reply_text(
            "🔐 Kamu belum login!\n"
            "Ketik /login untuk memulai.",
        )
        return False
    return True


# ── Conversation states ─────────────────────────────────────
LOGIN_USERNAME, LOGIN_PASSWORD = range(2)


# ============================================================
# /login  /logout
# ============================================================

async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the login conversation."""
    if has_credentials():
        await update.message.reply_text(
            "✅ Kamu sudah login sebagai *{u}*\n"
            "Ketik /logout dulu jika ingin ganti akun.".format(
                u=get_current_semester() or "—",
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🔐 *Login SPADA*\n\n"
        "Masukkan *username* SPADA kamu:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return LOGIN_USERNAME


async def login_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["login_user"] = update.message.text.strip()
    await update.message.reply_text(
        "Masukkan *password* SPADA kamu:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return LOGIN_PASSWORD


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _scraper
    username = context.user_data.get("login_user", "")
    password = update.message.text.strip()

    await update.message.reply_text("⏳ Validasi kredensial…")

    try:
        scraper = SpadaScraper(config.SPADA_BASE_URL, username, password)
        if not scraper.login():
            await update.message.reply_text(
                "❌ Login gagal! Username atau password salah.\n"
                "Coba lagi dengan /login",
            )
            return ConversationHandler.END

        # ── Save credentials ──────────────────────────────────
        save_credentials(username, password)
        _scraper = scraper

        # ── Auto-detect semester (SPADA) ─────────────────────
        await update.message.reply_text("🔍 Mendeteksi semester aktif…")
        sem = scraper.detect_current_semester()
        if sem:
            save_current_semester(sem)
            await update.message.reply_text(f"🎓 Semester SPADA: *{sem}*", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("⚠️ Tidak bisa mendeteksi semester dari SPADA.")

        # ── Auto-scrape attendance IDs ────────────────────────
        await update.message.reply_text("🔍 Memindai ID presensi…")
        amap = {}
        try:
            amap = scraper.scrape_attendance_ids(semester=sem)
        except Exception as e:
            print(f"[BOT] Attendance scrape failed: {e}")
        if amap:
            save_attendance_map(amap)
            await update.message.reply_text(
                f"✅ Ditemukan {len(amap)} kelas dengan presensi.",
            )
        else:
            await update.message.reply_text("⚠️ Tidak ditemukan presensi otomatis.")

        # ── Get student name ──────────────────────────────────
        student_name = ""
        try:
            student_name = scraper.get_student_name()
        except Exception as e:
            print(f"[BOT] Get student name failed: {e}")

        # ── Summary ───────────────────────────────────────────
        courses = scraper.get_courses(semester=sem)
        greeting_name = student_name if student_name else username
        await update.message.reply_text(
            f"✅ *Login berhasil!*\n\n"
            f"👋 Halo, *{greeting_name}*!\n"
            f"🎓 Semester: {sem}\n"
            f"📚 {len(courses)} mata kuliah\n"
            f"📋 {len(amap)} presensi terdeteksi\n\n"
            f"Ketik /help untuk melihat semua perintah.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error saat login: {e}")

    return ConversationHandler.END


async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Login dibatalkan.")
    return ConversationHandler.END


# ============================================================
# /bima  — show BIMA schedule + grades status (manual input)
# ============================================================

async def cmd_bima(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show BIMA schedule + grades status from local JSON files."""
    jadwal = load_bima_jadwal()
    nilai = load_bima_nilai()

    lines = ["*BIMA Status*\n"]

    if jadwal:
        lines.append(f"Jadwal: *{len(jadwal)}* mata kuliah")
        for j in jadwal:
            name = j.get("name", "?")
            day = j.get("day", "")
            start = j.get("start", "")
            end = j.get("end", "")
            room = j.get("room", "")
            time_str = f"{day} {start}-{end}" if day else "jadwal tersimpan"
            lines.append(f"  - {name} ({time_str} {room})".rstrip())
    else:
        lines.append("Jadwal: *belum diisi*")
        lines.append("Gunakan /setjadwal atau input di web dashboard.")

    lines.append("")

    if nilai:
        lines.append(f"Nilai: *{len(nilai)}* mata kuliah")
    else:
        lines.append("Nilai: *belum diisi*")

    lines.append("")
    lines.append("Input jadwal: /setjadwal [paste jadwal]")
    lines.append("Web dashboard: /jadwal-input (lihat instruksi)")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear stored credentials."""
    global _scraper
    if not has_credentials():
        await update.message.reply_text("Kamu belum login.")
        return

    clear_credentials()
    _scraper = None
    await update.message.reply_text(
        "👋 Logout berhasil!\n"
        "Ketik /login jika ingin masuk lagi.",
    )


# ============================================================
# /start  /help
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sem = get_current_semester()
    logged = has_credentials()
    status = f"🎓 Semester: *{sem}*" if sem else "🔐 Belum login"

    await update.message.reply_text(
        f"Halo {user.first_name}! 👋\n\n"
        f"Aku adalah bot *SPADA Reminder* 🤖\n"
        f"Aku akan membantu kamu:\n"
        f"  📋 Mengingatkan deadline tugas\n"
        f"  ✅ Auto absen (presensi)\n"
        f"  📸 Mengirim bukti screenshot\n\n"
        f"*Status:* {status}\n\n"
        f"*Perintah:*\n"
        f"  /login — Login ke SPADA\n"
        f"  /logout — Logout\n"
        f"  /dashboard — Lihat ringkasan\n"
        f"  /deadlines — Deadline mendatang\n"
        f"  /courses — Daftar mata kuliah\n"
        f"  /absen [nama] — Absen manual\n"
        f"  /tugas — Lihat semua tugas\n"
        f"  /edittugas — Edit tugas\n"
        f"  /hapustugas — Hapus tugas\n"
        f"  /briefing — Ringkasan harian\n"
        f"  /sync — Sync tracker dengan SPADA\n"
        f"  /status — Status bot\n"
        f"  /semester — Info semester aktif\n"
        f"  /setjadwal — Input jadwal manual\n"
        f"  /setsemester — Set kode semester\n"
        f"  /listjadwal — Lihat jadwal tersimpan\n"
        f"  /bima — Lihat jadwal & nilai BIMA\n"
        f"  /help — Bantuan\n\n"
        f"*Auto Features:*\n"
        f"  🌅 Daily briefing jam 7 pagi\n"
        f"  🔄 Reminder otomatis setiap 30 menit\n"
        f"  ⏰ Auto absen {config.ATTENDANCE_WINDOW_MINUTES} menit sebelum kelas berakhir",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sem = get_current_semester() or "—"
    await update.message.reply_text(
        "📖 *Cara Pakai Bot:*\n\n"
        "*Akun:*\n"
        "/login — Login ke SPADA\n"
        "/logout — Logout\n\n"
        "*Informasi:*\n"
        "/dashboard — Ringkasan SPADA\n"
        "/deadlines — Deadline tugas\n"
        "/courses — Semua mata kuliah\n"
        "/courses [semester] — Filter semester\n"
        f"  Contoh: /courses {sem}\n"
        "/tugas — Lihat semua tugas\n"
        "/edittugas — Edit tugas (judul/matkul/deadline/status)\n"
        "/hapustugas — Hapus tugas dari tracker\n"
        "/briefing — Ringkasan harian\n"
        "/sync — Sync tracker\n"
        "/status — Status bot\n"
        "/semester — Info semester aktif\n\n"
        "*Input Manual:*\n"
        "/setjadwal — Input jadwal dari SPADA\n"
        "/setsemester [kode] — Set kode semester\n"
        "  Contoh: /setsemester 20251\n"
        "/listjadwal — Lihat jadwal tersimpan\n\n"
        "*Presensi:*\n"
        "/absen [nama_kelas] — Absen manual\n\n"
        "*Upload via Telegram:*\n"
        "Kirim file + caption nama tugas\n"
        "Bot otomatis upload ke SPADA\n\n"
        "*Auto Features:*\n"
        "• Daily briefing jam 7 pagi\n"
        "• Reminder deadline otomatis\n"
        f"• Auto absen {config.ATTENDANCE_WINDOW_MINUTES} menit sebelum kelas berakhir\n"
        "• Screenshot bukti absen dikirim ke chat\n\n"
        f"*Semester aktif:* {sem}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ============================================================
# /dashboard
# ============================================================

async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return

    await update.message.reply_text("⏳ Loading dashboard…")

    try:
        scraper = _get_scraper()
        now = datetime.now()
        today_name = now.strftime("%A")
        date_str = now.strftime("%d %B %Y")
        sem = get_current_semester()

        courses = scraper.get_courses(semester=sem)
        course_names = {c["name"] for c in courses}

        # Today's classes (only from courses in current semester)
        schedule = get_course_schedule()
        today_classes = []
        for cname, s in schedule.items():
            if s.get("day") == today_name:
                if any(cname.lower() in cn.lower() for cn in course_names):
                    today_classes.append({
                        "course": cname,
                        "start": s.get("start", "-"),
                        "end": s.get("end", "-"),
                    })

        stats = get_stats()
        pending = get_pending_assignments()
        grades = get_grades_snapshot()

        lines = [
            f"📊 *Dashboard SPADA*\n"
            f"📅 {today_name}, {date_str}\n"
            f"🎒 Semester: {sem}\n",
        ]

        if today_classes:
            lines.append("*📚 Jadwal Hari Ini:*")
            for cls in today_classes:
                lines.append(f"  • {cls['course']}")
                lines.append(f"    🕐 {cls['start']} - {cls['end']}")
            lines.append("")
        else:
            lines.append("*📚 Jadwal Hari Ini:* 🎉 Tidak ada kelas\n")

        lines.append(f"*📝 Statistik Tugas:*")
        lines.append(f"  Total: {stats['total']} | ✅ {stats['submitted']} | ⏳ {stats['pending']}")

        if pending:
            lines.append(f"\n*⏳ Tugas Pending ({len(pending)}):*")
            for a in pending[:5]:
                course = a.get("course", "-")[:25]
                title = a.get("title", "-")[:30]
                due = a.get("due_date", "")[:15]
                lines.append(f"  • [{course}] {title}")
                if due:
                    lines.append(f"    Due: {due}")
            if len(pending) > 5:
                lines.append(f"  ... +{len(pending) - 5} lainnya")
        else:
            lines.append(f"\n*📝 Tugas:* ✅ Semua sudah disubmit!")

        if grades:
            lines.append(f"\n*📈 Nilai Terbaru:*")
            for g in grades[:5]:
                grade_val = g.get("grade", "-")
                lines.append(f"  • {g['course'][:30]}: {grade_val}")

        spada_ok = scraper.logged_in
        status_icon = "🟢" if spada_ok else "🔴"
        lines.append(
            f"\n*🤖 Bot Status:* {status_icon} Running\n"
            f"⏰ Auto Absen: {'🟢 ON' if config.AUTO_ATTENDANCE_ENABLED else '🔴 OFF'}\n"
            f"📅 {now.strftime('%H:%M:%S WIB')}"
        )

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ============================================================
# /deadlines
# ============================================================

async def cmd_deadlines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return

    await update.message.reply_text("⏳ Checking deadlines…")

    try:
        scraper = _get_scraper()
        sem = get_current_semester()
        deadlines = scraper.get_deadlines(semester=sem)

        if not deadlines:
            await update.message.reply_text(
                f"✅ Tidak ada deadline mendatang!\n📅 Semester: {sem}",
            )
            return

        label = f"Semester {sem}"
        lines = [f"📋 *Deadline Mendatang ({label}):*\n"]
        for i, d in enumerate(deadlines[:15], 1):
            emoji = "📝" if d.activity_type == "assignment" else "❓" if d.activity_type == "quiz" else "📅"
            lines.append(f"{i}. {emoji} *{d.title}*")
            if d.course and d.course != "Unknown":
                lines.append(f"   📚 {d.course}")
            if d.due_date:
                lines.append(f"   ⏰ {d.due_date}")
            if d.url:
                lines.append(f"   🔗 {d.url}")
            lines.append("")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ============================================================
# /courses
# ============================================================

async def cmd_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return

    await update.message.reply_text("⏳ Loading courses…")

    try:
        scraper = _get_scraper()
        schedule = get_course_schedule()
        sem = get_current_semester()

        if context.args:
            target = context.args[0]
            courses = scraper.get_courses(semester=target)
            lines = [f"📚 *Mata Kuliah Semester {target} ({len(courses)}):*\n"]
            for i, c in enumerate(courses, 1):
                day = schedule.get(c["name"], {}).get("day", "-")
                lines.append(f"{i}. {c['name']}")
                lines.append(f"   📅 {day}")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
            return

        # All semesters grouped
        all_courses = scraper.get_courses()
        semesters: dict[str, list] = {}
        for c in all_courses:
            s = c.get("semester", "Unknown")
            semesters.setdefault(s, []).append(c)

        sorted_sems = sorted(semesters.keys(), reverse=True)

        lines = ["📚 *Mata Kuliah (per Semester):*\n"]
        for s in sorted_sems:
            cls_list = semesters[s]
            marker = " ← *aktif*" if s == sem else ""
            lines.append(f"*Semester {s}{marker}* — {len(cls_list)} mata kuliah:")
            for c in cls_list:
                lines.append(f"  • {c['name']}")
            lines.append("")

        lines.append(f"\n💡 Gunakan `/courses {sem}` untuk detail.")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ============================================================
# /absen
# ============================================================

async def cmd_absen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return

    if not _has_jadwal():
        await update.message.reply_text(
            "📋 *Belum ada jadwal*\n\n"
            "Absensi baru muncul setelah jadwal diinput.\n\n"
            "Cara input jadwal:\n"
            "• /bima lalu /setjadwal [paste jadwal]\n"
            "• Web dashboard → menu Jadwal",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    amap = get_attendance_map()

    if context.args:
        course_name = " ".join(context.args)
    else:
        lines = ["📝 *Cara Pakai:*\n\n"]
        lines.append("/absen [nama_kelas]\n")
        lines.append("*Contoh:*\n")
        lines.append("  /absen Kriptografi\n")
        lines.append("  /absen IoT\n")
        lines.append("\n*Daftar kelas dengan presensi:*\n")
        for name in amap.keys():
            lines.append(f"  • {name}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # Find matching attendance ID
    attendance_id = None
    matched_name = None
    for name, aid in amap.items():
        if name.lower() in course_name.lower() or course_name.lower() in name.lower():
            attendance_id = aid
            matched_name = name
            break

    if not attendance_id:
        await update.message.reply_text(
            f"❌ Tidak ditemukan presensi untuk: {course_name}\n\n"
            f"Gunakan /absen untuk melihat daftar kelas.",
        )
        return

    await update.message.reply_text(f"⏳ Absen {matched_name}…")

    try:
        scraper = _get_scraper()
        result = scraper.submit_attendance(attendance_id)
        if result["success"]:
            await update.message.reply_text(
                f"✅ *Berhasil Absen!*\n\n"
                f"📚 {matched_name}\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
                f"📸 Mengirim bukti screenshot…",
                parse_mode=ParseMode.MARKDOWN,
            )
            if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
                with open(result["screenshot_path"], "rb") as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=f"📸 Bukti absen {matched_name} — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                    )
            else:
                await update.message.reply_text("⚠️ Screenshot tidak tersedia.")
        else:
            await update.message.reply_text(f"❌ Gagal absen: {result['message']}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ============================================================
# /status
# ============================================================

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return

    scraper = _get_scraper()
    now = datetime.now()
    today_name = now.strftime("%A")
    current_time = now.strftime("%H:%M")
    sem = get_current_semester()

    schedule = get_course_schedule()
    current_class = None
    for course_name, s in schedule.items():
        if s.get("day") == today_name:
            start = s.get("start", "")
            end = s.get("end", "")
            if start <= current_time <= end:
                current_class = course_name

    courses = scraper.get_courses(semester=sem) if scraper.logged_in else []

    status = "🟢 Connected" if scraper.logged_in else "🔴 Disconnected"
    amap = get_attendance_map()

    lines = [
        f"*Status Bot:*\n",
        f"🤖 Bot: 🟢 Running",
        f"📚 SPADA: {status}",
        f"🎒 Semester: {sem} ({len(courses)} mata kuliah)",
        f"📋 Presensi terdaftar: {len(amap)}",
        f"⏰ Auto Absen: {'🟢 ON' if config.AUTO_ATTENDANCE_ENABLED else '🔴 OFF'}",
        f"🔄 Check Interval: {config.REMINDER_CHECK_INTERVAL_MINUTES} min",
        f"📅 {now.strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    if current_class:
        lines.append(f"\n🔴 *Kelas sedang berlangsung:* {current_class}")
    else:
        lines.append(f"\n⚪ Tidak ada kelas saat ini")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ============================================================
# /tugas
# ============================================================

async def cmd_tugas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return

    if not _has_jadwal():
        await update.message.reply_text(
            "📋 *Belum ada jadwal*\n\n"
            "Tugas baru muncul setelah jadwal diinput.\n\n"
            "Cara input jadwal:\n"
            "• /bima lalu /setjadwal [paste jadwal]\n"
            "• Web dashboard → menu Jadwal",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text("⏳ Loading assignments & checking status…")

    try:
        scraper = _get_scraper()
        sem = get_current_semester()
        assignments = scraper.get_assignments(semester=sem)

        if not assignments:
            await update.message.reply_text(f"✅ Tidak ada tugas semester {sem}!")
            return

        with_deadline = [a for a in assignments if a.get("has_deadline")]
        no_deadline = [a for a in assignments if not a.get("has_deadline")]

        submitted_count = 0
        pending_count = 0

        label = f"Semester {sem}"
        lines = [f"📝 *Daftar Tugas ({label}):*\n"]

        if with_deadline:
            lines.append(f"*⏰ Ada Deadline ({len(with_deadline)}):*\n")
            for i, a in enumerate(with_deadline[:15], 1):
                try:
                    status_info = scraper.get_assignment_status(a["url"])
                    is_submitted = status_info["submitted"]
                    status_str = status_info["status"]
                    grade = status_info.get("grade", "")
                except Exception:
                    is_submitted = False
                    status_str = "Unknown"
                    grade = ""

                add_assignment(a["course"], a["title"], a["url"], a.get("due_date", ""))
                if is_submitted:
                    mark_submitted(a["url"])
                    submitted_count += 1
                else:
                    pending_count += 1

                emoji = "✅" if is_submitted else "⏳"
                lines.append(f"{i}. {emoji} *{a['title']}*")
                lines.append(f"   📚 {a['course'][:30]}")
                lines.append(f"   📋 {status_str[:30]}")
                if grade:
                    lines.append(f"   📈 {grade}")
                lines.append(f"   ⏰ {a['due_date'][:25]}")
                lines.append("")

        if no_deadline:
            lines.append(f"*📎 Tanpa Deadline ({len(no_deadline)}):*\n")
            for i, a in enumerate(no_deadline[:10], 1):
                try:
                    status_info = scraper.get_assignment_status(a["url"])
                    is_submitted = status_info["submitted"]
                    status_str = status_info["status"]
                    grade = status_info.get("grade", "")
                except Exception:
                    is_submitted = False
                    status_str = "Unknown"
                    grade = ""

                add_assignment(a["course"], a["title"], a["url"], "")
                if is_submitted:
                    mark_submitted(a["url"])
                    submitted_count += 1
                else:
                    pending_count += 1

                emoji = "✅" if is_submitted else "⏳"
                lines.append(f"{i}. {emoji} *{a['title']}*")
                lines.append(f"   📚 {a['course'][:30]}")
                lines.append(f"   📋 {status_str[:30]}")
                if grade:
                    lines.append(f"   📈 {grade}")
                lines.append("")

        lines.append(f"*📊 Ringkasan:* ✅ {submitted_count} submitted | ⏳ {pending_count} pending")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ============================================================
# /hapustugas — delete a tracked assignment
# ============================================================

async def cmd_hapustugas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a task from the local tracker.

    Usage:
      /hapustugas          — list all tasks with numbers
      /hapustugas 3        — delete task #3
    """
    all_a = get_all_assignments()

    if not all_a:
        await update.message.reply_text("📝 Tidak ada tugas yang ter-tracker.")
        return

    # No argument → show numbered list
    if not context.args:
        lines = [f"🗑️ *Hapus Tugas — Pilih nomor:*\n"]
        for i, a in enumerate(all_a, 1):
            emoji = "✅" if a.get("status") == "submitted" else "⏳"
            title = a.get("title", "-")[:40]
            course = a.get("course", "")[:25]
            lines.append(f"{i}. {emoji} {title}")
            lines.append(f"   📚 {course}")
        lines.append(f"\nKetik: /hapustugas [nomor]")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # Argument → delete by index
    try:
        idx = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Nomor tidak valid. Contoh: /hapustugas 3")
        return

    entry = get_assignment_by_index(idx)
    if not entry:
        await update.message.reply_text(
            f"❌ Nomor {idx} tidak ditemukan.\n"
            f"Total tugas: {len(all_a)}\n"
            f"Ketik /hapustugas untuk melihat daftar."
        )
        return

    deleted = delete_assignment_by_index(idx)
    if deleted:
        await update.message.reply_text(
            f"🗑️ *Tugas dihapus:*\n\n"
            f"📝 {deleted.get('title', '-')}\n"
            f"📚 {deleted.get('course', '-')}\n"
            f"📋 Status: {deleted.get('status', '-')}",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text("❌ Gagal menghapus tugas.")


# ============================================================
# /edittugas — edit a tracked assignment
# ============================================================

async def cmd_edittugas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit a task in the local tracker.

    Usage:
      /edittugas                             — list all tasks with numbers
      /edittugas 3                           — show task #3 details
      /edittugas 3 judul=Judul Baru          — edit title
      /edittugas 3 deadline=2026-10-01 23:59 — edit due date
      /edittugas 3 matkul=Kriptografi        — edit course name
      /edittugas 3 status=submitted          — mark as submitted
    """
    all_a = get_all_assignments()

    if not all_a:
        await update.message.reply_text("📝 Tidak ada tugas yang ter-tracker.")
        return

    # No argument → show numbered list
    if not context.args:
        lines = [f"✏️ *Edit Tugas — Pilih nomor:*\n"]
        for i, a in enumerate(all_a, 1):
            emoji = "✅" if a.get("status") == "submitted" else "⏳"
            title = a.get("title", "-")[:40]
            course = a.get("course", "")[:25]
            lines.append(f"{i}. {emoji} {title}")
            lines.append(f"   📚 {course}")
        lines.append(f"\nKetik: /edittugas [nomor] [field=value]")
        lines.append(f"Fields: judul, matkul, deadline, status")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # First arg is index
    try:
        idx = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Nomor tidak valid. Contoh: /edittugas 3 judul=Judul Baru")
        return

    entry = get_assignment_by_index(idx)
    if not entry:
        await update.message.reply_text(
            f"❌ Nomor {idx} tidak ditemukan.\n"
            f"Total tugas: {len(all_a)}\n"
            f"Ketik /edittugas untuk melihat daftar."
        )
        return

    # No field args → show task details
    if len(context.args) < 2:
        status_icon = "✅" if entry.get("status") == "submitted" else "⏳"
        lines = [
            f"✏️ *Edit Tugas #{idx}:*\n",
            f"📝 Judul: {entry.get('title', '-')}",
            f"📚 Matkul: {entry.get('course', '-')}",
            f"📋 Status: {status_icon} {entry.get('status', '-')}",
            f"⏰ Deadline: {entry.get('due_date', '-')}",
            f"🔗 URL: {entry.get('url', '-')[:50]}",
            f"\n*Ubah field:*",
            f"  /edittugas {idx} judul=Judul Baru",
            f"  /edittugas {idx} matkul=Nama Matkul",
            f"  /edittugas {idx} deadline=2026-10-01 23:59",
            f"  /edittugas {idx} status=submitted",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # Parse field=value pairs from remaining args
    raw = " ".join(context.args[1:])
    # Split on known field prefixes
    field_map = {}
    for prefix, key in [
        ("judul=", "title"),
        ("matkul=", "course"),
        ("deadline=", "due_date"),
        ("status=", "status"),
    ]:
        idx_pos = raw.lower().find(prefix)
        if idx_pos >= 0:
            value = raw[idx_pos + len(prefix):].strip()
            # For deadline, take everything until end of string
            if key == "due_date":
                value = value.strip()
            if value:
                field_map[key] = value

    if not field_map:
        await update.message.reply_text(
            "❌ Tidak ada field yang diubah.\n\n"
            "Format: /edittugas [nomor] judul=Judul Baru\n"
            "Fields: judul, matkul, deadline, status"
        )
        return

    # Validate status value
    if "status" in field_map:
        valid_statuses = {"pending", "submitted"}
        if field_map["status"].lower() not in valid_statuses:
            await update.message.reply_text(
                f"❌ Status tidak valid: {field_map['status']}\n"
                f"Valid: pending, submitted"
            )
            return
        field_map["status"] = field_map["status"].lower()

    updated = edit_assignment_by_index(idx, **field_map)
    if updated:
        # Build change description
        changes = []
        if "title" in field_map:
            changes.append(f"📝 Judul: {field_map['title']}")
        if "course" in field_map:
            changes.append(f"📚 Matkul: {field_map['course']}")
        if "due_date" in field_map:
            changes.append(f"⏰ Deadline: {field_map['due_date']}")
        if "status" in field_map:
            emoji = "✅" if field_map["status"] == "submitted" else "⏳"
            changes.append(f"📋 Status: {emoji} {field_map['status']}")

        await update.message.reply_text(
            f"✅ *Tugas #{idx} diperbarui:*\n\n" + "\n".join(changes),
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text("❌ Gagal mengupdate tugas.")


# ============================================================
# /briefing
# ============================================================

async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return
    await send_daily_briefing(context.bot)


async def send_daily_briefing(bot):
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        return

    if not has_credentials():
        return

    now = datetime.now()
    today_name = now.strftime("%A")
    date_str = now.strftime("%d %B %Y")
    sem = get_current_semester()

    lines = [f"🌅 *Selamat Pagi!*\n📅 {today_name}, {date_str}\n🎒 Semester {sem}\n"]

    scraper = _get_scraper()
    if scraper and scraper.logged_in:
        courses = scraper.get_courses(semester=sem)
        course_names = {c["name"] for c in courses}

        schedule = get_course_schedule()
        lines.append("*📚 Jadwal Hari Ini:*")
        today_classes = []
        for cname, s in schedule.items():
            if s.get("day") == today_name:
                if any(bn.lower() in cname.lower() for bn in course_names):
                    today_classes.append(f"  • {cname} ({s['start']} - {s['end']})")

        if today_classes:
            lines.extend(today_classes)
        else:
            lines.append("  🎉 Hari ini tidak ada kelas!")
        lines.append("")

    pending = get_pending_assignments()
    if pending:
        lines.append(f"*📝 Tugas Pending ({len(pending)}):*")
        for a in pending[:8]:
            due = a.get("due_date", "")
            if due:
                lines.append(f"  ⏳ {a['title']} — due {due[:20]}")
            else:
                lines.append(f"  📎 {a['title']} (tanpa deadline)")
        if len(pending) > 8:
            lines.append(f"  ... dan {len(pending) - 8} tugas lainnya")
    else:
        lines.append("*📝 Tugas:* ✅ Semua sudah disubmit!")
    lines.append("")

    stats = get_stats()
    lines.append(
        f"*📊 Statistik:*\n"
        f"  📝 Total: {stats['total']} | "
        f"✅ Submitted: {stats['submitted']} | "
        f"⏳ Pending: {stats['pending']}"
    )
    lines.append("")

    grades = get_grades_snapshot()
    if grades:
        lines.append("*📈 Nilai Terbaru:*")
        for g in grades[:5]:
            lines.append(f"  📚 {g['course']}: {g['grade']}")
    lines.append("")

    lines.append(
        "*⚡ Quick Commands:*\n"
        "  /tugas — Lihat semua tugas\n"
        "  /absen [nama] — Absen manual\n"
        "  /courses — Lihat mata kuliah\n"
        "  /dashboard — Lihat dashboard"
    )

    text = "\n".join(lines)
    log_daily_summary(text)

    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Daily briefing error: {e}")


# ============================================================
# /sync
# ============================================================

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return

    await update.message.reply_text("🔄 Syncing with SPADA…")

    try:
        scraper = _get_scraper()
        sem = get_current_semester()
        assignments = scraper.get_assignments(semester=sem)
        count = 0
        submitted = 0
        pending = 0

        for a in assignments:
            add_assignment(a["course"], a["title"], a["url"], a.get("due_date", ""))
            count += 1

            try:
                status_info = scraper.get_assignment_status(a["url"])
                if status_info["submitted"]:
                    mark_submitted(a["url"])
                    submitted += 1
                else:
                    pending += 1
            except Exception:
                pending += 1

        grades = scraper.get_grades()
        new_grades = update_grades(grades)

        stats = get_stats()
        lines = [
            f"✅ *Sync Complete!*\n",
            f"🎒 Semester: {sem}",
            f"📝 Total tracked: {count} assignments",
            f"📊 Grades: {stats['grades_count']}",
            f"✅ Submitted: {submitted}",
            f"⏳ Pending: {pending}",
        ]

        if new_grades:
            lines.append(f"\n🆕 *Nilai Baru:*")
            for g in new_grades:
                lines.append(f"  📚 {g['course']}: {g['grade']}")

        # Also sync BIMA schedule from local JSON
        bima_jadwal = load_bima_jadwal()
        if bima_jadwal:
            lines.append(f"\nBIMA Jadwal: *{len(bima_jadwal)}* mata kuliah")
            # Merge BIMA jadwal into course_schedule
            schedule = get_course_schedule()
            for j in bima_jadwal:
                name = j.get("name", "")
                if name and name not in schedule:
                    schedule[name] = {
                        "day": j.get("day", ""),
                        "start": j.get("start", ""),
                        "end": j.get("end", ""),
                        "room": j.get("room", ""),
                        "code": j.get("code", ""),
                        "kelas": j.get("kelas", ""),
                        "sks": j.get("sks", ""),
                        "dosen": j.get("dosen", ""),
                    }
            save_course_schedule(schedule)

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ============================================================
# /setjadwal — bulk manual schedule paste from SPADA
# ============================================================

# Conversation states for /setjadwal
SETJADWAL_WAITING = 0


def _parse_schedule_input(text: str) -> list[dict]:
    """Parse tab-separated schedule blocks pasted from SPADA's kehadiran page.

    Handles the actual SPADA format where each course block is:

        Kurikulum[tab]Kode[tab]Nama[tab]Kelas[tab]SKS[tab]
        Hari HH:MM - HH:MM Ruang
        Dosen name(s)
        Kehadiran_number

    Header words (Kurikulum, Kode Mata Kuliah, etc.) are skipped.
    The schedule line is the NEXT non-blank line after the tab row.

    Returns list of dicts with keys: code, name, kelas, sks, day, start, end, room, dosen.
    """
    import re

    courses = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    # Regex for schedule line: "Hari HH:MM - HH:MM Ruang"
    sched_re = re.compile(
        r"(Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu)"
        r"\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*(.*)",
        re.IGNORECASE,
    )

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # Skip blank lines
        if not line.strip():
            i += 1
            continue

        # Only process lines that contain tabs (course data rows)
        if "\t" not in line:
            i += 1
            continue

        parts = [p.strip() for p in line.split("\t")]
        parts = [p for p in parts if p]

        # Need at least 5 fields: Kurikulum, Kode, Nama, Kelas, SKS
        if len(parts) < 5:
            i += 1
            continue

        # SPADA format: Kurikulum | Kode | Nama | Kelas | SKS [| Jadwal_inline]
        # parts[0]=kurikulum (IF21), parts[1]=kode (120210032), etc.
        kurikulum = parts[0]
        code = parts[1]
        name = parts[2]
        kelas = parts[3]
        sks_str = parts[4]
        sks = int(sks_str) if sks_str.isdigit() else 0

        # Check if schedule is inline (parts[5]) or on the next line
        day = ""
        start_time = ""
        end_time = ""
        room = ""

        jadwal_text = parts[5] if len(parts) > 5 else ""
        m = sched_re.match(jadwal_text)

        if not m and jadwal_text:
            # parts[5] exists but isn't a schedule — might be noise, skip
            pass

        if not m:
            # Schedule is on the next non-blank line
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                if not next_line:
                    i += 1
                    continue
                m = sched_re.match(next_line)
                if m:
                    i += 1  # consume the schedule line
                break

        if m:
            day = m.group(1).capitalize()
            start_time = m.group(2)
            end_time = m.group(3)
            room = m.group(4).strip()
            i += 1 if not (jadwal_text and sched_re.match(jadwal_text)) else 0
        else:
            i += 1

        # Collect dosen lines (non-blank, non-tab lines until kehadiran number)
        dosen_parts = []
        while i < len(lines):
            next_line = lines[i].rstrip()
            stripped = next_line.strip()

            # Stop at next tab-separated course line
            if "\t" in next_line:
                break
            # Stop at kehadiran number (just a digit)
            if stripped.isdigit():
                i += 1
                break
            # Stop at schedule-like line (shouldn't appear here, but safety)
            if sched_re.match(stripped):
                break
            # Skip blank lines between dosen and kehadiran
            if not stripped:
                # Check if the line after blank is a number (kehadiran) or next course
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines):
                    ahead = lines[j].strip()
                    if ahead.isdigit() or "\t" in lines[j]:
                        break
                i += 1
                continue
            dosen_parts.append(stripped)
            i += 1

        dosen = ", ".join(dosen_parts) if dosen_parts else ""

        courses.append({
            "code": code,
            "name": name,
            "kelas": kelas,
            "sks": sks,
            "day": day,
            "start": start_time,
            "end": end_time,
            "room": room,
            "dosen": dosen,
        })

    return courses


async def cmd_setjadwal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bulk schedule input: /setjadwal then paste the tab-separated blocks.

    Accepts multi-line paste from SPADA's kehadiran page. Each course block
    is separated by blank lines. Fields are tab-separated:
        Kode  Nama  Kelas  SKS  Hari Start - End Ruang
        Dosen
        Kehadiran
    """
    if len(context.args) == 0:
        # Set flag so next message is treated as schedule paste
        context.user_data["awaiting_schedule_paste"] = True
        # Show help / usage
        await update.message.reply_text(
            "📅 *Input Jadwal Manual (Bulk)*\n\n"
            "Cara pakai:\n"
            "1. Buka halaman Kehadiran di SPADA\n"
            "2. Copy semua jadwal (tabs included)\n"
            "3. Kirim ke bot dengan format:\n\n"
            "<code>/setjadwal</code>\n"
            "<i>lalu paste jadwal di baris berikutnya</i>\n\n"
            "Atau langsung:\n"
            "<code>/setjadwal [paste jadwal di sini]</code>\n\n"
            "*Contoh format yang diterima:*\n"
            "<code>120210032\tKapita Selekta\tIF-A\t2\t"
            "Sabtu 07:30 - 09:15 Patt.I-3A</code>\n"
            "<code>Awang Hendrianto P.</code>\n"
            "<code>0</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Join all args as the raw text (user may paste inline)
    raw_text = " ".join(context.args)
    courses = _parse_schedule_input(raw_text)

    if not courses:
        await update.message.reply_text(
            "❌ Tidak bisa parse jadwal.\n\n"
            "Pastikan format sudah benar (copy langsung dari SPADA).",
        )
        return

    # Save all parsed courses
    schedule = get_course_schedule()
    for c in courses:
        schedule[c["name"]] = {
            "day": c["day"],
            "start": c["start"],
            "end": c["end"],
            "room": c["room"],
            "code": c["code"],
            "kelas": c["kelas"],
            "sks": c["sks"],
            "dosen": c["dosen"],
        }
    save_course_schedule(schedule)

    # Build summary
    lines = [f"✅ *{len(courses)} jadwal berhasil disimpan!*\n"]
    for c in courses:
        lines.append(
            f"📚 *{c['name']}* ({c['kelas']})\n"
            f"   📅 {c['day']} {c['start']} - {c['end']} "
            f"({c['room']})\n"
            f"   👨‍🏫 {c['dosen']}\n"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_setjadwal_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle multi-line paste: user sends /setjadwal then the paste in next message."""
    # This is invoked when user sends just /setjadwal (no args)
    # We store state and wait for next message
    context.user_data["awaiting_schedule_paste"] = True
    await update.message.reply_text(
        "📅 *Kirim jadwal SPADA sekarang!*\n\n"
        "Paste hasil copy dari halaman Kehadiran SPADA.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_schedule_paste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the pasted schedule text after /setjadwal."""
    if not context.user_data.get("awaiting_schedule_paste"):
        return False  # Not expecting schedule paste

    context.user_data["awaiting_schedule_paste"] = False
    raw_text = update.message.text

    courses = _parse_schedule_input(raw_text)

    if not courses:
        await update.message.reply_text(
            "❌ Tidak bisa parse jadwal.\n\n"
            "Coba paste ulang dari SPADA, atau gunakan /setjadwal [jadwal].",
        )
        return True

    schedule = get_course_schedule()
    for c in courses:
        schedule[c["name"]] = {
            "day": c["day"],
            "start": c["start"],
            "end": c["end"],
            "room": c["room"],
            "code": c["code"],
            "kelas": c["kelas"],
            "sks": c["sks"],
            "dosen": c["dosen"],
        }
    save_course_schedule(schedule)

    lines = [f"✅ *{len(courses)} jadwal berhasil disimpan!*\n"]
    for c in courses:
        lines.append(
            f"📚 *{c['name']}* ({c['kelas']})\n"
            f"   📅 {c['day']} {c['start']} - {c['end']} "
            f"({c['room']})\n"
            f"   👨‍🏫 {c['dosen']}\n"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
    )
    return True


# ============================================================
# /semester — detect semester from SPADA
# ============================================================

async def cmd_semester(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show or refresh semester info from SPADA."""
    if not _require_login(update):
        return

    sem_spada = get_current_semester()

    if context.args and context.args[0] == "refresh":
        # Force refresh from SPADA
        await update.message.reply_text("🔄 Mendeteksi semester dari SPADA…")
        try:
            scraper = _get_scraper()
            sem = scraper.get_semester()
            if sem:
                save_current_semester(sem)
                await update.message.reply_text(
                    f"✅ *Semester SPADA:* {sem}\n\n"
                    f"🎒 Semester aktif: {sem}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await update.message.reply_text(
                    "⚠️ Tidak bisa mendeteksi semester dari SPADA.\n"
                    "Pastikan kredensial benar.",
                )
        except Exception as e:
            await update.message.reply_text(f"❌ Error SPADA: {e}")
        return

    # Show current info
    lines = [
        "*🎓 Info Semester:*\n",
        f"📚 SPADA: {sem_spada or '(belum terdeteksi)'}",
        "",
        "💡 Ketik `/semester refresh` untuk update dari SPADA.",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ============================================================
# /setsemester — set semester manually
# ============================================================

async def cmd_setsemester(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set semester manually: /setsemester 20251"""
    if not context.args:
        await update.message.reply_text(
            "🎓 *Cara Pakai:*\n\n"
            "/setsemester [kode_semester]\n\n"
            "*Contoh:*\n"
            "  /setsemester 20251\n\n"
            f"Semester saat ini: *{get_current_semester() or '(kosong)'}*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    sem = context.args[0].strip()
    save_current_semester(sem)
    await update.message.reply_text(
        f"✅ Semester disimpan: *{sem}*",
        parse_mode=ParseMode.MARKDOWN,
    )


# ============================================================
# /listjadwal — view all saved schedule entries
# ============================================================

async def cmd_listjadwal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all saved schedule entries."""
    schedule = get_course_schedule()
    if not schedule:
        await update.message.reply_text(
            "📅 Belum ada jadwal tersimpan.\n\n"
            "Ketik /setjadwal untuk input jadwal.",
        )
        return

    sem = get_current_semester() or "—"
    lines = [f"📅 *Daftar Jadwal* (Semester: {sem})\n"]

    for name, s in schedule.items():
        day = s.get("day", "?")
        start = s.get("start", "?")
        end = s.get("end", "?")
        room = s.get("room", "")
        kelas = s.get("kelas", "")
        dosen = s.get("dosen", "")
        sks = s.get("sks", "")
        code = s.get("code", "")

        lines.append(f"*{name}* ({kelas})")
        if code:
            lines.append(f"  📋 Kode: {code}")
        lines.append(f"  📅 {day} {start} - {end}")
        if room:
            lines.append(f"  📍 {room}")
        if sks:
            lines.append(f"  🎓 SKS: {sks}")
        if dosen:
            lines.append(f"  👨‍🏫 {dosen}")
        lines.append("")

    lines.append(f"Total: *{len(schedule)}* mata kuliah")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ============================================================
# Photo handler — QR pairing for web dashboard
# ============================================================

# Rate limiting: max 5 photos per minute per chat
_photo_rate: dict[int, list[float]] = {}
_PHOTO_RATE_LIMIT = 5
_PHOTO_RATE_WINDOW = 60.0


def _check_photo_rate(chat_id: int) -> bool:
    """Return True if within rate limit, False if too many photos."""
    now = _time.time()
    timestamps = _photo_rate.get(chat_id, [])
    timestamps = [t for t in timestamps if now - t < _PHOTO_RATE_WINDOW]
    if len(timestamps) >= _PHOTO_RATE_LIMIT:
        _photo_rate[chat_id] = timestamps
        return False
    timestamps.append(now)
    _photo_rate[chat_id] = timestamps
    return True


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages — decode QR code for web dashboard pairing."""
    chat_id = update.effective_chat.id

    # Rate limit
    if not _check_photo_rate(chat_id):
        await update.message.reply_text(
            "Terlalu banyak foto. Coba lagi dalam semenit."
        )
        return

    # Lazy import cv2 (heavy)
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("opencv not installed — QR pairing unavailable")
        return

    from pairing_store import consume_token, is_token_valid, get_token_status

    # Download the photo (use highest resolution)
    photo = update.message.photo[-1]
    temp_path = None
    try:
        file = await context.bot.get_file(photo.file_id)
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, "qr_photo.jpg")
        await file.download_to_drive(temp_path)

        # Decode QR
        img = cv2.imread(temp_path)
        if img is None:
            await update.message.reply_text("Gagal membaca foto. Kirim ulang.")
            return

        detector = cv2.QRCodeDetector()
        data, _, _ = detector.detectAndDecode(img)

        if not data:
            await update.message.reply_text(
                "Tidak ada QR code terdeteksi di foto.\n"
                "Kirim screenshot QR code dari halaman pairing web."
            )
            return

        token = data.strip()
        status = get_token_status(token)

        if status is None or not is_token_valid(token):
            await update.message.reply_text(
                "QR tidak dikenali atau sudah kedaluwarsa.\n"
                "Buka halaman pairing web untuk generate QR baru."
            )
            return

        if consume_token(token, chat_id):
            await update.message.reply_text(
                "Dashboard berhasil terhubung!\n"
                "Buka halaman web untuk melihat dashboard."
            )
        else:
            await update.message.reply_text(
                "QR tidak valid atau sudah dipakai.\n"
                "Buka halaman pairing web untuk generate QR baru."
            )

    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        await update.message.reply_text(
            "Gagal memproses foto. Kirim ulang screenshot QR code."
        )
    finally:
        # Cleanup temp file
        if temp_path and os.path.exists(temp_path):
            try:
                import shutil
                shutil.rmtree(os.path.dirname(temp_path), ignore_errors=True)
            except Exception:
                pass


# ============================================================
# Document upload handler
# ============================================================

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_login(update):
        return

    doc = update.message.document
    if not doc:
        return

    caption = update.message.caption or ""
    if not caption:
        await update.message.reply_text(
            "📎 *Upload ke SPADA*\n\n"
            "Kirim file dengan caption berisi nama tugas.\n"
            "Contoh: kirim file PDF dengan caption `Tugas Kriptografi`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(f"🔍 Mencari tugas: {caption}…")

    try:
        scraper = _get_scraper()
        sem = get_current_semester()
        assignments = scraper.get_assignments(semester=sem)
        matching = None

        for a in assignments:
            if caption.lower() in a["title"].lower():
                matching = a
                break

        if not matching:
            await update.message.reply_text(
                f"❌ Tidak ditemukan tugas: {caption}\n\nKirim /tugas untuk melihat daftar tugas.",
            )
            return

        file = await context.bot.get_file(doc.file_id)
        temp_dir = tempfile.mkdtemp()
        local_path = os.path.join(temp_dir, doc.file_name or "upload.pdf")
        await file.download_to_drive(local_path)

        await update.message.reply_text(
            f"📤 Uploading to: *{matching['title']}*\n"
            f"📚 {matching['course']}\n"
            f"📄 {doc.file_name} ({doc.file_size // 1024}KB)",
            parse_mode=ParseMode.MARKDOWN,
        )

        result = scraper.upload_file_to_assignment(matching["url"], local_path, doc.file_name)

        if result["success"]:
            mark_submitted(matching["url"], result.get("screenshot_path", ""))
            await update.message.reply_text(
                f"✅ *File Berhasil Diupload!*\n\n"
                f"📝 {matching['title']}\n"
                f"📚 {matching['course']}\n"
                f"📄 {doc.file_name}\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
                f"📸 Mengirim bukti screenshot…",
                parse_mode=ParseMode.MARKDOWN,
            )
            if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
                with open(result["screenshot_path"], "rb") as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=f"📸 Bukti upload: {matching['title']} — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                    )
        else:
            await update.message.reply_text(f"❌ Gagal upload: {result['message']}")

        try:
            os.remove(local_path)
            os.rmdir(temp_dir)
        except Exception:
            pass

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ============================================================
# Auto Reminder & Attendance
# ============================================================

async def auto_reminder(app: Application):
    if not has_credentials():
        return
    try:
        scraper = _get_scraper()
        sem = get_current_semester()
        deadlines = scraper.get_deadlines(semester=sem)
        for d in deadlines:
            if d.due_datetime:
                now = datetime.now()
                diff = d.due_datetime - now
                if timedelta(hours=23, minutes=50) < diff < timedelta(hours=24, minutes=10):
                    await _send_reminder(app, d, "⏰ *24 jam lagi!*")
                elif timedelta(minutes=55) < diff < timedelta(hours=1, minutes=5):
                    await _send_reminder(app, d, "⚠️ *1 jam lagi!*")
                elif timedelta(minutes=10) < diff < timedelta(minutes=20):
                    await _send_reminder(app, d, "🚨 *15 menit lagi!*")
    except Exception as e:
        logger.error(f"Reminder error: {e}")


async def _send_reminder(app: Application, deadline, urgency: str):
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        return
    text = (
        f"{urgency}\n\n"
        f"📝 *{deadline.title}*\n"
        f"📚 {deadline.course}\n"
        f"⏰ Deadline: {deadline.due_date}\n"
    )
    if deadline.url:
        text += f"🔗 {deadline.url}\n"
    try:
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Send reminder error: {e}")


async def auto_attendance(app: Application):
    if not config.AUTO_ATTENDANCE_ENABLED:
        return
    if not has_credentials():
        return

    now = datetime.now()
    current_day = now.strftime("%A")
    current_time = now.strftime("%H:%M")

    schedule = get_course_schedule()
    amap = get_attendance_map()

    for course_name, s in schedule.items():
        if s.get("day") != current_day:
            continue
        end_time = s.get("end", "")
        if not end_time:
            continue
        try:
            end_h, end_m = map(int, end_time.split(":"))
            end_dt = now.replace(hour=end_h, minute=end_m, second=0)
            check_dt = end_dt - timedelta(minutes=config.ATTENDANCE_WINDOW_MINUTES)
            if check_dt <= now <= end_dt:
                attendance_id = amap.get(course_name)
                if attendance_id:
                    logger.info(f"Auto-attending: {course_name}")
                    try:
                        scraper = _get_scraper()
                        result = scraper.submit_attendance(attendance_id)
                        if result["success"]:
                            chat_id = config.TELEGRAM_CHAT_ID
                            if chat_id:
                                await app.bot.send_message(
                                    chat_id=chat_id,
                                    text=(
                                        f"✅ *Auto Absen Berhasil!*\n\n"
                                        f"📚 {course_name}\n"
                                        f"⏰ {now.strftime('%H:%M:%S')}\n"
                                        f"📸 Mengirim bukti screenshot…"
                                    ),
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                                if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
                                    with open(result["screenshot_path"], "rb") as photo:
                                        await app.bot.send_photo(
                                            chat_id=chat_id,
                                            photo=InputFile(photo),
                                            caption=f"📸 Bukti absen {course_name} — {now.strftime('%d/%m/%Y %H:%M')}",
                                        )
                    except Exception as e:
                        logger.error(f"Auto-attendance error for {course_name}: {e}")
        except Exception as e:
            logger.error(f"Schedule parse error: {e}")


# ============================================================
# Main
# ============================================================

def main():
    if not config.TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set!")
        return

    # Try to restore session from stored credentials
    if has_credentials():
        print("🔐 Restoring SPADA session from stored credentials…")
        global _scraper
        from store import get_credentials
        u, p = get_credentials()
        _scraper = SpadaScraper(config.SPADA_BASE_URL, u, p)
        if _scraper.login():
            print("✅ SPADA session restored!")
            sem = get_current_semester()
            courses = _scraper.get_courses(semester=sem) if sem else []
            print(f"🎒 Semester {sem}: {len(courses)} mata kuliah aktif")
        else:
            print("⚠️ Stored credentials expired — user must /login again.")
            clear_credentials()
            _scraper = None
    else:
        print("ℹ️ No stored credentials — waiting for /login.")

    # Create bot application
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # ── Login conversation ────────────────────────────────────
    login_handler = ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={
            LOGIN_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_username),
            ],
            LOGIN_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_password),
            ],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )
    app.add_handler(login_handler)

    # ── Command handlers ──────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("deadlines", cmd_deadlines))
    app.add_handler(CommandHandler("courses", cmd_courses))
    app.add_handler(CommandHandler("absen", cmd_absen))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("tugas", cmd_tugas))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("setjadwal", cmd_setjadwal))
    app.add_handler(CommandHandler("setsemester", cmd_setsemester))
    app.add_handler(CommandHandler("listjadwal", cmd_listjadwal))
    app.add_handler(CommandHandler("semester", cmd_semester))
    app.add_handler(CommandHandler("bima", cmd_bima))
    app.add_handler(CommandHandler("edittugas", cmd_edittugas))
    app.add_handler(CommandHandler("hapustugas", cmd_hapustugas))

    # Handle schedule paste after /setjadwal (must be before document handler)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_schedule_paste,
    ))

    # Handle document uploads
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Handle photo uploads — QR pairing for web dashboard
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # ── Job queue for auto tasks ──────────────────────────────
    job_queue = app.job_queue

    job_queue.run_repeating(
        auto_reminder,
        interval=config.REMINDER_CHECK_INTERVAL_MINUTES * 60,
        first=10,
    )

    job_queue.run_repeating(
        auto_attendance,
        interval=5 * 60,
        first=30,
    )

    job_queue.run_daily(
        lambda ctx: asyncio.create_task(send_daily_briefing(ctx.bot)),
        time=dtime(hour=0, minute=0),  # 00:00 UTC = 07:00 WIB
    )

    print("🤖 Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
