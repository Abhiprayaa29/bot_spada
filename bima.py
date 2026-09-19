"""BIMA Scraper — DrissionPage-based login with reCAPTCHA v2 bypass.

Handles https://bima.upnyk.ac.id/login which has:
  - Laravel CSRF token
  - reCAPTCHA v2 (sitekey: 6LekiE0sAAAAABv_pEjSv8h_B6WNnz8BTlqe7AYZ)
  - Same credentials as SPADA

Strategy (DrissionPage):
  - Uses Chrome DevTools Protocol (CDP) directly — no WebDriver fingerprint
  - DrissionPage's ChromiumPage is much harder for reCAPTCHA to detect
  - Adds human-like delays, random scrolling, realistic mouse patterns
  - Falls back gracefully if reCAPTCHA challenge appears
  - Saves cookies for session reuse across restarts
"""

import os
import re
import json
import time
import random
import logging
from typing import Optional

from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger(__name__)

BIMA_BASE_URL = "https://bima.upnyk.ac.id"
BIMA_LOGIN_URL = f"{BIMA_BASE_URL}/login"
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "data", "bima_cookies.json")


class BimaScraper:
    """DrissionPage-based BIMA scraper with reCAPTCHA v2 handling."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.logged_in = False
        self._page: Optional[ChromiumPage] = None

    def _ensure_dir(self):
        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)

    def _save_cookies(self):
        """Persist cookies for session reuse."""
        try:
            self._ensure_dir()
            cookies = self._page.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f, indent=2, default=str)
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
            # Check if cookies are expired (older than 24 hours)
            for cookie in cookies:
                if "expiry" in cookie:
                    if cookie["expiry"] < time.time():
                        logger.info("BIMA cookies expired")
                        return False
            for cookie in cookies:
                self._page.cookies(cookie)
            logger.info("Loaded BIMA cookies from disk")
            return True
        except Exception as e:
            logger.warning(f"Failed to load BIMA cookies: {e}")
            return False

    def _start_browser(self):
        """Launch Chromium via DrissionPage CDP with stealth settings."""
        try:
            co = ChromiumOptions()
            co.headless()
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            co.set_argument("--disable-gpu")
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--disable-infobars")
            co.set_argument("--window-size=1280,800")
            co.set_argument("--lang=id-ID")
            co.set_user_agent(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            )
            # Use a fresh profile to avoid residual automation flags
            co.set_user_data_path("/tmp/bima_chrome_profile")

            self._page = ChromiumPage(co)
            logger.info("DrissionPage Chromium started")
        except Exception as e:
            logger.error(f"Failed to start browser: {e}")
            raise

    def _stop_browser(self):
        """Clean up browser resources."""
        try:
            if self._page:
                self._page.quit()
        except Exception:
            pass
        self._page = None

    def _human_delay(self, min_s=0.5, max_s=2.0):
        """Random human-like delay."""
        time.sleep(random.uniform(min_s, max_s))

    def _human_type(self, element, text: str):
        """Type text character by character with random delays (human-like)."""
        for char in text:
            element.input(char)
            time.sleep(random.uniform(0.05, 0.15))

    def _human_scroll(self):
        """Random small scroll to simulate human behavior."""
        scroll_amount = random.randint(50, 200)
        self._page.scroll.down(scroll_amount)
        self._human_delay(0.3, 0.8)

    def _solve_recaptcha(self) -> bool:
        """Attempt to solve reCAPTCHA v2 using DrissionPage.

        DrissionPage uses CDP directly (not WebDriver), so reCAPTCHA
        is much less likely to detect automation. The checkbox click
        often passes without showing a challenge.

        Returns True if solved, False if challenge appeared.
        """
        try:
            # Wait for reCAPTCHA iframe to load
            self._human_delay(1, 2)

            # Find the reCAPTCHA iframe via CDP
            # DrissionPage can access iframe content directly
            recaptcha_iframe = self._page.get_frame("recaptcha")

            if recaptcha_iframe is None:
                # Try finding by src pattern
                iframes = self._page.get_frames()
                recaptcha_iframe = None
                for frame in iframes:
                    src = frame.attr("src") or ""
                    if "recaptcha" in src and "api2/anchor" in src:
                        recaptcha_iframe = frame
                        break

            if recaptcha_iframe is None:
                logger.warning("reCAPTCHA iframe not found")
                return False

            # Click the checkbox
            checkbox = recaptcha_iframe.ele("id:recaptcha-anchor", timeout=5)
            if checkbox:
                self._human_delay(0.5, 1)
                checkbox.click()
                logger.info("Clicked reCAPTCHA checkbox")

                # Wait for reCAPTCHA to process
                self._human_delay(3, 5)

                # Check if solved (checkbox gets checked class)
                try:
                    anchor = recaptcha_iframe.ele("id:recaptcha-anchor", timeout=3)
                    if anchor:
                        aria = anchor.attr("aria-checked") or ""
                        if aria == "true":
                            logger.info("reCAPTCHA solved (checkbox checked)")
                            return True
                except Exception:
                    pass

                # Check if challenge appeared
                challenge_frame = None
                for frame in self._page.get_frames():
                    src = frame.attr("src") or ""
                    if "recaptcha" in src and "api2/bframe" in src:
                        challenge_frame = frame
                        break

                if challenge_frame:
                    try:
                        challenge_frame.ele("css:.rc-imageselect-desc-no-canonical", timeout=3)
                        logger.warning("reCAPTCHA challenge appeared — cannot auto-solve")
                        return False
                    except Exception:
                        pass

                # No challenge detected — might be solved or processing
                logger.info("No challenge detected, proceeding")
                return True
            else:
                logger.warning("reCAPTCHA checkbox not found")
                return False

        except Exception as e:
            logger.error(f"reCAPTCHA interaction failed: {e}")
            return False

    def login(self) -> bool:
        """Login to BIMA with reCAPTCHA v2 handling."""
        try:
            self._start_browser()

            # Try loading saved cookies first
            if self._load_cookies():
                self._page.get(BIMA_BASE_URL)
                self._human_delay(2, 3)
                if "/login" not in self._page.url:
                    logger.info("BIMA session restored from cookies")
                    self.logged_in = True
                    return True
                logger.info("BIMA cookies expired, logging in again")

            # Load login page
            self._page.get(BIMA_LOGIN_URL)
            self._human_delay(2, 3)

            # Human-like scroll
            self._human_scroll()

            # Extract CSRF token
            csrf_input = self._page.ele("name:_token", timeout=5)
            csrf_token = csrf_input.val if csrf_input else ""
            logger.info(f"Got CSRF token: {csrf_token[:10]}...")

            # Fill credentials with human-like typing
            username_input = self._page.ele("name:username", timeout=5)
            password_input = self._page.ele("name:password", timeout=5)

            if username_input and password_input:
                username_input.click()
                self._human_delay(0.3, 0.5)
                self._human_type(username_input, self.username)

                self._human_delay(0.5, 1)

                password_input.click()
                self._human_delay(0.3, 0.5)
                self._human_type(password_input, self.password)

                logger.info("Filled credentials")
            else:
                logger.error("Login form inputs not found")
                return False

            # Small delay before reCAPTCHA
            self._human_delay(1, 2)

            # Solve reCAPTCHA
            recaptcha_solved = self._solve_recaptcha()
            if not recaptcha_solved:
                logger.warning("reCAPTCHA not solved, trying to submit anyway")

            # Human delay before submit
            self._human_delay(0.5, 1)

            # Click submit button
            submit = self._page.ele("css:button[type='submit'], input[type='submit'], .btn-primary", timeout=5)
            if submit:
                submit.click()
                logger.info("Clicked submit")
            else:
                # Fallback: submit form via JS
                self._page.run_js("document.querySelector('form').submit()")
                logger.info("Submitted form via JS")

            # Wait for navigation
            self._human_delay(3, 5)

            # Check if login succeeded
            if "/login" in self._page.url:
                logger.error("Login failed — still on login page")
                try:
                    error_el = self._page.ele("css:.alert-danger, .error, .invalid-feedback", timeout=2)
                    if error_el:
                        logger.error(f"Error message: {error_el.text}")
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
        if not self.logged_in:
            if not self.login():
                return None

        try:
            self._start_browser()

            # Load cookies and navigate
            if self._load_cookies():
                self._page.get(BIMA_BASE_URL)
                self._human_delay(2, 3)
                if "/login" in self._page.url:
                    self._stop_browser()
                    self.logged_in = False
                    if not self.login():
                        return None
                    self._start_browser()
                    if self._load_cookies():
                        self._page.get(BIMA_BASE_URL)
                        self._human_delay(2, 3)
            else:
                self._stop_browser()
                self.logged_in = False
                if not self.login():
                    return None
                self._start_browser()
                if self._load_cookies():
                    self._page.get(BIMA_BASE_URL)
                    self._human_delay(2, 3)

            raw_text = self._page.html or ""
            result = {}

            # Pattern 1: semester dropdown
            try:
                semester_select = self._page.ele("css:select[name*='semester'], select[name*='periode'], #semester", timeout=3)
                if semester_select:
                    selected_val = semester_select.val
                    result["semester_id"] = selected_val
                    # Get selected option text
                    selected_option = self._page.ele(f"css:select option[value='{selected_val}']", timeout=2)
                    if selected_option:
                        result["semester_name"] = selected_option.text.strip()
                    # Get all options
                    options = semester_select.eles("tag:option")
                    result["available_semesters"] = [
                        {"value": opt.attr("value"), "text": opt.text.strip()}
                        for opt in options
                    ]
                    logger.info(f"Found semester: {result.get('semester_name')} (id={selected_val})")
            except Exception as e:
                logger.debug(f"No semester dropdown found: {e}")

            # Pattern 2: semester text in page
            if not result.get("semester_name"):
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
                    period_el = self._page.ele("css:.period, .semester, .periode, [class*='semester'], [class*='periode']", timeout=3)
                    if period_el:
                        result["semester_name"] = period_el.text.strip()
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
        finally:
            self._stop_browser()

    def get_courses(self) -> list[dict]:
        """Scrape enrolled courses from BIMA dashboard for the active semester."""
        if not self.logged_in:
            if not self.login():
                return []

        try:
            self._start_browser()

            if self._load_cookies():
                self._page.get(BIMA_BASE_URL)
                self._human_delay(2, 3)
            else:
                self._stop_browser()
                self.logged_in = False
                if not self.login():
                    return []
                self._start_browser()
                if self._load_cookies():
                    self._page.get(BIMA_BASE_URL)
                    self._human_delay(2, 3)

            raw_text = self._page.html or ""
            courses = []
            seen = set()

            # Strategy 1: look for course cards/links on dashboard
            course_selectors = [
                "css:a[href*='course']",
                "css:.card a",
                "css:.panel a",
                "css:table a[href*='matkul']",
                "css:.list-group-item a",
                "css:a[href*='matakuliah']",
                "css:[class*='course'] a",
            ]
            for sel in course_selectors:
                try:
                    links = self._page.eles(sel, timeout=2)
                    for link in links:
                        text = link.text.strip()
                        href = link.attr("href") or ""
                        if text and len(text) > 3 and text not in seen:
                            seen.add(text)
                            cid_match = re.search(r'id[=/](\d+)', href)
                            courses.append({
                                "name": text,
                                "id": cid_match.group(1) if cid_match else "",
                            })
                except Exception:
                    continue

            # Strategy 2: parse course names from raw text
            if not courses:
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
        finally:
            self._stop_browser()


def detect_semester_from_bima(username: str, password: str) -> Optional[str]:
    """Convenience function: login to BIMA and return current semester name."""
    scraper = BimaScraper(username, password)
    info = scraper.get_semester_info()
    if info and info.get("semester_name"):
        return info["semester_name"]
    return None
