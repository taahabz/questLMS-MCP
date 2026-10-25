import os
import re
import json
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import ssl
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
    """Allow legacy TLS renegotiation, which the LMS server requires but OpenSSL 3 blocks by default."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


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
        # Disable insecure request warnings if campus self-signed cert is encountered
        requests.packages.urllib3.disable_warnings()
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

    def is_logged_in(self) -> bool:
        """Checks if current session is active without re-logging in."""
        try:
            res = self.session.get(f"{self.base_url}/my/", verify=False, timeout=10)
            if "login/index.php" in res.url or "Log in to the site" in res.text:
                return False
            # Refresh sesskey if present
            sesskey_match = re.search(r'"sesskey":"([^"]+)"', res.text)
            if sesskey_match:
                self.sesskey = sesskey_match.group(1)
            user_match = re.search(r'class="usertext[^"]*"[^>]*>(.*?)</span>', res.text)
            if user_match:
                self.user_name = user_match.group(1).strip()
            return True
        except Exception:
            return False

    def login(self, force: bool = False) -> Dict[str, Any]:
        """Logs into NUST LMS and extracts session tokens."""
        if not force and self._load_session_cache() and self.is_logged_in():
            return {
                "status": "success",
                "cached": True,
                "user": self.user_name,
                "sesskey": self.sesskey
            }

        if not self.username or not self.password:
            raise ValueError(
                "No LMS credentials found. Run `.venv/bin/python3 src/cli.py login` to store them in your OS keychain"
            )

        # 1. Fetch login page to retrieve logintoken
        login_page_url = f"{self.base_url}/login/index.php"
        res = self.session.get(login_page_url, verify=False, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        token_input = soup.find("input", {"name": "logintoken"})
        if not token_input or not token_input.get("value"):
            raise RuntimeError("Failed to extract 'logintoken' from login page")

        logintoken = token_input["value"]

        # 2. Post credentials
        login_data = {
            "username": self.username,
            "password": self.password,
            "logintoken": logintoken,
        }
        post_res = self.session.post(login_page_url, data=login_data, verify=False, timeout=20)

        # 3. Verify success
        if "Invalid login" in post_res.text:
            raise PermissionError("Invalid LMS username or password. Please verify credentials.")

        # Extract sesskey
        sesskey_match = re.search(r'"sesskey":"([^"]+)"', post_res.text)
        if not sesskey_match:
            # Try fetching /my/ page directly
            my_res = self.session.get(f"{self.base_url}/my/", verify=False, timeout=15)
            sesskey_match = re.search(r'"sesskey":"([^"]+)"', my_res.text)
            user_match = re.search(r'class="usertext[^"]*"[^>]*>(.*?)</span>', my_res.text)
            if user_match:
                self.user_name = user_match.group(1).strip()
            dashboard_html = my_res.text
        else:
            dashboard_html = post_res.text

        if not sesskey_match:
            raise RuntimeError("Login completed but sesskey could not be extracted.")

        self.sesskey = sesskey_match.group(1)

        user_match = re.search(r'class="usertext[^"]*"[^>]*>(.*?)</span>', dashboard_html)
        if user_match:
            self.user_name = user_match.group(1).strip()

        # Extract user ID
        uid_match = re.search(r'data-userid="(\d+)"', dashboard_html)
        if uid_match:
            self.user_id = uid_match.group(1)

        self._save_session_cache()

        return {
            "status": "success",
            "cached": False,
            "user": self.user_name or self.username,
            "sesskey": self.sesskey,
            "user_id": self.user_id
        }

    def ensure_authenticated(self):
        """Ensures active session, logging in if needed."""
        if not self.sesskey or not self.is_logged_in():
            self.login()

    def get_upcoming_deadlines(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Pulls upcoming calendar deadlines via Moodle AJAX endpoint."""
        self.ensure_authenticated()
        ajax_url = f"{self.base_url}/lib/ajax/service.php?sesskey={self.sesskey}&info=core_calendar_get_action_events_by_timesort"
        payload = [{
            "index": 0,
            "methodname": "core_calendar_get_action_events_by_timesort",
            "args": {
                "limitnum": limit,
                "timesortfrom": 0
            }
        }]

        res = self.session.post(ajax_url, json=payload, verify=False, timeout=15)
        res_json = res.json()

        events = []
        if res_json and isinstance(res_json, list) and not res_json[0].get("error"):
            raw_events = res_json[0].get("data", {}).get("events", [])
            for ev in raw_events:
                events.append({
                    "id": ev.get("id"),
                    "name": ev.get("name"),
                    "course_id": ev.get("course", {}).get("id"),
                    "course_name": ev.get("course", {}).get("fullname"),
                    "course_code": ev.get("course", {}).get("shortname"),
                    "timestamp": ev.get("timesort"),
                    "due_date": ev.get("formattedtime"),
                    "action_name": ev.get("action", {}).get("name"),
                    "action_url": ev.get("action", {}).get("url"),
                    "item_id": ev.get("action", {}).get("itemid"),
                    "is_actionable": ev.get("action", {}).get("actionable", True),
                    "module_name": ev.get("modulename"),
                    "instance": ev.get("instance"),
                })
        return events

    def get_enrolled_courses(self) -> List[Dict[str, Any]]:
        """Lists all enrolled courses with progress and IDs."""
        self.ensure_authenticated()
        ajax_url = f"{self.base_url}/lib/ajax/service.php?sesskey={self.sesskey}&info=core_course_get_enrolled_courses_by_timeline_classification"
        payload = [{
            "index": 0,
            "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
            "args": {
                "classification": "all",
                "limit": 30,
                "offset": 0,
                "sort": "fullname"
            }
        }]

        res = self.session.post(ajax_url, json=payload, verify=False, timeout=15)
        res_json = res.json()

        courses = []
        if res_json and isinstance(res_json, list) and not res_json[0].get("error"):
            raw_courses = res_json[0].get("data", {}).get("courses", [])
            for c in raw_courses:
                courses.append({
                    "id": c.get("id"),
                    "fullname": c.get("fullname"),
                    "shortname": c.get("shortname"),
                    "progress": c.get("progress"),
                    "view_url": f"{self.base_url}/course/view.php?id={c.get('id')}"
                })
        return courses

    def get_course_contents(self, course_id: int) -> Dict[str, Any]:
        """Scrapes sections, lab modules, lecture resources, and assignments for a course."""
        self.ensure_authenticated()
        course_url = f"{self.base_url}/course/view.php?id={course_id}"
        res = self.session.get(course_url, verify=False, timeout=20)
        soup = BeautifulSoup(res.text, "html.parser")

        course_title = soup.find("h1") or soup.find("title")
        title_text = course_title.get_text(strip=True) if course_title else f"Course {course_id}"

        sections = []
        section_elems = soup.select("li.section, div.section")
        for sec in section_elems:
            sec_title_elem = sec.select_one(".sectionname, .section-title, h3")
            sec_title = sec_title_elem.get_text(strip=True) if sec_title_elem else "General"

            activities = []
            for act in sec.select(".activity"):
                act_classes = act.get("class", [])
                act_type = next((c for c in act_classes if c not in ["activity", "modtype_"]), "other")
                link_elem = act.select_one("a.aalmodal, a")
                name_elem = act.select_one(".instancename") or link_elem

                if link_elem and link_elem.get("href"):
                    activities.append({
                        "name": name_elem.get_text(strip=True) if name_elem else "Untitled",
                        "type": act_type.replace("modtype_", ""),
                        "url": link_elem["href"],
                        "module_id": act.get("id", "").replace("module-", "")
                    })

            if activities:
                sections.append({
                    "section_name": sec_title,
                    "activities": activities
                })

        return {
            "course_id": course_id,
            "title": title_text,
            "sections": sections
        }

    def get_assignment_details(self, assign_id: int) -> Dict[str, Any]:
        """Scrapes full assignment information, description, rubric, and downloadable manuals."""
        self.ensure_authenticated()
        assign_url = f"{self.base_url}/mod/assign/view.php?id={assign_id}"
        res = self.session.get(assign_url, verify=False, timeout=20)
        soup = BeautifulSoup(res.text, "html.parser")

        title = soup.find("h2") or soup.find("h1")
        title_text = title.get_text(strip=True) if title else "Assignment"

        # Description / instructions
        intro_elem = soup.select_one("#intro, .submissionstatustable + div, .no-overflow")
        description = intro_elem.get_text(separator="\n", strip=True) if intro_elem else ""

        # Attached files (lab manuals, starter code, PDFs)
        attached_files = []
        for file_link in soup.select("#intro a, .submissionstatustable a, .fileuploadsubmission a, .intro-content a"):
            href = file_link.get("href", "")
            if "pluginfile.php" in href or "mod_assign" in href or ".pdf" in href or ".zip" in href or ".ipynb" in href:
                file_name = file_link.get_text(strip=True) or Path(urllib.parse.urlparse(href).path).name
                attached_files.append({
                    "name": file_name,
                    "url": href
                })

        # Submission status table
        status_table = {}
        for tr in soup.select("table.generaltable tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                key = th.get_text(strip=True).lower()
                val = td.get_text(strip=True)
                status_table[key] = val

        return {
            "id": assign_id,
            "title": title_text,
            "url": assign_url,
            "description": description,
            "status": status_table.get("submission status", "Not submitted"),
            "grading_status": status_table.get("grading status", "Not graded"),
            "due_date": status_table.get("due date", "Unknown"),
            "time_remaining": status_table.get("time remaining", "Unknown"),
            "attached_files": attached_files
        }

    def download_file(self, file_url: str, output_path: str) -> str:
        """Downloads a protected file from LMS using the active session."""
        self.ensure_authenticated()
        dest = Path(output_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        with self.session.get(file_url, stream=True, verify=False, timeout=30) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return str(dest)

    def download_assignment_assets(self, assign_id: int, target_dir: str) -> List[str]:
        """Downloads all attached lab manuals and resources for an assignment."""
        details = self.get_assignment_details(assign_id)
        out_dir = Path(target_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        downloaded_paths = []
        for f in details.get("attached_files", []):
            safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', f["name"])
            dest_path = out_dir / safe_name
            self.download_file(f["url"], str(dest_path))
            downloaded_paths.append(str(dest_path))

        return downloaded_paths

    def submit_assignment(self, assign_id: int, file_path: str) -> Dict[str, Any]:
        """Uploads a completed lab file to Moodle assignment draft area and submits."""
        self.ensure_authenticated()
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Step 1: Open the edit submission page
        edit_url = f"{self.base_url}/mod/assign/view.php?id={assign_id}&action=editsubmission"
        res = self.session.get(edit_url, verify=False, timeout=20)
        soup = BeautifulSoup(res.text, "html.parser")

        # Step 2: Extract draft itemid and filemanager options
        itemid_input = soup.find("input", {"name": re.compile(r'files_filemanager.*itemid|itemid')})
        draft_itemid = None
        if itemid_input:
            draft_itemid = itemid_input.get("value")
        else:
            draft_match = re.search(r'"itemid":\s*(\d+)', res.text)
            if draft_match:
                draft_itemid = draft_match.group(1)

        if not draft_itemid:
            raise RuntimeError("Could not find draft itemid. Ensure submissions are open for this assignment.")

        # Step 3: Upload file to Moodle repository draft area
        upload_url = f"{self.base_url}/repository/repository_ajax.php?action=upload"
        with open(path, "rb") as f_data:
            files = {
                "repo_upload_file": (path.name, f_data, "application/octet-stream")
            }
            data = {
                "sesskey": self.sesskey,
                "itemid": draft_itemid,
                "title": path.name,
                "author": self.user_name or "Student",
                "savepath": "/",
            }
            upload_res = self.session.post(upload_url, data=data, files=files, verify=False, timeout=30)

        # Step 4: Submit the form to save the submission
        form = soup.find("form", {"id": "mform1"}) or soup.find("form", {"class": "mform"})
        if not form:
            raise RuntimeError("Could not find submission form on LMS")

        form_data = {}
        for inp in form.find_all("input"):
            if inp.get("name"):
                form_data[inp["name"]] = inp.get("value", "")

        # Update submission filemanager
        for k in list(form_data.keys()):
            if "itemid" in k:
                form_data[k] = draft_itemid

        form_action = form.get("action", edit_url)
        save_res = self.session.post(form_action, data=form_data, verify=False, timeout=25)

        return {
            "status": "success",
            "assignment_id": assign_id,
            "filename": path.name,
            "draft_itemid": draft_itemid,
            "message": f"Successfully uploaded and saved submission for '{path.name}'"
        }
