"""Telegram Bot for SPADA LMS — flexible login, auto-detect semester, reminders, auto-attendance."""

import os
import asyncio
import logging
import tempfile
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
from tracker import (
    add_assignment, mark_submitted, get_pending_assignments,
    get_submitted_assignments, get_all_assignments, get_submission_history,
    update_grades, get_grades_snapshot, log_daily_summary, get_stats,
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


# ── Conversation states for /login ───────────────────────────
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
        amap = scraper.scrape_attendance_ids(semester=sem)
        if amap:
            save_attendance_map(amap)
            await update.message.reply_text(
                f"✅ Ditemukan {len(amap)} kelas dengan presensi.",
            )
        else:
            await update.message.reply_text("⚠️ Tidak ditemukan presensi otomatis.")

        # ── Get student name ──────────────────────────────────
        student_name = scraper.get_student_name()

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
        f"  /briefing — Ringkasan harian\n"
        f"  /sync — Sync tracker dengan SPADA\n"
        f"  /status — Status bot\n"
        f"  /semester — Info semester aktif\n"
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
        "/briefing — Ringkasan harian\n"
        "/sync — Sync tracker\n"
        "/status — Status bot\n"
        "/semester — Info semester aktif\n\n"
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

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ============================================================
# /setjadwal — manual schedule entry
# ============================================================

async def cmd_setjadwal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set schedule for a course: /setjadwal Kriptografi Selasa 15:00 16:30"""
    if not _require_login(update):
        return

    if len(context.args) < 4:
        await update.message.reply_text(
            "📅 *Cara Pakai:*\n\n"
            "/setjadwal [nama_kelas] [hari] [jam_mulai] [jam_selesai]\n\n"
            "*Contoh:*\n"
            "  /setjadwal Kriptografi Selasa 15:00 16:30\n"
            "  /setjadwal IoT Rabu 15:00 16:30",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Parse args: last 3 are day, start, end; everything before is course name
    end_time = context.args[-1]
    start_time = context.args[-2]
    day = context.args[-3]
    course_name = " ".join(context.args[:-3])

    valid_days = {"Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
    if day.capitalize() not in {d.capitalize() for d in valid_days}:
        await update.message.reply_text(f"❌ Hari tidak valid: {day}\nContoh: Senin, Selasa, Rabu, dll.")
        return

    update_course_schedule_entry(course_name, day.capitalize(), start_time, end_time)
    await update.message.reply_text(
        f"✅ Jadwal disimpan!\n\n"
        f"📚 {course_name}\n"
        f"📅 {day} {start_time} - {end_time}",
    )


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
    app.add_handler(CommandHandler("semester", cmd_semester))

    # Handle document uploads
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

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
