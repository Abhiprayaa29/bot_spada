"""SPADA LMS Scraper - login, deadlines, attendance, screenshot."""

import re
import os
import time
import json
import tempfile
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class Deadline:
    course: str
    title: str
    url: str
    due_date: str
    due_datetime: Optional[datetime] = None
    activity_type: str = ""  # assignment, quiz, attendance


@dataclass
class AttendanceSession:
    course: str
    attendance_id: int
    session_date: str
    status: str  # open, closed, completed
    take_url: str = ""


class SpadaScraper:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        self.logged_in = False
        self._courses = []

    def login(self) -> bool:
        """Login to SPADA LMS."""
        try:
            login_page = self.session.get(f"{self.base_url}/login/index.php", verify=False)
            soup = BeautifulSoup(login_page.text, "html.parser")
            token_input = soup.find("input", {"name": "logintoken"})
            token = token_input.get("value", "") if token_input else ""

            login_data = {
                "username": self.username,
                "password": self.password,
                "logintoken": token,
            }
            resp = self.session.post(
                f"{self.base_url}/login/index.php",
                data=login_data,
                allow_redirects=True,
                verify=False,
            )

            if "Masuk" in resp.text and "Anda belum masuk" in resp.text:
                self.logged_in = False
                return False

            self.logged_in = True
            return True
        except Exception as e:
            print(f"[SPADA] Login error: {e}")
            return False

    def _ensure_login(self):
        if not self.logged_in:
            self.login()

    @staticmethod
    def _extract_semester(name: str) -> str:
        """Extract semester code from course name like 'Kriptografi IF-E (20261)' → '20261'."""
        m = re.search(r'\((\d{5})\)', name)
        return m.group(1) if m else ""

    @staticmethod
    def _clean_course_name(name: str) -> str:
        """Remove semester code from course name. 'Kriptografi IF-E (20261)' → 'Kriptografi'."""
        return re.sub(r'\s+IF-[A-Z]\s+\(\d{5}\)\s*$', '', name).strip()

    def get_courses(self, semester: str = "") -> list[dict]:
        """Get enrolled courses.
        
        Args:
            semester: If provided, only return courses matching this semester (e.g. "20261").
                      If empty, return all courses.
        """
        self._ensure_login()
        resp = self.session.get(f"{self.base_url}/my/", verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        courses = []
        course_links = soup.select("a[href*='course/view.php']")
        seen_ids = set()

        for link in course_links:
            href = link.get("href", "")
            match = re.search(r'id=(\d+)', href)
            if match:
                cid = match.group(1)
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    name = link.get_text(strip=True)
                    if name and len(name) > 3:
                        sem = self._extract_semester(name)
                        clean = self._clean_course_name(name)
                        if not semester or sem == semester:
                            courses.append({
                                "id": cid,
                                "name": clean,
                                "full_name": name,
                                "semester": sem,
                            })

        self._courses = courses
        return courses

    def get_all_semesters(self) -> list[str]:
        """Get sorted list of all unique semester codes."""
        self._ensure_login()
        resp = self.session.get(f"{self.base_url}/my/", verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        sems = set()
        for link in soup.select("a[href*='course/view.php']"):
            name = link.get_text(strip=True)
            sem = self._extract_semester(name)
            if sem:
                sems.add(sem)
        return sorted(sems, reverse=True)

    def get_deadlines(self, semester: str = "") -> list[Deadline]:
        """Get upcoming deadlines from dashboard timeline.
        
        Args:
            semester: If provided, only return deadlines from this semester.
        """
        self._ensure_login()
        resp = self.session.get(f"{self.base_url}/my/", verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        deadlines = []

        # Find timeline events
        events = soup.select(".event, .timeline-event, [class*='event']")
        for event in events:
            text = event.get_text(" ", strip=True)
            link = event.select_one("a")
            if link:
                href = link.get("href", "")
                title = link.get_text(strip=True)
            else:
                href = ""
                title = text[:100]

            # Parse due date from text
            due_str = ""
            due_dt = None

            # Common patterns: "Tomorrow, 12:00 AM", "Tuesday, 22 September, 1:00 PM"
            tomorrow_match = re.search(r'Tomorrow\s*,?\s*(\d{1,2}:\d{2}\s*(?:AM|PM))', text, re.I)
            if tomorrow_match:
                due_str = f"Tomorrow {tomorrow_match.group(1)}"
                tomorrow = datetime.now() + timedelta(days=1)
                time_str = tomorrow_match.group(1)
                try:
                    t = datetime.strptime(time_str, "%I:%M %p")
                    due_dt = tomorrow.replace(hour=t.hour, minute=t.minute, second=0)
                except:
                    pass

            date_match = re.search(r'(\w+day)\s*,\s*(\d{1,2})\s+(\w+)\s*,?\s*(\d{1,2}:\d{2}\s*(?:AM|PM))', text, re.I)
            if date_match:
                due_str = f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)} {date_match.group(4)}"

            # Determine activity type
            act_type = "other"
            if "assignment" in text.lower() or "tugas" in title.lower():
                act_type = "assignment"
            elif "quiz" in text.lower():
                act_type = "quiz"
            elif "attendance" in text.lower() or "presensi" in text.lower():
                act_type = "attendance"

            # Extract course name from context
            course = "Unknown"
            course_semester = ""
            parent = event.parent
            if parent:
                course_el = parent.select_one(".coursename, [class*='course']")
                if course_el:
                    course = course_el.get_text(strip=True)
                    course_semester = self._extract_semester(course)
                    course = self._clean_course_name(course)

            # Filter by semester if specified
            if semester and course_semester and course_semester != semester:
                continue

            deadlines.append(Deadline(
                course=course,
                title=title,
                url=href,
                due_date=due_str or text[:60],
                due_datetime=due_dt,
                activity_type=act_type,
            ))

        return deadlines

    def get_course_sections(self, course_id: str) -> list[dict]:
        """Get sections/activities for a specific course."""
        self._ensure_login()
        url = f"{self.base_url}/course/view.php?id={course_id}"
        resp = self.session.get(url, verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        sections = []
        for sec in soup.select(".section, .sli-content, [class*='section']"):
            heading = sec.select_one("h3, h4, .sectionname")
            if heading:
                name = heading.get_text(strip=True)
                activities = []
                for act in sec.select("li.activity, a[href*='mod/']"):
                    act_name = act.select_one("a, .activityname")
                    if act_name:
                        activities.append({
                            "name": act_name.get_text(strip=True),
                            "url": act_name.get("href", ""),
                        })
                sections.append({"name": name, "activities": activities})

        return sections

    def get_attendance_status(self, attendance_id: int) -> list[dict]:
        """Check attendance sessions and their status."""
        self._ensure_login()
        url = f"{self.base_url}/mod/attendance/view.php?id={attendance_id}&mode=2"
        resp = self.session.get(url, verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        sessions = []
        table = soup.select_one("table")
        if table:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 3:
                    date_text = cells[0].get_text(strip=True)
                    status = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                    points = cells[2].get_text(strip=True) if len(cells) > 2 else ""

                    # Check for "Take attendance" link
                    take_link = row.select_one("a[href*='take']")
                    take_url = take_link.get("href", "") if take_link else ""

                    sessions.append({
                        "date": date_text,
                        "status": status,
                        "points": points,
                        "take_url": take_url,
                    })

        return sessions

    def submit_attendance(self, attendance_id: int) -> dict:
        """Try to submit attendance (mark as Present/Hadir).
        
        Returns: {"success": bool, "message": str, "screenshot_path": str}
        """
        self._ensure_login()

        # First, get the attendance page
        url = f"{self.base_url}/mod/attendance/view.php?id={attendance_id}"
        resp = self.session.get(url, verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for open sessions with take links
        take_links = soup.select("a[href*='take']")

        if not take_links:
            # Try to find any clickable session
            all_links = soup.find_all("a")
            for link in all_links:
                href = link.get("href", "")
                if "take" in href or "submit" in href:
                    take_links.append(link)

        if not take_links:
            return {
                "success": False,
                "message": "No open attendance sessions found",
                "screenshot_path": "",
            }

        # Try to submit attendance for each open session
        for link in take_links:
            take_url = link.get("href", "")
            if not take_url.startswith("http"):
                take_url = f"{self.base_url}{take_url}"

            # Go to the take attendance page
            take_resp = self.session.get(take_url, verify=False)
            take_soup = BeautifulSoup(take_resp.text, "html.parser")

            # Find the attendance form
            form = take_soup.find("form", {"action": lambda a: a and "take" in str(a)})
            if not form:
                form = take_soup.find("form")

            if form:
                # Find status radio buttons (Hadir/Present)
                radios = form.find_all("input", {"type": "radio"})
                hadir_value = None
                for radio in radios:
                    label = radio.find_next("label")
                    if label and ("hadir" in label.get_text(strip=True).lower() or "present" in label.get_text(strip=True).lower()):
                        hadir_value = radio.get("value")
                        break

                if not hadir_value and radios:
                    hadir_value = radios[0].get("value")  # First option is usually "Hadir"

                if hadir_value:
                    # Build form data
                    form_data = {}
                    for inp in form.find_all("input"):
                        name = inp.get("name")
                        if name:
                            if inp.get("type") == "radio":
                                if inp.get("value") == hadir_value:
                                    form_data[name] = hadir_value
                            else:
                                form_data[name] = inp.get("value", "")

                    # Submit
                    action_url = form.get("action", take_url)
                    if not action_url.startswith("http"):
                        action_url = f"{self.base_url}{action_url}"

                    submit_resp = self.session.post(
                        action_url,
                        data=form_data,
                        allow_redirects=True,
                        verify=False,
                    )

                    # Check success
                    if submit_resp.status_code == 200:
                        # Take screenshot of the attendance status page as proof
                        screenshot_path = ""
                        try:
                            screenshot_path = self.take_screenshot(
                                f"{self.base_url}/mod/attendance/view.php?id={attendance_id}",
                                filename=f"absen_{attendance_id}_{int(time.time())}.png",
                            )
                        except Exception as e:
                            print(f"[SPADA] Screenshot failed: {e}")

                        return {
                            "success": True,
                            "message": "Attendance submitted successfully!",
                            "screenshot_path": screenshot_path,
                        }

        return {
            "success": False,
            "message": "Could not submit attendance",
            "screenshot_path": "",
        }

    def take_screenshot(self, url: str, filename: str = "screenshot.png") -> str:
        """Take a screenshot of a SPADA page using Playwright.
        
        Transfers the requests session cookies into Playwright and uses
        anti-detection settings to bypass the SPADA WAF.  Returns the
        path to the saved PNG file.
        """
        from playwright.sync_api import sync_playwright

        # Build cookie list from requests session for Playwright
        pw_cookies = []
        for cookie in self.session.cookies:
            pw_cookies.append({
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain or ".spada.upnyk.ac.id",
                "path": cookie.path or "/",
            })

        out_path = os.path.join(tempfile.gettempdir(), filename)
        ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            ctx = browser.new_context(
                ignore_https_errors=True,
                user_agent=ua,
                extra_http_headers={
                    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )

            # Anti-detection: remove webdriver flag
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id', 'en'] });
            """)

            # Inject SPADA cookies
            if pw_cookies:
                ctx.add_cookies(pw_cookies)

            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            # Give Moodle a moment to render
            page.wait_for_timeout(3000)
            page.screenshot(path=out_path, full_page=False)
            browser.close()

        return out_path

    def get_grades(self) -> list[dict]:
        """Get grades from the grade report."""
        self._ensure_login()
        url = f"{self.base_url}/grade/report/mygrades.php"
        resp = self.session.get(url, verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        grades = []
        table = soup.select_one("table")
        if table:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    course = cells[0].get_text(strip=True)
                    grade = cells[1].get_text(strip=True)
                    if course and grade:
                        grades.append({"course": course, "grade": grade})

        return grades

    def get_assignments(self, semester: str = "") -> list[dict]:
        """Get all assignments from enrolled courses.
        
        Args:
            semester: If provided, only return assignments from this semester.
        
        Returns list of dicts with: course, title, url, status, due_date, semester, has_deadline
        """
        self._ensure_login()
        assignments = []
        
        # Get courses (filtered by semester if specified)
        courses = self.get_courses(semester=semester)
        
        for course in courses:
            try:
                # Get assignments for this course
                url = f"{self.base_url}/mod/assign/index.php?id={course['id']}"
                resp = self.session.get(url, verify=False)
                soup = BeautifulSoup(resp.text, "html.parser")
                
                # Find assignment rows
                rows = soup.select("tr")
                for row in rows:
                    link = row.select_one("a[href*='assign/view']")
                    if link:
                        title = link.get_text(strip=True)
                        href = link.get("href", "")
                        if not href.startswith("http"):
                            href = f"{self.base_url}{href}"
                        
                        # Check status
                        status_cell = row.select_one("td:last-child")
                        status = status_cell.get_text(strip=True) if status_cell else "Unknown"
                        
                        # Check due date
                        due_cell = row.select_one("td:nth-child(3)")
                        due_date = due_cell.get_text(strip=True) if due_cell else ""
                        
                        # Determine if assignment has a real deadline
                        has_deadline = bool(due_date and due_date != "-" and due_date.strip())
                        
                        assignments.append({
                            "course": course["name"],
                            "course_id": course["id"],
                            "full_course": course.get("full_name", ""),
                            "title": title,
                            "url": href,
                            "status": status,
                            "due_date": due_date if has_deadline else "",
                            "semester": course.get("semester", ""),
                            "has_deadline": has_deadline,
                        })
            except Exception as e:
                print(f"[SPADA] Error fetching assignments for {course['name']}: {e}")
        
        return assignments

    def submit_assignment(self, assignment_url: str) -> dict:
        """Submit an assignment (if submission is open).
        
        Returns: {"success": bool, "message": str, "screenshot_path": str}
        """
        self._ensure_login()
        
        try:
            # Go to assignment page
            resp = self.session.get(assignment_url, verify=False)
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Check if submission is open
            submit_btn = soup.select_one("input[type='submit'], button[type='submit']")
            add_file_btn = soup.select_one("a[href*='add_submission'], input[value*='Add']")
            
            if not submit_btn and not add_file_btn:
                return {
                    "success": False,
                    "message": "Submission not available or already submitted",
                    "screenshot_path": "",
                }
            
            # Click "Add submission" if exists
            if add_file_btn:
                add_url = add_file_btn.get("href", "")
                if add_url:
                    if not add_url.startswith("http"):
                        add_url = f"{self.base_url}{add_url}"
                    resp = self.session.get(add_url, verify=False)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    submit_btn = soup.select_one("input[type='submit'], button[type='submit']")
            
            # Find the submission form
            form = soup.find("form", {"enctype": "multipart/form-data"})
            if not form:
                form = soup.find("form")
            
            if form and submit_btn:
                # Build form data (empty submission to mark as submitted)
                form_data = {}
                for inp in form.find_all("input"):
                    name = inp.get("name")
                    if name and inp.get("type") != "file":
                        form_data[name] = inp.get("value", "")
                
                # Add submit button value
                if submit_btn.get("name"):
                    form_data[submit_btn["name"]] = submit_btn.get("value", "Submit")
                
                # Submit form
                action_url = form.get("action", assignment_url)
                if not action_url.startswith("http"):
                    action_url = f"{self.base_url}{action_url}"
                
                submit_resp = self.session.post(
                    action_url,
                    data=form_data,
                    allow_redirects=True,
                    verify=False,
                )
                
                # Check success
                if submit_resp.status_code == 200:
                    # Take screenshot as proof
                    screenshot_path = ""
                    try:
                        screenshot_path = self.take_screenshot(
                            assignment_url,
                            filename=f"assignment_{int(time.time())}.png",
                        )
                    except Exception as e:
                        print(f"[SPADA] Screenshot failed: {e}")
                    
                    return {
                        "success": True,
                        "message": "Assignment submitted successfully!",
                        "screenshot_path": screenshot_path,
                    }
            
            return {
                "success": False,
                "message": "Could not find submission form",
                "screenshot_path": "",
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error: {str(e)}",
                "screenshot_path": "",
            }

    def upload_file_to_assignment(self, assignment_url: str, file_path: str, filename: str = "") -> dict:
        """Upload a file to an assignment.
        
        Returns: {"success": bool, "message": str, "screenshot_path": str}
        """
        self._ensure_login()
        
        try:
            if not filename:
                filename = os.path.basename(file_path)
            
            # Go to assignment page
            resp = self.session.get(assignment_url, verify=False)
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Find "Add submission" button
            add_link = soup.select_one("a[href*='add_submission'], input[value*='Add']")
            
            if add_link:
                add_url = add_link.get("href", "")
                if add_url:
                    if not add_url.startswith("http"):
                        add_url = f"{self.base_url}{add_url}"
                    resp = self.session.get(add_url, verify=False)
                    soup = BeautifulSoup(resp.text, "html.parser")
            
            # Find file upload form
            form = soup.find("form", {"enctype": "multipart/form-data"})
            if not form:
                form = soup.find("form")
            
            if not form:
                return {
                    "success": False,
                    "message": "Upload form not found",
                    "screenshot_path": "",
                }
            
            # Build form data
            form_data = {}
            for inp in form.find_all("input"):
                name = inp.get("name")
                if name:
                    if inp.get("type") == "file":
                        continue  # Skip file inputs
                    form_data[name] = inp.get("value", "")
            
            # Find file input name
            file_input = form.find("input", {"type": "file"})
            file_field_name = "file"  # default
            if file_input:
                file_field_name = file_input.get("name", "file")
            
            # Submit with file
            action_url = form.get("action", assignment_url)
            if not action_url.startswith("http"):
                action_url = f"{self.base_url}{action_url}"
            
            submit_resp = self.session.post(
                action_url,
                data=form_data,
                files={file_field_name: (filename, open(file_path, "rb"))},
                allow_redirects=True,
                verify=False,
            )
            
            if submit_resp.status_code == 200:
                # Take screenshot as proof
                screenshot_path = ""
                try:
                    screenshot_path = self.take_screenshot(
                        assignment_url,
                        filename=f"upload_{int(time.time())}.png",
                    )
                except Exception as e:
                    print(f"[SPADA] Screenshot failed: {e}")
                
                return {
                    "success": True,
                    "message": "File uploaded successfully!",
                    "screenshot_path": screenshot_path,
                }
            
            return {
                "success": False,
                "message": f"Upload failed (HTTP {submit_resp.status_code})",
                "screenshot_path": "",
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error: {str(e)}",
                "screenshot_path": "",
            }

    def get_assignment_status(self, assignment_url: str) -> dict:
        """Check assignment submission status.
        
        Returns: {"submitted": bool, "status": str, "grade": str, "feedback": str}
        """
        self._ensure_login()
        
        try:
            resp = self.session.get(assignment_url, verify=False)
            soup = BeautifulSoup(resp.text, "html.parser")
            
            status_text = ""
            grade = ""
            feedback = ""
            submitted = False
            
            # Method 1: Check submission status table (Moodle standard)
            status_table = soup.select_one(".submissionstatustable")
            if status_table:
                text = status_table.get_text(" ", strip=True)
                if "Submitted for grading" in text or "Disubmit untuk dinilai" in text:
                    submitted = True
                    status_text = "Submitted for grading"
                elif "No attempt" in text or "Belum ada usulan" in text:
                    submitted = False
                    status_text = "Not submitted"
                elif "Draft" in text:
                    submitted = False
                    status_text = "Draft"
            
            # Method 2: Check specific status cells
            if not submitted:
                status_cells = soup.select(".submissionstatussubmitted, [class*=statussubmitted]")
                if status_cells:
                    submitted = True
                    status_text = status_cells[0].get_text(strip=True)
            
            # Method 3: Check for "Edit submission" button (means already has draft/submission)
            if not submitted:
                edit_btn = soup.select_one("a[href*='edit'], input[value*='Edit']")
                if edit_btn:
                    status_text = "Draft exists"
            
            # Method 4: Check grading status
            grade_cells = soup.select(".gradinggrade, [class*=grading]")
            for cell in grade_cells:
                text = cell.get_text(strip=True)
                if text and text not in ("-", ""):
                    grade = text
                    break
            
            # Look for grade in submission status table
            if not grade and status_table:
                grade_match = None
                for row in status_table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        label = cells[0].get_text(strip=True)
                        value = cells[1].get_text(strip=True)
                        if "grade" in label.lower() or "nilai" in label.lower():
                            if value and value not in ("-", "Not graded", "Belum dinilai"):
                                grade = value
            
            # Look for feedback
            feedback_div = soup.select_one(".feedback, .editor, .comment")
            if feedback_div:
                feedback = feedback_div.get_text(strip=True)[:200]
            
            # Check time remaining for due date info
            time_remaining = ""
            time_div = soup.select_one(".timeselect, [class*=remaining]")
            if time_div:
                time_remaining = time_div.get_text(strip=True)[:50]
            
            return {
                "submitted": submitted,
                "status": status_text or ("Submitted" if submitted else "Not submitted"),
                "grade": grade,
                "feedback": feedback,
                "time_remaining": time_remaining,
            }
            
        except Exception as e:
            return {
                "submitted": False,
                "status": f"Error: {str(e)}",
                "grade": "",
                "feedback": "",
                "time_remaining": "",
            }

    def get_dashboard_text(self) -> str:
        """Get formatted dashboard summary."""
        courses = self.get_courses()
        deadlines = self.get_deadlines()

        lines = ["📊 *Dashboard SPADA WIMAYA*\n"]

        # Active deadlines
        if deadlines:
            lines.append("*📋 Deadline Mendatang:*")
            for d in deadlines[:10]:
                emoji = "📝" if d.activity_type == "assignment" else "❓" if d.activity_type == "quiz" else "📅"
                lines.append(f"  {emoji} {d.title}")
                if d.due_date:
                    lines.append(f"     ⏰ {d.due_date}")
            lines.append("")

        # Course count
        lines.append(f"*📚 Total Mata Kuliah:* {len(courses)}")

        return "\n".join(lines)

    # ── Auto-detect helpers ───────────────────────────────────

    def detect_current_semester(self) -> str:
        """Return the semester code with the most enrolled courses.

        Typically the semester with the most courses is the active one.
        Falls back to the lexicographically largest code if counts are equal.
        """
        all_semesters = self.get_all_semesters()
        if not all_semesters:
            return ""

        best_sem = ""
        best_count = 0
        for sem in all_semesters:
            courses = self.get_courses(semester=sem)
            if len(courses) > best_count:
                best_count = len(courses)
                best_sem = sem
        return best_sem

    def scrape_attendance_ids(self, semester: str = "") -> dict[str, int]:
        """Scan every course page for attendance activities.

        Returns a dict mapping cleaned course name → attendance module id.
        """
        self._ensure_login()
        courses = self.get_courses(semester=semester)
        amap: dict[str, int] = {}

        for course in courses:
            try:
                url = f"{self.base_url}/course/view.php?id={course['id']}"
                resp = self.session.get(url, verify=False)
                soup = BeautifulSoup(resp.text, "html.parser")

                for link in soup.select("a[href*='mod/attendance/view']"):
                    href = link.get("href", "")
                    m = re.search(r'id=(\d+)', href)
                    if m:
                        amap[course["name"]] = int(m.group(1))
                        break  # take the first attendance per course
            except Exception as e:
                print(f"[SPADA] Error scraping attendance for {course['name']}: {e}")

        return amap

    def update_credentials(self, username: str, password: str):
        """Swap credentials and reset the session so the next request re-logs in."""
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        self.logged_in = False
        self._courses = []
