import os
import re
import html
import json
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import ssl
import certifi
import keyring
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Automatically load from repo root .env.local or .env if present
root_dir = Path(__file__).resolve().parent.parent
load_dotenv(root_dir / ".env.local")
load_dotenv(root_dir / ".env")

BASE_URL = os.getenv("LMS_BASE_URL") or os.getenv("LMS_URL") or "https://lms.nust.edu.pk"
KEYRING_SERVICE = "questlms"
SESSION_FILE = Path.home() / ".cache" / "questlms" / "session.json"

# Screen-reader-only spans that leak into visible text ("Course" prefix, " File" suffix, ...)
HIDDEN_TEXT_SELECTOR = ".accesshide, .sr-only, .visually-hidden"


def load_credentials() -> Tuple[str, str]:
    """Read credentials from env vars / .env.local first, then the OS keychain."""
    username = os.getenv("LMS_USERNAME", "")
    password = os.getenv("LMS_PASSWORD", "")
    if username and password:
        return username, password
    try:
        username = username or keyring.get_password(KEYRING_SERVICE, "username") or ""
        if username:
            password = password or keyring.get_password(KEYRING_SERVICE, username) or ""
    except keyring.errors.KeyringError:
        pass
    return username, password


def save_credentials(username: str, password: str):
    """Store credentials in the OS keychain (macOS Keychain, Windows Credential Locker, Secret Service)."""
    keyring.set_password(KEYRING_SERVICE, "username", username)
    keyring.set_password(KEYRING_SERVICE, username, password)


def clear_credentials():
    """Remove stored credentials from the OS keychain and drop the cached session."""
    username = keyring.get_password(KEYRING_SERVICE, "username")
    for key in filter(None, [username, "username"]):
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass
    SESSION_FILE.unlink(missing_ok=True)


