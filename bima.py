"""BIMA Scraper — Playwright-based login with reCAPTCHA v2 bypass.

Handles https://bima.upnyk.ac.id/login which has:
  - Laravel CSRF token
  - reCAPTCHA v2 (sitekey: 6LekiE0sAAAAABv_pEjSv8h_B6WNnz8BTlqe7AYZ)
  - Same credentials as SPADA

Strategy:
  1. Launch Playwright chromium (headless, stealth)
  2. Load login page, extract CSRF token
  3. Fill credentials, click reCAPTCHA checkbox
  4. Wait for reCAPTCHA to resolve (no challenge for clean sessions)
  5. Submit form, save cookies for reuse
  6. Scrape semester info from dashboard

If reCAPTCHA shows an image challenge, we retry with stealth tweaks.
If it consistently blocks, the user needs a CAPTCHA solving service.
"""

import os
import json
import time
import logging
from typing import Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

BIMA_BASE_URL = "https://bima.upnyk.ac.id"
BIMA_LOGIN_URL = f"{BIMA_BASE_URL}/login"
RECAPTCHA_SITEKEY = "6LekiE0sAAAAABv_pEjSv8h_B6WNnz8BTlqe7AYZ"
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "data", "bima_cookies.json")


class BimaScraper:
    """Playwright-based BIMA scraper with reCAPTCHA v2 handling."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.logged_in = False
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def _ensure_dir(self):
        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)

    def _save_cookies(self):
        """Persist cookies for session reuse."""
        try:
            self._ensure_dir()
            cookies = self._context.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f, indent=2)
            logger.info("BIMA cookies saved")
        except Exception as e:
            logger.error(f"Failed to save BIMA cookies: {e}")

    def _load_cookies(self) -> bool:
        """Load previously saved cookies."""
        if not os.path.exists(COOKIE_FILE):
            return False
        try:
            with open(COOKIE_FILE, "r") as f:
                cookies = json.load(f)
            self._context.add_cookies(cookies)
            logger.info("Loaded BIMA cookies from disk")
            return True
        except Exception as e:
            logger.warning(f"Failed to load BIMA cookies: {e}")
            return False

    def _start_browser(self):
        """Launch Playwright chromium with stealth settings."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="id-ID",
        )
        # Remove webdriver property to avoid detection
        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['id-ID', 'id', 'en-US', 'en'],
            });
        """)
        self._page = self._context.new_page()

    def _stop_browser(self):
        """Clean up browser resources."""
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def _solve_recaptcha(self, page: Page) -> bool:
        """Attempt to click the reCAPTCHA checkbox.

        reCAPTCHA v2 uses an iframe. We need to:
        1. Find the reCAPTCHA iframe
        2. Click the checkbox inside it
        3. Wait for it to resolve (no challenge)

        Returns True if solved, False if challenge appeared.
        """
        try:
            # Wait for reCAPTCHA iframe to load
            recaptcha_frame = page.frame_locator("iframe[src*='recaptcha']")
            checkbox = recaptcha_frame.locator("#recaptcha-anchor")

            # Wait for checkbox to be visible
            checkbox.wait_for(state="visible", timeout=10000)

            # Click the checkbox
            checkbox.click()
            logger.info("Clicked reCAPTCHA checkbox")

            # Wait a bit for reCAPTCHA to process
            time.sleep(3)

            # Check if reCAPTCHA was solved (the checkbox should be checked)
            # After solving, the anchor class changes to include 'recaptcha-checkbox-checked'
            try:
                page.frame_locator("iframe[src*='recaptcha']").locator(
                    ".recaptcha-checkbox-checked"
                ).wait_for(state="visible", timeout=5000)
                logger.info("reCAPTCHA solved (checkbox checked)")
                return True
            except Exception:
                # Check if challenge iframe appeared
                challenge = page.frame_locator("iframe[src*='recaptcha/api2/bframe']")
                try:
                    challenge.locator(".rc-imageselect-desc-no-canonical").wait_for(
                        state="visible", timeout=3000
                    )
                    logger.warning("reCAPTCHA challenge appeared — cannot auto-solve")
                    return False
                except Exception:
                    # No challenge either — might still be solved
                    logger.info("No challenge detected, proceeding")
                    return True

        except Exception as e:
            logger.error(f"reCAPTCHA interaction failed: {e}")
            return False

    def login(self) -> bool:
        """Login to BIMA with reCAPTCHA v2 handling."""
        try:
            self._start_browser()

            # Try loading saved cookies first
            if self._load_cookies():
                self._page.goto(BIMA_BASE_URL)
                time.sleep(2)
                if "/login" not in self._page.url:
                    logger.info("BIMA session restored from cookies")
                    self.logged_in = True
                    return True
                logger.info("BIMA cookies expired, logging in again")

            # Load login page
            self._page.goto(BIMA_LOGIN_URL, wait_until="networkidle")
            time.sleep(1)

            # Extract CSRF token
            csrf_token = self._page.locator('input[name="_token"]').input_value()
            logger.info(f"Got CSRF token: {csrf_token[:10]}...")

            # Fill credentials
            self._page.fill('input[name="username"]', self.username)
            self._page.fill('input[name="password"]', self.password)
            logger.info("Filled credentials")

            # Solve reCAPTCHA
            recaptcha_solved = self._solve_recaptcha(self._page)
            if not recaptcha_solved:
                logger.warning("reCAPTCHA not solved, trying to submit anyway")
                # Sometimes it works even without visual confirmation

            # Click submit button
            self._page.click('button[type="submit"], input[type="submit"], .btn-primary')
            logger.info("Clicked submit")

            # Wait for navigation
            self._page.wait_for_load_state("networkidle")
            time.sleep(2)

            # Check if login succeeded
            if "/login" in self._page.url:
                logger.error("Login failed — still on login page")
                # Check for error messages
                try:
                    error = self._page.locator(".alert-danger, .error, .invalid-feedback").text_content()
                    logger.error(f"Error message: {error}")
                except Exception:
                    pass
                return False

            logger.info("BIMA login successful!")
            self.logged_in = True
            self._save_cookies()
            return True

        except Exception as e:
            logger.error(f"BIMA login error: {e}")
            return False
        finally:
            self._stop_browser()

    def get_semester_info(self) -> Optional[dict]:
        """Scrape semester info from BIMA dashboard.

        Returns dict with:
          - semester_name: e.g. "2024/2025 Gasal"
          - semester_id: e.g. "20241"
          - raw_text: full text for debugging
        """
        page = self._ensure_page()
        if page is None:
            return None

        try:
            raw_text = page.text_content("body") or ""
            result = {}

            # Pattern 1: semester dropdown
            try:
                semester_select = page.locator('select[name*="semester"], select[name*="periode"], #semester')
                if semester_select.count() > 0:
                    selected = semester_select.input_value()
                    result["semester_id"] = selected
                    selected_text = page.locator(
                        f'select option[value="{selected}"]'
                    ).text_content()
                    result["semester_name"] = selected_text.strip()
                    options = semester_select.locator("option").all()
                    result["available_semesters"] = [
                        {"value": opt.get_attribute("value"), "text": opt.text_content().strip()}
                        for opt in options
                    ]
                    logger.info(f"Found semester: {result['semester_name']} (id={selected})")
            except Exception as e:
                logger.debug(f"No semester dropdown found: {e}")

            # Pattern 2: semester text in page
            if not result.get("semester_name"):
                import re
                patterns = [
                    r"(?:Semester|Periode)\s*[:\s]*([\d]{4}[\s/\-]?[\d]?[\s]*(?:Ganjil|Genap|Gasal|Gelombang\s*\d))",
                    r"(?:Semester|Periode)\s*[:\s]*([\d]{4}[0-9])",
                    r"((?:20\d{2}|21\d{2})[/\-]?\d{0,2}\s*(?:Ganjil|Genap|Gasal))",
                    r"(Semester\s+\w+)",
                ]
                for pat in patterns:
                    m = re.search(pat, raw_text, re.IGNORECASE)
                    if m:
                        result["semester_name"] = m.group(1).strip()
                        logger.info(f"Found semester in text: {result['semester_name']}")
                        break

            # Pattern 3: active period element
            if not result.get("semester_name"):
                try:
                    period_el = page.locator(".period, .semester, .periode, [class*='semester'], [class*='periode']")
                    if period_el.count() > 0:
                        result["semester_name"] = period_el.first.text_content().strip()
                        logger.info(f"Found period element: {result['semester_name']}")
                except Exception:
                    pass

            if result.get("semester_name") or result.get("semester_id"):
                result["raw_text"] = raw_text[:2000]
                return result

            logger.warning("Could not find semester info on BIMA")
            logger.debug(f"Page text preview: {raw_text[:500]}")
            return {"semester_name": "", "semester_id": "", "raw_text": raw_text[:2000]}

        except Exception as e:
            logger.error(f"BIMA semester scrape error: {e}")
            return None

    def get_courses(self) -> list[dict]:
        """Scrape enrolled courses from BIMA dashboard for the active semester.

        Returns list of dicts with: name, id (if available).
        These are the courses the student is currently taking — the source
        of truth for which semester's material to display.
        """
        page = self._ensure_page()
        if page is None:
            return []

        try:
            raw_text = page.text_content("body") or ""
            courses = []

            # Strategy 1: look for course cards/links on dashboard
            # Common BIMA patterns: course cards with name, or table rows
            course_selectors = [
                "a[href*='course']",
                ".card a, .panel a",
                "table a[href*='matkul']",
                ".list-group-item a",
                "a[href*='matakuliah']",
                "[class*='course'] a",
            ]
            seen = set()
            for sel in course_selectors:
                try:
                    links = page.locator(sel).all()
                    for link in links:
                        text = link.text_content().strip()
                        href = link.get_attribute("href") or ""
                        if text and len(text) > 3 and text not in seen:
                            seen.add(text)
                            # Try to extract course ID from href
                            import re
                            cid_match = re.search(r'id[=/](\d+)', href)
                            courses.append({
                                "name": text,
                                "id": cid_match.group(1) if cid_match else "",
                            })
                except Exception:
                    continue

            # Strategy 2: parse course names from raw text
            if not courses:
                import re
                # Look for patterns like "IFxxxx - Course Name" or numbered courses
                for line in raw_text.split('\n'):
                    line = line.strip()
                    if len(line) > 5 and any(kw in line.lower() for kw in ['praktikum', 'kuliah', 'matakuliah', 'matkul']):
                        if line not in seen:
                            seen.add(line)
                            courses.append({"name": line, "id": ""})

            logger.info(f"Found {len(courses)} courses on BIMA dashboard")
            return courses

        except Exception as e:
            logger.error(f"BIMA courses scrape error: {e}")
            return []

    def _ensure_page(self) -> Optional["Page"]:
        """Ensure we have a live BIMA page. Handles cookie restore and re-login.
        Returns the page object, or None on failure."""
        if not self.logged_in:
            if not self.login():
                return None

        try:
            self._start_browser()

            # Load saved cookies
            if self._load_cookies():
                self._page.goto(BIMA_BASE_URL, wait_until="networkidle")
                time.sleep(2)
                if "/login" in self._page.url:
                    self._stop_browser()
                    self.logged_in = False
                    if not self.login():
                        return None
                    self._start_browser()
                    if self._load_cookies():
                        self._page.goto(BIMA_BASE_URL, wait_until="networkidle")
                        time.sleep(2)
            else:
                self._stop_browser()
                self.logged_in = False
                if not self.login():
                    return None
                self._start_browser()
                if self._load_cookies():
                    self._page.goto(BIMA_BASE_URL, wait_until="networkidle")
                    time.sleep(2)

            return self._page

        except Exception as e:
            logger.error(f"BIMA page setup error: {e}")
            return None


def detect_semester_from_bima(username: str, password: str) -> Optional[str]:
    """Convenience function: login to BIMA and return current semester name."""
    scraper = BimaScraper(username, password)
    info = scraper.get_semester_info()
    if info and info.get("semester_name"):
        return info["semester_name"]
    return None
