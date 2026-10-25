"""QuestLMS MCP Server for Claude Code, Claude Desktop, and AI Coding Agents."""

import functools
from typing import Optional, List, Dict, Any
import requests
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from client import NustLMSClient, LMSError

server = MCPServer("questlms-mcp")
_client: Optional[NustLMSClient] = None


def lms_errors(fn):
    """Report expected failures (bad credentials, closed submissions, network) to the model with their message."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (LMSError, PermissionError, ValueError, FileNotFoundError) as e:
            raise ToolError(str(e)) from e
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            hint = " (check the ID: it may not exist or you may not have access)" if status in (403, 404) else ""
            raise ToolError(f"NUST LMS returned HTTP {status} for {e.request.url if e.request else 'the request'}{hint}") from e
        except requests.RequestException as e:
            raise ToolError(f"Could not reach NUST LMS: {e}") from e
    return wrapper


def get_client() -> NustLMSClient:
    global _client
    if _client is None:
        _client = NustLMSClient()
    return _client


@server.tool()
def lms_check_session() -> Dict[str, Any]:
    """Check the status of your NUST LMS session and view your active student profile.

    Returns:
        JSON object containing session status, user name, and sesskey.
    """
    client = get_client()
    try:
        res = client.login()
        return {
            "status": "connected",
            "user": res.get("user"),
            "sesskey": res.get("sesskey"),
            "cached": res.get("cached", False)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "hint": "Run `.venv/bin/python3 src/cli.py login` in the questLMS-MCP folder to store credentials in your OS keychain, then reconnect the MCP server"
        }


@server.tool()
@lms_errors
def lms_get_deadlines(limit: int = 15, include_overdue: bool = True) -> List[Dict[str, Any]]:
    """Fetch pending NUST LMS deadlines (assignments, labs, quizzes) that still need action: overdue first, then upcoming by due date.

    Items already submitted for grading are not listed. Drafts that were never submitted may not appear either;
    use lms_get_assignment to check an assignment's exact status.

    Args:
        limit: Maximum number of upcoming (not yet due) events, 1-50 (default: 15).
        include_overdue: Also include past-due items that are still pending (default: true).

    Returns:
        Events with name, course, due_date text, due_iso, overdue flag, assign_id (for lms_get_assignment), and LMS links.
    """
    client = get_client()
    return client.get_upcoming_deadlines(limit=limit, include_overdue=include_overdue)


@server.tool()
@lms_errors
def lms_list_courses(include_past: bool = False) -> List[Dict[str, Any]]:
    """List the courses the student is enrolled in on NUST LMS (current semester by default).

    Args:
        include_past: Also include finished/past courses (default: false).

    Returns:
        List of courses with course IDs, course names, short codes, and completion progress.
    """
    client = get_client()
    return client.get_enrolled_courses(include_past=include_past)


@server.tool()
@lms_errors
def lms_get_course_contents(course_id: int) -> Dict[str, Any]:
    """Retrieve all sections, lecture slides, lab manuals, and assignments inside a specific course.

    Args:
        course_id: The numerical ID of the course on LMS (e.g., 1858).

    Returns:
        Detailed list of sections and their associated modules and resource URLs.
    """
    client = get_client()
    return client.get_course_contents(course_id)


@server.tool()
@lms_errors
def lms_get_assignment(assign_id: int) -> Dict[str, Any]:
    """Fetch full assignment details, submission status, due date, description text, and attached file links.

    Args:
        assign_id: The numerical ID of the assignment module (from the LMS URL `mod/assign/view.php?id=...`).

    Returns:
        Title, description, opened/due/cut-off dates, submission and grading status, `submitted` flag,
        whether it can still be edited or submitted for grading, instructor `attached_files`,
        your `submitted_files`, and external links from the description.
    """
    client = get_client()
    return client.get_assignment_details(assign_id)


@server.tool()
@lms_errors
def lms_download_lab_manual(assign_id: int, target_dir: str = "./labs") -> Dict[str, Any]:
    """Download the instructor's attached files (lab manuals, starter code, rubrics) for an assignment.

    Args:
        assign_id: The numerical ID of the assignment module.
        target_dir: Directory to save into. Prefer an absolute path: relative paths resolve against
            the MCP server's working directory (default: './labs').

    Returns:
        Status summary with absolute paths of all downloaded files.
    """
    client = get_client()
    saved_files = client.download_assignment_assets(assign_id, target_dir)
    return {
        "status": "success",
        "assignment_id": assign_id,
        "count": len(saved_files),
        "downloaded_files": saved_files
    }


@server.tool()
@lms_errors
def lms_submit_lab(assign_id: int, file_path: str, finalize: bool = False) -> Dict[str, Any]:
    """Upload a solution file (e.g. .py, .pdf, .zip, .ipynb) to a NUST LMS assignment and save the submission.

    Other files already in the submission are kept; a file with the same name is replaced.
    Many assignments then hold the submission as a DRAFT until it is submitted for grading. Others
    submit for grading as soon as they are saved; for those the tool refuses (uploading nothing)
    unless finalize=true.
    Only pass finalize=true after the user has explicitly confirmed: it submits for grading,
    accepts the submission statement on their behalf, and usually cannot be undone.

    Args:
        assign_id: The numerical ID of the assignment module.
        file_path: Absolute path to the solution file to upload.
        finalize: Also submit for grading (default: false, leaves a draft).

    Returns:
        Verified submission status, files now in the submission, and whether it is submitted for grading.
    """
    client = get_client()
    return client.submit_assignment(assign_id, file_path, finalize=finalize)


if __name__ == "__main__":
    server.run()