class LegacyTLSAdapter(HTTPAdapter):
    """Allow legacy TLS renegotiation, which the LMS server requires but OpenSSL 3 blocks by default.

    Certificate and hostname verification stay on: the LMS has a valid certificate.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _soup(markup: str) -> BeautifulSoup:
    soup = BeautifulSoup(markup, "html.parser")
    for el in soup.select(HIDDEN_TEXT_SELECTOR):
        el.decompose()
    return soup


def _html_to_text(markup: Optional[str]) -> str:
    if not markup:
        return ""
    return BeautifulSoup(markup, "html.parser").get_text(" ", strip=True)


def _cmid_from_url(url: Optional[str]) -> Optional[int]:
    """Course-module id (the `id=` in mod/<x>/view.php?id=...) from an LMS URL."""
    match = re.search(r"[?&]id=(\d+)", url or "")
    return int(match.group(1)) if match else None


def _filename_from_url(url: str) -> str:
    return urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)


class LMSError(RuntimeError):
    """Raised when the LMS rejects a request or returns something unexpected."""


class NustLMSClient:
    """Session-managed client for NUST Moodle LMS."""

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None, session_file: Optional[str] = None):
        if not (username and password):
            username, password = load_credentials()
        self.username = username
        self.password = password
        self.base_url = BASE_URL.rstrip("/")
        self.session_file = Path(session_file or SESSION_FILE)
        self.session = requests.Session()
        self.session.mount("https://", LegacyTLSAdapter())
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.sesskey: Optional[str] = None
        self.user_name: Optional[str] = None
        self.user_id: Optional[str] = None

    def _ensure_session_dir(self):
        self.session_file.parent.mkdir(parents=True, exist_ok=True)

    def _save_session_cache(self):
        try:
            self._ensure_session_dir()
            data = {
                "cookies": requests.utils.dict_from_cookiejar(self.session.cookies),
                "sesskey": self.sesskey,
                "user_name": self.user_name,
                "user_id": self.user_id,
            }
            # Session cookies grant LMS access, so keep the cache readable by the owner only
            fd = os.open(self.session_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _load_session_cache(self) -> bool:
        if not self.session_file.exists():
            return False
        try:
            with open(self.session_file, "r") as f:
                data = json.load(f)
            self.session.cookies.update(requests.utils.cookiejar_from_dict(data.get("cookies", {})))
            self.sesskey = data.get("sesskey")
            self.user_name = data.get("user_name")
            self.user_id = data.get("user_id")
            return bool(self.sesskey)
        except Exception:
            return False

    @staticmethod
    def _is_login_page(res: requests.Response) -> bool:
        return "/login/index.php" in res.url or 'name="logintoken"' in res.text

    def _read_profile(self, html: str):
        """Pick up sesskey, full name and user id from any logged-in page."""
        sesskey_match = re.search(r'"sesskey":"([^"]+)"', html)
        if sesskey_match:
            self.sesskey = sesskey_match.group(1)
        uid_match = re.search(r'"userId":(\d+)', html) or re.search(r'data-userid="(\d+)"', html)
        if uid_match:
            self.user_id = uid_match.group(1)
        soup = BeautifulSoup(html, "html.parser")
        name_elem = soup.select_one(".user-name, .usermenu .usertext")
        if name_elem and name_elem.get_text(strip=True):
            self.user_name = name_elem.get_text(" ", strip=True)
        else:
            initials = soup.select_one(".userinitials[title], .userpicture[title]")
            if initials:
                self.user_name = initials["title"].strip()

    def is_logged_in(self) -> bool:
        """Checks if current session is active without re-logging in."""
        try:
            res = self.session.get(f"{self.base_url}/my/", timeout=10)
            if self._is_login_page(res):
                return False
            self._read_profile(res.text)
            return True
        except requests.RequestException:
            return False

    def login(self, force: bool = False) -> Dict[str, Any]:
        """Logs into NUST LMS and extracts session tokens."""
        if not force and self._load_session_cache() and self.is_logged_in():
            self._save_session_cache()
            return {
                "status": "success",
                "cached": True,
                "user": self.user_name or self.username,
                "sesskey": self.sesskey,
                "user_id": self.user_id
            }

        if not self.username or not self.password:
            raise ValueError(
                "No LMS credentials found. Run `.venv/bin/python3 src/cli.py login` to store them in your OS keychain"
            )

        # Start from a clean cookie jar so a stale session can't leak into the new one
        self.session.cookies.clear()
        self.sesskey = None

        # 1. Fetch login page to retrieve logintoken
        login_page_url = f"{self.base_url}/login/index.php"
        res = self.session.get(login_page_url, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        token_input = soup.find("input", {"name": "logintoken"})
        if not token_input or not token_input.get("value"):
            raise LMSError("Failed to extract 'logintoken' from login page")

        # 2. Post credentials
        login_data = {
            "username": self.username,
            "password": self.password,
            "logintoken": token_input["value"],
        }
        post_res = self.session.post(login_page_url, data=login_data, timeout=20)

        # 3. Verify success: a failed login lands back on the login form
        if "Invalid login" in post_res.text or self._is_login_page(post_res):
            raise PermissionError("Invalid LMS username or password. Please verify credentials.")

        self._read_profile(post_res.text)
        if not self.sesskey or not self.user_name:
            my_res = self.session.get(f"{self.base_url}/my/", timeout=15)
            self._read_profile(my_res.text)

        if not self.sesskey:
            raise LMSError("Login completed but sesskey could not be extracted.")

        self._save_session_cache()

        return {
            "status": "success",
            "cached": False,
            "user": self.user_name or self.username,
            "sesskey": self.sesskey,
            "user_id": self.user_id
        }

    def ensure_authenticated(self):
        """Ensures a session exists. Expired sessions are detected and renewed per request."""
        if not self.sesskey:
            self.login()

    def _get_page(self, url: str, timeout: int = 20) -> requests.Response:
        """GET an LMS page, logging in again once if the session has expired."""
        self.ensure_authenticated()
        res = self.session.get(url, timeout=timeout)
        if self._is_login_page(res):
            self.login(force=True)
            res = self.session.get(url, timeout=timeout)
            if self._is_login_page(res):
                raise LMSError(f"LMS keeps redirecting to the login page for {url}")
        res.raise_for_status()
        return res

    def _ajax(self, methodname: str, args: Dict[str, Any]) -> Any:
        """Call a Moodle AJAX web service, renewing the session once if it has expired."""
        self.ensure_authenticated()
        for attempt in range(2):
            ajax_url = f"{self.base_url}/lib/ajax/service.php?sesskey={self.sesskey}&info={methodname}"
            res = self.session.post(ajax_url, json=[{"index": 0, "methodname": methodname, "args": args}], timeout=20)
            try:
                result = res.json()[0]
            except (ValueError, IndexError, KeyError, TypeError):
                raise LMSError(f"Unexpected response from LMS for {methodname} (HTTP {res.status_code})")
            if not result.get("error"):
                return result.get("data")
            exc = result.get("exception") or {}
            if attempt == 0 and exc.get("errorcode") in ("invalidsesskey", "servicerequireslogin", "requireloginerror"):
                self.login(force=True)
                continue
            raise LMSError(f"{methodname} failed: {exc.get('message') or exc.get('errorcode') or 'unknown error'}")

    def _calendar_events(self, limit: int, timesortfrom: Optional[int] = None, timesortto: Optional[int] = None) -> List[Dict[str, Any]]:
        args: Dict[str, Any] = {"limitnum": limit}
        if timesortfrom is not None:
            args["timesortfrom"] = timesortfrom
        if timesortto is not None:
            args["timesortto"] = timesortto
        data = self._ajax("core_calendar_get_action_events_by_timesort", args) or {}
        return data.get("events", [])

    def get_upcoming_deadlines(self, limit: int = 20, include_overdue: bool = True) -> List[Dict[str, Any]]:
        """Pending (not yet submitted) deadlines from the Moodle timeline: overdue first, then upcoming by due date."""
        now = int(time.time())
        limit = max(1, min(int(limit), 50))  # Moodle rejects limitnum outside 1..50
        raw_events = []
        if include_overdue:
            raw_events += self._calendar_events(50, timesortfrom=0, timesortto=now)
        # Queried separately so a backlog of overdue items can't push upcoming ones past the limit
        raw_events += self._calendar_events(limit, timesortfrom=now)

        events = []
        seen = set()
        for ev in raw_events:
            if ev.get("id") in seen:
                continue
            seen.add(ev.get("id"))
            course = ev.get("course") or {}
            action = ev.get("action") or {}
            due_ts = ev.get("timesort")
            events.append({
                "id": ev.get("id"),
                "name": html.unescape(ev.get("activityname") or ev.get("name") or ""),
                "event_name": html.unescape(ev.get("name") or ""),
                "module_name": ev.get("modulename"),
                "assign_id": _cmid_from_url(ev.get("url")) if ev.get("modulename") == "assign" else None,
                "course_id": course.get("id"),
                "course_name": html.unescape(course.get("fullname") or ""),
                "course_code": html.unescape(course.get("shortname") or ""),
                "timestamp": due_ts,
                "due_date": _html_to_text(ev.get("formattedtime")),
                "due_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(due_ts)) if due_ts else None,
                "overdue": bool(ev.get("overdue")) if "overdue" in ev else bool(due_ts and due_ts < now),
                "url": ev.get("url"),
                "action_name": action.get("name"),
                "action_url": action.get("url"),
                "is_actionable": action.get("actionable", True),
                "instance": ev.get("instance"),
            })
        return events

    def get_enrolled_courses(self, include_past: bool = False) -> List[Dict[str, Any]]:
        """Lists enrolled courses with progress and IDs (current semester only unless include_past)."""
        data = self._ajax("core_course_get_enrolled_courses_by_timeline_classification", {
            "classification": "all" if include_past else "inprogress",
            "limit": 0,
            "offset": 0,
            "sort": "fullname"
        }) or {}

        courses = []
        for c in data.get("courses", []):
            courses.append({
                "id": c.get("id"),
                "fullname": html.unescape(c.get("fullname") or ""),
                "shortname": html.unescape(c.get("shortname") or ""),
                "progress": c.get("progress"),
                "view_url": f"{self.base_url}/course/view.php?id={c.get('id')}"
            })
        return courses

    def get_course_contents(self, course_id: int) -> Dict[str, Any]:
        """Scrapes sections, lab modules, lecture resources, and assignments for a course."""
        course_url = f"{self.base_url}/course/view.php?id={course_id}"
        soup = _soup(self._get_page(course_url).text)

        for label in soup.select("h1 .course-backtext"):  # theme's "Course" label inside the heading
            label.decompose()
        course_title = soup.find("h1") or soup.find("title")
        title_text = course_title.get_text(" ", strip=True) if course_title else f"Course {course_id}"

        sections = []
        for sec in soup.select("li.section"):
            sec_title_elem = sec.select_one(".sectionname")
            sec_title = sec_title_elem.get_text(" ", strip=True) if sec_title_elem else "General"

            activities = []
            for act in sec.select("li.activity"):
                # Activities inside a subsection belong to that subsection's own <li.section>
                if act.find_parent("li", class_="section") is not sec:
                    continue
                act_classes = act.get("class", [])
                act_type = next((c[len("modtype_"):] for c in act_classes if c.startswith("modtype_")), "other")
                if act_type == "subsection":
                    continue
                link_elem = act.select_one("a.aalink, a[href*='/mod/']") or act.select_one("a[href]")
                name_elem = act.select_one(".instancename") or link_elem
                module_id = act.get("id", "").replace("module-", "")

                activities.append({
                    "name": name_elem.get_text(" ", strip=True) if name_elem else "Untitled",
                    "type": act_type,
                    "url": link_elem["href"] if link_elem and link_elem.get("href") else None,
                    "module_id": int(module_id) if module_id.isdigit() else None
                })

            if activities:
                sections.append({
                    "section_name": sec_title,
                    "is_subsection": "delegated-section" in sec.get("class", []),
                    "activities": activities
                })

        return {
            "course_id": course_id,
            "title": title_text,
            "sections": sections
        }

    def get_assignment_details(self, assign_id: int) -> Dict[str, Any]:
        """Scrapes full assignment information, description, dates, status, and attached files."""
        assign_url = f"{self.base_url}/mod/assign/view.php?id={assign_id}"
        soup = _soup(self._get_page(assign_url).text)

        if soup.select_one("#page-mod-assign-view, body[id^='page-mod-assign']") is None and not soup.select_one(".submissionstatustable, [data-region=activity-dates]"):
            raise LMSError(f"Module {assign_id} is not an assignment you can access")

        title = soup.select_one("#region-main h2") or soup.find("h2") or soup.find("title")
        title_text = title.get_text(" ", strip=True) if title else "Assignment"

        # Description / instructions
        intro_elem = soup.select_one(".activity-description, #intro")
        description = ""
        if intro_elem:
            text_elem = intro_elem.select_one(".no-overflow")
            if text_elem is None:
                # No text body: drop the attachment list so file names/timestamps aren't read as the description
                text_elem = BeautifulSoup(str(intro_elem), "html.parser")
                for el in text_elem.select("ul:has(.fileuploadsubmission), .fileuploadsubmission, .fileuploadsubmissiontime"):
                    el.decompose()
            description = text_elem.get_text(separator="\n", strip=True)

        # Opened / Due / Cut-off dates
        dates = {}
        for div in soup.select("[data-region=activity-dates] > div"):
            label = div.find("strong")
            if label:
                key = label.get_text(strip=True).rstrip(":").lower()
                label.extract()
                dates[key] = div.get_text(" ", strip=True)

        # Files: instructor attachments vs. files you have already submitted
        attached_files, submitted_files, seen = [], [], set()
        for file_link in soup.select("a[href*='pluginfile.php']"):
            href = file_link["href"]
            if href in seen:
                continue
            seen.add(href)
            entry = {"name": file_link.get_text(strip=True) or _filename_from_url(href), "url": href}
            if "/assignsubmission_" in href:
                submitted_files.append(entry)
            elif "/mod_assign/" in href:
                attached_files.append(entry)

        # Links in the description that point at external material (Drive, GitHub, ...)
        links = []
        if intro_elem:
            for a in intro_elem.select("a[href]"):
                if "pluginfile.php" not in a["href"] and a["href"].startswith("http"):
                    links.append({"text": a.get_text(" ", strip=True), "url": a["href"]})

        # Submission status table
        status_table = {}
        for tr in soup.select("table.generaltable tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                status_table[th.get_text(strip=True).lower()] = td.get_text(" ", strip=True)

        # Which actions the LMS currently offers (hidden "action" inputs on the button forms)
        actions = {inp.get("value") for inp in soup.select("form input[name=action]")}
        status = status_table.get("submission status", "Unknown")

        return {
            "id": assign_id,
            "title": title_text,
            "url": assign_url,
            "description": description,
            "status": status,
            "submitted": status.lower().startswith("submitted"),
            "is_draft": status.lower().startswith("draft"),
            "grading_status": status_table.get("grading status", "Not graded"),
            "grade": status_table.get("grade"),
            "opened": dates.get("opened") or dates.get("allow submissions from"),
            "due_date": dates.get("due") or status_table.get("due date", "Unknown"),
            "cutoff_date": dates.get("cut-off date"),
            "time_remaining": status_table.get("time remaining", "Unknown"),
            "last_modified": status_table.get("last modified"),
            "attempt": status_table.get("attempt number"),
            "can_edit_submission": "editsubmission" in actions,
            "can_submit_for_grading": "submit" in actions,
            "attached_files": attached_files,
            "submitted_files": submitted_files,
            "links": links
        }

    def download_file(self, file_url: str, output_path: str) -> str:
        """Downloads a protected file from LMS using the active session."""
        self.ensure_authenticated()
        dest = Path(output_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        with self.session.get(file_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            if self._is_login_page(r):
                raise LMSError(f"Session expired while downloading {file_url}")
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return str(dest)

    def download_assignment_assets(self, assign_id: int, target_dir: str) -> List[str]:
        """Downloads all instructor-attached manuals and resources for an assignment."""
        details = self.get_assignment_details(assign_id)
        out_dir = Path(target_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        downloaded_paths = []
        used_names = set()
        for f in details.get("attached_files", []):
            safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', _filename_from_url(f["url"]) or f["name"]).lstrip(".") or "file"
            stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
            n = 1
            while safe_name in used_names:
                n += 1
                safe_name = f"{stem}_{n}{suffix}"
            used_names.add(safe_name)
            downloaded_paths.append(self.download_file(f["url"], str(out_dir / safe_name)))

        return downloaded_paths

    @staticmethod
    def _form_fields(form) -> Dict[str, Any]:
        """Collect what a browser would send for a form, without pressing Cancel."""
        data: Dict[str, Any] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            itype = (inp.get("type") or "text").lower()
            if not name or itype in ("submit", "button", "image", "reset", "file"):
                continue
            if itype in ("checkbox", "radio") and not inp.has_attr("checked"):
                continue
            data[name] = inp.get("value", "")
        for ta in form.find_all("textarea"):
            if ta.get("name"):
                data[ta["name"]] = ta.get_text()
        for sel in form.find_all("select"):
            if sel.get("name"):
                opt = sel.find("option", selected=True) or sel.find("option")
                data[sel["name"]] = opt.get("value", opt.get_text()) if opt else ""
        return data

    @staticmethod
    def _page_errors(soup: BeautifulSoup) -> List[str]:
        return [e.get_text(" ", strip=True) for e in soup.select(".form-control-feedback.invalid-feedback, .error, .alert-danger, .notifyproblem") if e.get_text(strip=True)]

    def _upload_to_draft(self, page_html: str, draft_itemid: str, path: Path) -> str:
        """Upload a file into a filemanager draft area; returns the stored filename."""
        repos = re.search(r'"repositories":(\{.*?\}\}),"externallink"', page_html)
        repo_id = None
        if repos:
            try:
                repo_id = next((r["id"] for r in json.loads(repos.group(1)).values() if r.get("type") == "upload"), None)
            except ValueError:
                pass
        if not repo_id:
            raise LMSError("This assignment does not accept file uploads (no upload repository on the form)")
        ctx_match = re.search(r'"context":\{"id":(\d+)', page_html)
        client_match = re.search(r'"client_id":"([^"]+)"', page_html)

        common = {
            "sesskey": self.sesskey,
            "itemid": draft_itemid,
            "ctx_id": ctx_match.group(1) if ctx_match else "",
            "client_id": client_match.group(1) if client_match else "",
            "env": "filemanager",
            "savepath": "/",
        }
        with open(path, "rb") as f_data:
            res = self.session.post(
                f"{self.base_url}/repository/repository_ajax.php?action=upload",
                data={**common, "repo_id": repo_id, "title": path.name, "author": self.user_name or "", "license": "unknown",
                      "maxbytes": -1, "areamaxbytes": -1},
                files={"repo_upload_file": (path.name, f_data, "application/octet-stream")},
                timeout=120,
            )
        try:
            result = res.json()
        except ValueError:
            raise LMSError(f"File upload failed (HTTP {res.status_code})")
        if result.get("error"):
            raise LMSError(f"File upload rejected by LMS: {result['error']}")

        if result.get("event") == "fileexists":
            # Replace the previously submitted file of the same name with the new upload
            new = result.get("newfile", {})
            res = self.session.post(
                f"{self.base_url}/repository/repository_ajax.php?action=overwrite",
                data={**common, "repo_id": repo_id, "existingfilename": path.name, "existingfilepath": "/",
                      "newfilename": new.get("filename"), "newfilepath": new.get("filepath", "/")},
                timeout=30,
            )
            try:
                overwrite = res.json()
            except ValueError:
                overwrite = {"error": f"HTTP {res.status_code}"}
            if overwrite.get("error"):
                raise LMSError(f"Could not replace existing file '{path.name}': {overwrite['error']}")
        return path.name

    def submit_assignment(self, assign_id: int, file_path: str, finalize: bool = False) -> Dict[str, Any]:
        """Uploads a file to an assignment and saves the submission.

        Other files already in the submission are kept; a file with the same name is replaced.
        finalize=True is required to submit for grading: it presses "Submit assignment" on draft-based
        assignments, and is needed at all on assignments where saving is itself the final submission.
        It accepts the submission statement if there is one. That step usually cannot be undone.
        """
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Step 1: Open the edit submission page
        edit_url = f"{self.base_url}/mod/assign/view.php?id={assign_id}&action=editsubmission"
        res = self._get_page(edit_url)
        soup = BeautifulSoup(res.text, "html.parser")

        form = next((f for f in soup.select("form.mform") if f.find("input", {"name": "action", "value": "savesubmission"})), None)
        if not form:
            notices = self._page_errors(soup) or [e.get_text(" ", strip=True) for e in soup.select(".alert, .box.generalbox")][:2]
            raise LMSError("Submissions are not open for this assignment" + (f": {notices[0]}" if notices else ""))

        # Step 2: The file manager's draft area id lives in the hidden *_filemanager input
        fm_input = form.find("input", {"name": re.compile(r"_filemanager$")})
        if not fm_input or not fm_input.get("value"):
            raise LMSError("This assignment does not accept file submissions")
        draft_itemid = fm_input["value"]

        # Without drafts, "Save changes" IS the final submission and carries the submission
        # statement. Check this before uploading anything.
        statement_input = form.find("input", {"name": "submissionstatement", "type": "checkbox"})
        if statement_input and not finalize:
            label = form.find("label", {"for": statement_input.get("id")}) if statement_input.get("id") else None
            statement = label.get_text(" ", strip=True) if label else ""
            raise LMSError(
                "Nothing uploaded. This assignment submits for grading as soon as it is saved and requires "
                f"accepting the submission statement{f': {statement!r}' if statement else ''}. "
                "Call again with finalize=true once the user has confirmed."
            )

        # Step 3: Upload file into the draft area
        self._upload_to_draft(res.text, draft_itemid, path)

        # Step 4: Save the submission form
        form_data = self._form_fields(form)
        if statement_input:
            form_data["submissionstatement"] = statement_input.get("value") or "1"
        form_data["submitbutton"] = "Save changes"
        form_action = urllib.parse.urljoin(edit_url, form.get("action") or edit_url)
        save_res = self.session.post(form_action, data=form_data, timeout=60)
        save_soup = BeautifulSoup(save_res.text, "html.parser")
        if save_soup.find("input", {"name": "action", "value": "savesubmission"}):
            errors = self._page_errors(save_soup)
            raise LMSError("LMS did not accept the submission" + (f": {'; '.join(errors)}" if errors else ""))

        # Step 5: Optionally submit for grading
        finalized = bool(statement_input)
        if finalize and not finalized:
            details = self.get_assignment_details(assign_id)
            if details["can_submit_for_grading"]:
                confirm_url = f"{self.base_url}/mod/assign/view.php?id={assign_id}&action=submit"
                confirm_soup = BeautifulSoup(self._get_page(confirm_url).text, "html.parser")
                confirm_form = next((f for f in confirm_soup.select("form.mform") if f.find("input", {"name": "action", "value": "confirmsubmit"})), None)
                if not confirm_form:
                    raise LMSError("Saved as draft, but the LMS did not show the submit-for-grading confirmation")
                confirm_data = self._form_fields(confirm_form)
                if confirm_form.find("input", {"name": "submissionstatement"}):
                    confirm_data["submissionstatement"] = "1"
                confirm_data["submitbutton"] = "Continue"
                confirm_res = self.session.post(urllib.parse.urljoin(confirm_url, confirm_form.get("action") or confirm_url), data=confirm_data, timeout=60)
                confirm_res_soup = BeautifulSoup(confirm_res.text, "html.parser")
                if confirm_res_soup.find("input", {"name": "action", "value": "confirmsubmit"}):
                    errors = self._page_errors(confirm_res_soup)
                    raise LMSError("Saved as draft, but submit for grading failed" + (f": {'; '.join(errors)}" if errors else ""))
                finalized = True

        # Step 6: Verify against the assignment page
        details = self.get_assignment_details(assign_id)
        uploaded = any(f["name"] == path.name or _filename_from_url(f["url"]) == path.name for f in details["submitted_files"])
        if not uploaded:
            raise LMSError(f"Submission saved, but '{path.name}' is not listed among the submitted files (status: {details['status']})")

        if details["submitted"]:
            message = f"'{path.name}' is submitted for grading."
        elif details["can_submit_for_grading"]:
            message = (f"'{path.name}' is saved as a DRAFT, not yet submitted for grading. "
                       "Call again with finalize=true to submit it.")
        else:
            message = f"'{path.name}' is saved (status: {details['status']})."

        return {
            "status": "success",
            "assignment_id": assign_id,
            "filename": path.name,
            "submission_status": details["status"],
            "submitted_for_grading": details["submitted"],
            "finalized_now": finalized,
            "submitted_files": [f["name"] for f in details["submitted_files"]],
            "message": message
        }
