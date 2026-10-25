"""QuestLMS MCP Server for Claude Code, Claude Desktop, and AI Coding Agents."""

import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from mcp.server.mcpserver import MCPServer
from client import NustLMSClient

server = MCPServer("questlms-mcp")
_client: Optional[NustLMSClient] = None


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
            "hint": "Ensure LMS_USERNAME and LMS_PASSWORD are set in your environment or in .env.local"
        }


@server.tool()
def lms_get_deadlines(limit: int = 15) -> List[Dict[str, Any]]:
    """Fetch all upcoming NUST LMS assignment deadlines, lab submissions, and quizzes sorted by urgency.

    Args:
        limit: Maximum number of upcoming events to retrieve (default: 15).

    Returns:
        A list of upcoming events with assignment names, due dates, course codes, and direct LMS links.
    """
    client = get_client()
    return client.get_upcoming_deadlines(limit=limit)


@server.tool()
def lms_list_courses() -> List[Dict[str, Any]]:
    """List all semester courses the student is currently enrolled in on NUST LMS.

    Returns:
        List of courses with course IDs, course names, short codes, and completion progress.
    """
    client = get_client()
    return client.get_enrolled_courses()


@server.tool()
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
def lms_get_assignment(assign_id: int) -> Dict[str, Any]:
    """Fetch full assignment details, submission status, due date, description text, and attached file links.

    Args:
        assign_id: The numerical ID of the assignment module (from the LMS URL `mod/assign/view.php?id=...`).

    Returns:
        Assignment details, grading status, description, and list of attached lab manuals or starter files.
    """
    client = get_client()
    return client.get_assignment_details(assign_id)


@server.tool()
def lms_download_lab_manual(assign_id: int, target_dir: str = "./labs") -> Dict[str, Any]:
    """Download all attached lab manuals, starter codes, or rubric files for an assignment directly into your workspace.

    Args:
        assign_id: The numerical ID of the assignment module.
        target_dir: Local directory path where files should be downloaded (default: './labs').

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
def lms_submit_lab(assign_id: int, file_path: str) -> Dict[str, Any]:
    """Upload and submit a completed lab solution file (e.g. .py, .pdf, .zip, .ipynb) to NUST LMS.

    Args:
        assign_id: The numerical ID of the assignment module.
        file_path: Local filesystem path to the solution file to upload.

    Returns:
        Submission status and confirmation from LMS.
    """
    client = get_client()
    return client.submit_assignment(assign_id, file_path)


if __name__ == "__main__":
    server.run()
