# QuestLMS MCP Server ⚡
### Connect Claude to NUST LMS (`lms.nust.edu.pk`)

An [MCP](https://modelcontextprotocol.io) server that lets **Claude Code**, **Claude Desktop**, Cursor, or any MCP-compatible agent read your NUST Moodle: check deadlines, see what you haven't submitted, download lab manuals, and upload solutions.

> **No hosting required.** The server runs locally on your own machine — Claude launches it in the background when needed — and logs in with *your* LMS account. Your password never leaves your computer except to log in to the LMS itself.

Companion project: [QuestLMS Chrome extension](https://github.com/taahabz/questLMS).

---

## ✅ Requirements

- **Python 3.10+** — check with `python3 --version`
- **Git**
- **Claude Code** ([install guide](https://docs.claude.com/en/docs/claude-code)) or Claude Desktop
- Your NUST LMS username (e.g. `yourname.bscs23seecs`) and password

---

## 🚀 Setup (about 2 minutes)

### 1. Clone the repo

```bash
git clone https://github.com/taahabz/questLMS-MCP.git
cd questLMS-MCP
```

### 2. Run the setup script

```bash
./setup.sh
```

It will:

1. Create a private Python environment in `.venv/` and install dependencies.
2. Ask for your LMS **username** and **password** (the password is hidden as you type), test them by logging in to the LMS, and — only if login succeeds — save them to your **operating system's keychain** (macOS Keychain, Windows Credential Locker, or Linux Secret Service). Nothing is written to a plain-text file.
3. Register the server with Claude Code as `nust-lms`.

<details>
<summary>Windows (no bash)?</summary>

Run these in PowerShell from the repo folder:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python src\cli.py login
claude mcp add nust-lms --scope user -- "$PWD\.venv\Scripts\python.exe" "$PWD\src\server.py"
```
</details>

### 3. Start using it

Open a **new** Claude Code session (or type `/mcp` in an existing one and reconnect `nust-lms`), then ask:

> *"What submissions do I have left on LMS?"*

To confirm it's connected, type `/mcp` — `nust-lms` should show as **connected** with 7 tools.

---

## 🖥️ Claude Desktop (optional)

Run `./setup.sh` first so your credentials are in the keychain. Then open the config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add the server, replacing `/path/to/questLMS-MCP` with where you cloned the repo (run `pwd` inside the folder to get it):

```json
{
  "mcpServers": {
    "nust-lms": {
      "command": "/path/to/questLMS-MCP/.venv/bin/python3",
      "args": ["/path/to/questLMS-MCP/src/server.py"]
    }
  }
}
```

On Windows use `.venv\\Scripts\\python.exe` and `src\\server.py`. Restart Claude Desktop; the LMS tools appear under the 🔨 icon.

---

## 🔐 Managing your credentials

| Task | Command |
| :--- | :--- |
| Change password / switch account | `.venv/bin/python3 src/cli.py login` |
| Remove credentials from this machine | `.venv/bin/python3 src/cli.py logout` |
| Check you're logged in | `.venv/bin/python3 src/cli.py status` |

After changing credentials, reconnect the server in Claude with `/mcp`.

**Where things are stored**
- Credentials: OS keychain, service name `questlms`.
- Login session cookies: `~/.cache/questlms/session.json`, readable only by your user account.

**No keychain available** (headless Linux, CI)? Set `LMS_USERNAME` and `LMS_PASSWORD` as environment variables, or copy `.env.example` to `.env.local` and fill it in. These take precedence over the keychain. `.env.local` is gitignored — never commit it.

---

## 🛠️ Available tools

| Tool | Parameters | What it does |
| :--- | :--- | :--- |
| `lms_check_session` | — | Verifies your login and shows your profile. |
| `lms_get_deadlines` | `limit` (default 15) | Upcoming assignment, lab, and quiz deadlines, most urgent first. |
| `lms_list_courses` | — | Courses you're enrolled in this semester. |
| `lms_get_course_contents` | `course_id` | Sections of a course: slides, lab manuals, links. |
| `lms_get_assignment` | `assign_id` | Description, due date, submission status, and attached files. |
| `lms_download_lab_manual` | `assign_id`, `target_dir` | Downloads an assignment's attached manuals/starter files. |
| `lms_submit_lab` | `assign_id`, `file_path` | Uploads your solution file to the assignment. |

`assign_id` is the number in an LMS link like `mod/assign/view.php?id=12345`.

> ⚠️ `lms_submit_lab` really submits to the LMS. Claude Code asks for permission before running tools; read what it's about to upload before approving.

---

## 💬 Things to ask Claude

- *"Check my upcoming deadlines on NUST LMS."*
- *"Which assignments haven't I submitted yet?"*
- *"Download the lab manual for assignment 12345 into `./labs/lab2/` and summarize the requirements."*
- *"Submit `./labs/lab2/solution.py` to assignment 12345."*

---

## 🧪 Command-line use (without Claude)

```bash
.venv/bin/python3 src/cli.py status
.venv/bin/python3 src/cli.py deadlines --limit 20
.venv/bin/python3 src/cli.py courses
.venv/bin/python3 src/cli.py course <course_id>
.venv/bin/python3 src/cli.py assign <assign_id>
.venv/bin/python3 src/cli.py download <assign_id> --dest ./labs/lab7
.venv/bin/python3 src/cli.py submit <assign_id> ./solution.zip
```

---

## 🩺 Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| `No LMS credentials found` | Run `.venv/bin/python3 src/cli.py login`, then `/mcp` → reconnect. |
| `Login failed` during setup | Check your username/password by logging in at lms.nust.edu.pk in a browser. |
| `nust-lms` shows *failed* in `/mcp` | Re-run `./setup.sh` — usually the repo folder was moved, which breaks the `.venv` path. |
| `SSL: UNSAFE_LEGACY_RENEGOTIATION_DISABLED` | You're on an old copy; `git pull`. The client handles the LMS's legacy TLS. |
| Can't reach the LMS at all | Some campus/VPN networks block it; try another network. |
| Stale or wrong account | `.venv/bin/python3 src/cli.py logout`, then `login` again. |

---

## 🔄 Updating

```bash
cd questLMS-MCP
git pull
.venv/bin/python3 -m pip install -r requirements.txt
```

Then `/mcp` → reconnect `nust-lms`.

## 🗑️ Uninstalling

```bash
.venv/bin/python3 src/cli.py logout     # removes keychain entries + session cache
claude mcp remove nust-lms --scope user
cd .. && rm -rf questLMS-MCP
```
