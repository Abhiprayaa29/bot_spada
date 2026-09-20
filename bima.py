"""BIMA schedule + grades — manual input parsing + JSON storage.

No browser, no scraping. User pastes jadwal text, this module parses and saves it.
"""

import os
import re
import json
import logging

logger = logging.getLogger(__name__)

SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "data", "bima_schedule.json")
GRADES_FILE = os.path.join(os.path.dirname(__file__), "data", "bima_grades.json")


# ── Schedule parser (tab-separated blocks from SPADA/BIMA) ───

def parse_schedule_input(text: str) -> list[dict]:
    """Parse tab-separated schedule blocks pasted from BIMA/SPADA.

    Expected format per course (multi-line block):

        IF21\\t120210032\\tKapita Selekta\\tIF-A\\t2\\t
        Sabtu 07:30 - 09:15 Patt.I-3A
        (blank)
        Awang Hendrianto P. Dr. S.T., M.T.
        (blank)
        0

    Praktikum / courses with no schedule:

        120210059\\t120210059\\tPraktikum Pemrograman IoT dan Komputasi Awan\\tIF-A\\t2\\t
        (blank)
        (blank)
        (blank)
        0

    Returns list of dicts with keys:
        code, name, kelas, sks, day, start, end, room, dosen, kurikulum
    """
    courses = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].rstrip()
        stripped = line.strip()

        # Skip blank lines and non-data header lines (no tabs)
        if not stripped or "\t" not in stripped:
            i += 1
            continue

        # ── Parse the tab-separated header line ──
        parts = [p.strip() for p in line.split("\t")]
        # Strip trailing empty fields (e.g. trailing tab on Praktikum lines)
        while parts and not parts[-1]:
            parts.pop()

        # Need at least: code, name, kelas, sks
        # Some lines have 5 fields: kurikulum, code, name, kelas, sks
        # Some have 4: code, name, kelas, sks (kurikulum = same as code)
        if len(parts) < 4:
            i += 1
            continue

        if len(parts) >= 5 or (len(parts) >= 4 and not parts[0].isdigit()):
            # 5-field format: kurikulum, code, name, kelas, sks
            kurikulum = parts[0]
            code = parts[1]
            name = parts[2]
            kelas = parts[3]
            sks_str = parts[4] if len(parts) >= 5 else ""
        else:
            code = parts[0]
            name = parts[1]
            kelas = parts[2]
            sks_str = parts[3]
            kurikulum = code

        sks = int(sks_str) if sks_str.isdigit() else 0

        # ── Scan ahead for schedule, dosen, kehadiran ──
        day = ""
        start_time = ""
        end_time = ""
        room = ""
        dosen_lines = []
        kehadiran = ""

        j = i + 1
        found_schedule = False
        blanks_after_schedule = 0
        dosen_done = False

        while j < n:
            ahead = lines[j].rstrip()
            ahead_s = ahead.strip()

            # Stop if we hit another tab-separated header line (next course)
            # Schedule lines have at most 1 tab (e.g. "Sabtu 07:30 - 09:15\tPatt.I-3A"),
            # header lines have 3+ tabs, so only break on 2+ tabs.
            if ahead.count("\t") >= 2:
                break

            # ── Check for schedule line ──
            sched_match = re.match(
                r"(Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu)"
                r"\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*(.*)",
                ahead_s,
                re.IGNORECASE,
            )

            if sched_match and not found_schedule:
                day = sched_match.group(1).capitalize()
                start_time = sched_match.group(2)
                end_time = sched_match.group(3)
                room = sched_match.group(4).strip()
                found_schedule = True
                j += 1
                continue

            # ── Check for kehadiran (just a digit) ──
            if ahead_s.isdigit() and found_schedule:
                kehadiran = ahead_s
                j += 1
                break

            # If we haven't found schedule yet and this is blank,
            # skip blank lines between header and schedule/dosen
            if not ahead_s:
                j += 1
                continue

            # ── Dosen line (non-blank, non-schedule, non-digit) ──
            # Skip "Kurikulum: ..." lines
            if ahead_s.startswith("Kurikulum:"):
                j += 1
                continue

            if found_schedule and not ahead_s.isdigit():
                # After schedule found, non-blank non-digit = dosen
                dosen_lines.append(ahead_s)
            elif not found_schedule and not sched_match:
                # Before schedule, could be dosen if no schedule for this course
                # Check if next few lines are all blank (praktikum with no schedule)
                dosen_lines.append(ahead_s)

            j += 1

        dosen = ", ".join(dosen_lines) if dosen_lines else ""

        courses.append({
            "kurikulum": kurikulum,
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

        i = j

    return courses


# ── Storage functions ───────────────────────────────────────

def save_schedule(courses: list[dict]) -> None:
    """Save parsed courses to data/bima_schedule.json."""
    os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(courses, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(courses)} schedule entries")


def load_schedule() -> list[dict]:
    """Load schedule from data/bima_schedule.json."""
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_grades(grades: list[dict]) -> None:
    """Save grades to data/bima_grades.json."""
    os.makedirs(os.path.dirname(GRADES_FILE), exist_ok=True)
    with open(GRADES_FILE, "w") as f:
        json.dump(grades, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(grades)} grade entries")


def load_grades() -> list[dict]:
    """Load grades from data/bima_grades.json."""
    if os.path.exists(GRADES_FILE):
        try:
            with open(GRADES_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


# ── Aliases for backward compatibility ──────────────────────

def load_bima_jadwal() -> list[dict]:
    return load_schedule()


def load_bima_nilai() -> list[dict]:
    return load_grades()
