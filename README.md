# QuestLMS MCP Server ⚡
### Model Context Protocol (MCP) Server for NUST LMS (`lms.nust.edu.pk`)

Seamlessly connect **Claude Code** (Terminal CLI), **Claude Desktop**, Cursor, or any MCP-compatible AI agent directly to NUST Moodle.

Let Claude check pending deadlines, download lab manuals & starter archives, solve lab exercises, and upload completed submissions directly through your terminal.

---

## 🛠️ Available MCP Tools

Claude has direct access to the following 7 tools:

| Tool Name | Parameters | Description |
| :--- | :--- | :--- |
| `lms_check_session` | *None* | Verifies login session and student profile information. |
| `lms_get_deadlines` | `limit` (default: 15) | Pulls all upcoming assignment and lab deadlines sorted by urgency. |
| `lms_list_courses` | *None* | Lists all semester courses you are currently enrolled in. |
| `lms_get_course_contents` | `course_id` | Scrapes course sections for lecture slides, lab manuals, and links. |
| `lms_get_assignment` | `assign_id` | Retrieves rubric, description, submission status, and attached file links. |
| `lms_download_lab_manual` | `assign_id, target_dir` | Authenticates and downloads attached manuals/starter files to your local folder. |
| `lms_submit_lab` | `assign_id, file_path` | Uploads and saves your solution file directly to LMS. |

---

## ⚡ Quick Setup

### 1. Configure Credentials
Add your NUST LMS student credentials to `.env.local` in the project root:
```env
LMS_USERNAME=your_roll_number.dept
LMS_PASSWORD=your_lms_password
```
*(This file is automatically ignored by git and will never be shared).*

---

### 2. Connect to Claude Code (Terminal)

To add this MCP server to **Claude Code** in terminal, run:

```bash
claude mcp add nust-lms -- python3 /Users/taahabz/projects/Personal/questLMS/mcp-server/src/server.py
```
*(Or specify the venv Python binary: `/Users/taahabz/projects/Personal/questLMS/mcp-server/.venv/bin/python3`)*

---

### 3. Connect to Claude Desktop

Open your Claude Desktop configuration file:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Add the `nust-lms` server to your `mcpServers` object:

```json
{
  "mcpServers": {
    "nust-lms": {
      "command": "/Users/taahabz/projects/Personal/questLMS/mcp-server/.venv/bin/python3",
      "args": [
        "/Users/taahabz/projects/Personal/questLMS/mcp-server/src/server.py"
      ],
      "env": {
        "PYTHONPATH": "/Users/taahabz/projects/Personal/questLMS/mcp-server/src",
        "LMS_USERNAME": "your_roll_number.dept",
        "LMS_PASSWORD": "your_lms_password"
      }
    }
  }
}
```

Restart Claude Desktop, and you will see the hammer icon 🔨 with all 7 LMS tools active.

---

## 💬 Prompts to Try with Claude in Terminal

Once connected, you can give Claude high-level instructions:

- **Check deadlines**:
  > *"Claude, check my upcoming deadlines on NUST LMS."*

- **Download and inspect a lab**:
  > *"Download the lab manual and starter code for Assignment 2 into `./labs/lab2/` and summarize the requirements."*

- **Work on the lab**:
  > *"Solve Exercise 1 and Exercise 2 from the lab manual, implement the unit tests, and run them."*

- **Upload solution**:
  > *"Submit `./labs/lab2/solution.py` to LMS for assignment ID 29895."*

---

## 🧪 Testing with Standalone CLI

You can also test any action manually from your terminal without opening Claude:

```bash
# Check login status
./mcp-server/.venv/bin/python3 mcp-server/src/cli.py status

# View deadlines
./mcp-server/.venv/bin/python3 mcp-server/src/cli.py deadlines

# List enrolled courses
./mcp-server/.venv/bin/python3 mcp-server/src/cli.py courses

# Download lab files
./mcp-server/.venv/bin/python3 mcp-server/src/cli.py download 29895 --dest ./labs/lab7
```
