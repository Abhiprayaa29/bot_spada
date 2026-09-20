"""BIMA Scraper — lightweight requests-based client with cookie auth.

No DrissionPage, no reCAPTCHA solving.
User logs into BIMA manually in browser, exports cookies, sends to bot.
"""

import os
import re
import json
import time
import logging
import requests
from typing import Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BIMA_BASE_URL = "https://bima.upnyk.ac.id"
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "data", "bima_cookies.json")
GRADES_FILE = os.path.join(os.path.dirname(__file__), "data", "bima_grades.json")
SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "data", "bima_schedule.json")

JADWAL_URLS = [
    f"{BIMA_BASE_URL}/akademik/jadwal",
    f"{BIMA_BASE_URL}/akademik/jadwal-kuliah",
    f"{BIMA_BASE_URL}/mahasiswa/jadwal",
    f"{BIMA_BASE_URL}/jadwal",
]

NILAI_URLS = [
    f"{BIMA_BASE_URL}/akademik/nilai",
    f"{BIMA_BASE_URL}/akademik/nilai-mahasiswa",
    f"{BIMA_BASE_URL}/mahasiswa/nilai",
    f"{BIMA_BASE_URL}/nilai",
]


def _ensure_dir():
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)


class BimaClient:
    """Simple requests-based BIMA client. Cookie auth only."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        })
        self.session.verify = False

    # ── Cookie management ──────────────────────────────────────

    def load_cookies(self) -> bool:
        """Load cookies from data/bima_cookies.json."""
        if not os.path.exists(COOKIE_FILE):
            return False
        try:
            with open(COOKIE_FILE, "r") as f:
                cookies = json.load(f)
            if not cookies:
                return False
            now = time.time()
            for c in cookies:
                if c.get("expiry") and c["expiry"] < now:
                    logger.info("BIMA cookies expired")
                    return False
            for c in cookies:
                name = c.get("name", "")
                value = c.get("value", "")
                domain = c.get("domain", ".upnyk.ac.id")
                path = c.get("path", "/")
                self.session.cookies.set(name, value, domain=domain, path=path)
            logger.info(f"Loaded {len(cookies)} BIMA cookies")
            return True
        except Exception as e:
            logger.error(f"Failed to load BIMA cookies: {e}")
            return False

    def save_cookies(self):
        """Persist current session cookies to disk."""
        try:
            _ensure_dir()
            cookies = []
            for c in self.session.cookies:
                entry = {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                }
                if hasattr(c, "expires") and c.expires:
                    entry["expiry"] = c.expires
                cookies.append(entry)
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f, indent=2)
            logger.info(f"Saved {len(cookies)} BIMA cookies")
        except Exception as e:
            logger.error(f"Failed to save BIMA cookies: {e}")

    def import_cookies_netscape(self, text: str) -> int:
        """Import cookies from Netscape/browser cookie export format."""
        count = 0
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path, secure, expiry, name, value = parts[:7]
            self.session.cookies.set(
                name, value,
                domain=domain.lstrip("."),
                path=path,
            )
            count += 1
        if count > 0:
            self.save_cookies()
        logger.info(f"Imported {count} cookies from Netscape format")
        return count

    def import_cookies_json(self, text: str) -> int:
        """Import cookies from JSON array (browser devtools export)."""
        try:
            cookies = json.loads(text)
        except json.JSONDecodeError:
            return 0
        if not isinstance(cookies, list):
            return 0
        count = 0
        for c in cookies:
            name = c.get("name", "")
            value = c.get("value", "")
            domain = c.get("domain", ".upnyk.ac.id")
            path = c.get("path", "/")
            if name:
                self.session.cookies.set(name, value, domain=domain.lstrip("."), path=path)
                count += 1
        if count > 0:
            self.save_cookies()
        logger.info(f"Imported {count} cookies from JSON format")
        return count

    def import_cookies_string(self, text: str) -> int:
        """Import cookies from 'name=value; name2=value2' format (from browser console)."""
        text = text.strip()
        if text.startswith("[") or text.startswith("{"):
            return self.import_cookies_json(text)
        if "\t" in text and len(text.split("\t")) >= 7:
            return self.import_cookies_netscape(text)
        # Parse "key=value; key2=value2" format
        count = 0
        for pair in text.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name = name.strip()
            value = value.strip()
            if name:
                self.session.cookies.set(name, value, domain=".upnyk.ac.id", path="/")
                count += 1
        if count > 0:
            self.save_cookies()
        logger.info(f"Imported {count} cookies from string format")
        return count

    # ── Auth check ─────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """Check if current cookies provide a valid BIMA session."""
        try:
            resp = self.session.get(BIMA_BASE_URL, timeout=10, allow_redirects=True)
            if "/login" in resp.url:
                return False
            text = resp.text.lower()
            if "logout" in text or "keluar" in text or "dashboard" in text:
                return True
            if "mahasiswa" in text or "akademik" in text:
                return True
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"BIMA auth check failed: {e}")
            return False

    def get_dashboard_name(self) -> str:
        """Try to extract student name from BIMA dashboard."""
        try:
            resp = self.session.get(BIMA_BASE_URL, timeout=10, allow_redirects=True)
            if "/login" in resp.url:
                return ""
            soup = BeautifulSoup(resp.text, "html.parser")
            # Look for name in common elements
            for sel in [".user-name", ".nama", "h4", "h3", "strong", ".profile-name"]:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(strip=True)
                    if len(text) > 3 and any(c.isalpha() for c in text):
                        return text
            return ""
        except Exception:
            return ""

    # ── Jadwal (Schedule) ──────────────────────────────────────

    def get_jadwal(self) -> list:
        """Scrape class schedule from BIMA."""
        if not self.load_cookies():
            logger.warning("No BIMA cookies loaded")
            return []

        for url in JADWAL_URLS:
            try:
                resp = self.session.get(url, timeout=15, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                if "/login" in resp.url:
                    logger.warning("BIMA session expired during jadwal fetch")
                    return []
                schedule = self._parse_jadwal_html(resp.text)
                if schedule:
                    self._save_schedule(schedule)
                    logger.info(f"Got {len(schedule)} schedule entries from {url}")
                    return schedule
            except Exception as e:
                logger.debug(f"Jadwal fetch failed from {url}: {e}")
                continue
        logger.warning("Could not fetch jadwal from any BIMA URL")
        return []

    def _parse_jadwal_html(self, html: str) -> list:
        """Parse schedule from BIMA HTML page."""
        soup = BeautifulSoup(html, "html.parser")
        entries = []

        # Strategy 1: table rows
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 3:
                    continue
                texts = [c.get_text(strip=True) for c in cells]
                entry = self._classify_jadwal_row(texts)
                if entry:
                    entries.append(entry)

        # Strategy 2: card-based layout
        if not entries:
            for card in soup.find_all(class_=re.compile(r"card|item|row|jadwal")):
                text = card.get_text(" ", strip=True)
                entry = self._parse_jadwal_text(text)
                if entry:
                    entries.append(entry)

        # Strategy 3: structured data in specific elements
        if not entries:
            for item in soup.find_all(class_=re.compile(r"jadwal|schedule|matkul|course")):
                text = item.get_text(" ", strip=True)
                entry = self._parse_jadwal_text(text)
                if entry:
                    entries.append(entry)

        return entries

    def _classify_jadwal_row(self, cells):
        """Classify a table row as a schedule entry."""
        cells = [c for c in cells if c]
        if len(cells) < 2:
            return None

        day_pattern = re.compile(
            r"(senin|selasa|rabu|kamis|jumat|jum'at|sabtu|minggu)", re.IGNORECASE
        )
        time_pattern = re.compile(r"\d{1,2}[:.]\d{2}")

        has_day = any(day_pattern.search(c) for c in cells)
        has_time = any(time_pattern.search(c) for c in cells)

        if not has_day and not has_time:
            return None

        entry = {"name": "", "code": "", "day": "", "time": "", "room": "", "sks": "", "lecturer": ""}

        for cell in cells:
            dm = day_pattern.search(cell)
            if dm:
                entry["day"] = dm.group(1).capitalize()
            tm = time_pattern.search(cell)
            if tm and not entry["time"]:
                entry["time"] = cell
            if re.match(r"^\d{1,2}$", cell):
                entry["sks"] = cell
            if not entry["name"] and len(cell) > 5 and not day_pattern.search(cell):
                entry["name"] = cell
            if re.match(r"^[A-Z]{2,10}\d{3,5}$", cell):
                entry["code"] = cell

        return entry if entry["name"] else None

    def _parse_jadwal_text(self, text):
        """Try to parse schedule from free text."""
        day_pattern = re.compile(
            r"(senin|selasa|rabu|kamis|jumat|jum'at|sabtu|minggu)", re.IGNORECASE
        )
        time_pattern = re.compile(r"(\d{1,2}[:.]\d{2})\s*[-\u2013]\s*(\d{1,2}[:.]\d{2})")

        dm = day_pattern.search(text)
        tm = time_pattern.search(text)
        if not dm:
            return None

        entry = {
            "name": "",
            "code": "",
            "day": dm.group(1).capitalize(),
            "time": tm.group(0) if tm else "",
            "room": "",
            "sks": "",
            "lecturer": "",
        }
        remainder = text[:dm.start()] + text[dm.end():]
        if tm:
            remainder = remainder[:tm.start()] + remainder[tm.end():]
        remainder = re.sub(r"\s+", " ", remainder).strip("- ")
        entry["name"] = remainder[:100] if remainder else ""

        return entry if entry["name"] else None

    def _save_schedule(self, schedule):
        """Save jadwal to data/bima_schedule.json."""
        try:
            _ensure_dir()
            with open(SCHEDULE_FILE, "w") as f:
                json.dump(schedule, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save schedule: {e}")

    # ── Nilai (Grades) ─────────────────────────────────────────

    def get_nilai(self) -> list:
        """Scrape grades from BIMA."""
        if not self.load_cookies():
            logger.warning("No BIMA cookies loaded")
            return []

        for url in NILAI_URLS:
            try:
                resp = self.session.get(url, timeout=15, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                if "/login" in resp.url:
                    logger.warning("BIMA session expired during nilai fetch")
                    return []
                grades = self._parse_nilai_html(resp.text)
                if grades:
                    self._save_grades(grades)
                    logger.info(f"Got {len(grades)} grade entries from {url}")
                    return grades
            except Exception as e:
                logger.debug(f"Nilai fetch failed from {url}: {e}")
                continue
        logger.warning("Could not fetch nilai from any BIMA URL")
        return []

    def _parse_nilai_html(self, html: str) -> list:
        """Parse grades from BIMA HTML page."""
        soup = BeautifulSoup(html, "html.parser")
        entries = []

        # Strategy 1: table rows
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                texts = [c.get_text(strip=True) for c in cells]
                if len(texts) < 2:
                    continue
                entry = self._classify_nilai_row(texts)
                if entry:
                    entries.append(entry)

        # Strategy 2: card-based
        if not entries:
            for card in soup.find_all(class_=re.compile(r"card|item|row|nilai|grade")):
                text = card.get_text(" ", strip=True)
                entry = self._parse_nilai_text(text)
                if entry:
                    entries.append(entry)

        return entries

    def _classify_nilai_row(self, cells):
        """Classify a table row as a grade entry."""
        cells = [c for c in cells if c]
        if len(cells) < 2:
            return None

        grade_pattern = re.compile(r"^[A-Da-d][+-]?$|^E$|^[-]$")
        score_pattern = re.compile(r"^\d{1,3}([.,]\d+)?$")

        entry = {"name": "", "code": "", "credit": "", "grade": "", "score": ""}

        for cell in cells:
            if grade_pattern.match(cell):
                entry["grade"] = cell.upper()
            elif score_pattern.match(cell) and not entry["score"]:
                entry["score"] = cell.replace(",", ".")
            elif re.match(r"^[A-Z]{2,10}\d{3,5}$", cell):
                entry["code"] = cell
            elif re.match(r"^\d{1,2}$", cell) and not entry["credit"]:
                entry["credit"] = cell
            elif not entry["name"] and len(cell) > 3:
                entry["name"] = cell

        return entry if entry["name"] else None

    def _parse_nilai_text(self, text):
        """Try to parse a grade entry from free text."""
        grade_pattern = re.compile(r"\b([A-Da-d][+-]?|E)\b")
        score_pattern = re.compile(r"(\d{1,3}([.,]\d+)?)")
        grades_found = grade_pattern.findall(text)
        scores_found = score_pattern.findall(text)

        if not grades_found and not scores_found:
            return None

        entry = {"name": "", "code": "", "credit": "", "grade": "", "score": ""}
        code_match = re.search(r"[A-Z]{2,10}\d{3,5}", text)
        if code_match:
            entry["code"] = code_match.group()

        if grades_found:
            entry["grade"] = grades_found[0].upper()
        if scores_found:
            entry["score"] = scores_found[0][0].replace(",", ".")

        # Extract course name: remove codes, grades, scores
        remainder = text
        if code_match:
            remainder = remainder[:code_match.start()] + remainder[code_match.end():]
        for g in grades_found:
            remainder = re.sub(r"\b" + re.escape(g) + r"\b", "", remainder)
        for s in scores_found:
            remainder = remainder.replace(s[0], "")
        remainder = re.sub(r"\s+", " ", remainder).strip("- ")
        entry["name"] = remainder[:100] if remainder else ""

        return entry if entry["name"] else None

    def _save_grades(self, grades):
        """Save nilai to data/bima_grades.json."""
        try:
            _ensure_dir()
            with open(GRADES_FILE, "w") as f:
                json.dump(grades, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save grades: {e}")


# ── Convenience functions ──────────────────────────────────────

def load_bima_jadwal() -> list:
    """Load cached jadwal from data/bima_schedule.json."""
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def load_bima_nilai() -> list:
    """Load cached nilai from data/bima_grades.json."""
    if os.path.exists(GRADES_FILE):
        try:
            with open(GRADES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []
