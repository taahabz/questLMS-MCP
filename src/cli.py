#!/usr/bin/env python3
"""QuestLMS Terminal CLI for manual testing and quick inspection."""

import sys
import getpass
import argparse
from client import NustLMSClient, SESSION_FILE, save_credentials, clear_credentials


def main():
    parser = argparse.ArgumentParser(description="QuestLMS Terminal Tool")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Credentials
    login_parser = subparsers.add_parser("login", help="Verify credentials and store them in your OS keychain")
    login_parser.add_argument("--username", type=str, help="LMS username (prompted if omitted)")
    subparsers.add_parser("logout", help="Remove stored credentials and cached session")

    # Status / login
    subparsers.add_parser("status", help="Check LMS login status and profile")

    # Deadlines
    dl_parser = subparsers.add_parser("deadlines", help="List upcoming assignment deadlines")
    dl_parser.add_argument("--limit", type=int, default=10, help="Number of upcoming deadlines to show (max 50)")
    dl_parser.add_argument("--no-overdue", action="store_true", help="Hide overdue items")

    # Courses
    courses_parser = subparsers.add_parser("courses", help="List enrolled courses (current semester)")
    courses_parser.add_argument("--all", action="store_true", help="Include past courses")

    # Course contents
    cc_parser = subparsers.add_parser("course", help="Inspect contents of a course")
    cc_parser.add_argument("id", type=int, help="Course ID")

    # Assignment details
    as_parser = subparsers.add_parser("assign", help="Inspect assignment details")
    as_parser.add_argument("id", type=int, help="Assignment ID")

    # Download manual
    dl_file_parser = subparsers.add_parser("download", help="Download assignment files/manuals")
    dl_file_parser.add_argument("id", type=int, help="Assignment ID")
    dl_file_parser.add_argument("--dest", type=str, default="./labs", help="Destination folder")

    # Submit
    sub_parser = subparsers.add_parser("submit", help="Submit a file to an assignment")
    sub_parser.add_argument("id", type=int, help="Assignment ID")
    sub_parser.add_argument("file", type=str, help="Path to solution file to submit")
    sub_parser.add_argument("--finalize", action="store_true", help="Also submit for grading (usually irreversible)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "login":
        username = args.username or input("LMS username: ").strip()
        password = getpass.getpass("LMS password: ")
        # Drop any cached session so the password is actually checked against the LMS
        SESSION_FILE.unlink(missing_ok=True)
        try:
            res = NustLMSClient(username, password).login()
        except Exception as e:
            print(f"❌ Login failed, nothing saved: {e}", file=sys.stderr)
            sys.exit(1)
        save_credentials(username, password)
        print(f"✅ Logged in as {res.get('user')}; credentials saved to your OS keychain.")
        return

    if args.command == "logout":
        clear_credentials()
        print("✅ Removed stored credentials and cached session.")
        return

    client = NustLMSClient()

    try:
        if args.command == "status":
            res = client.login()
            print(f"✅ Connected to NUST LMS as: {res.get('user')}")
            print(f"🔑 Session Key: {res.get('sesskey')}")

        elif args.command == "deadlines":
            deadlines = client.get_upcoming_deadlines(limit=args.limit, include_overdue=not args.no_overdue)
            print(f"\n⚡ Found {len(deadlines)} Pending Deadlines:")
            for d in deadlines:
                flag = "⚠️  OVERDUE " if d["overdue"] else ""
                print(f" • {flag}[{d['due_date']}] {d['name']}")
                print(f"   Course: {d['course_name']} ({d['course_code']})")
                if d.get("assign_id"):
                    print(f"   Assign ID: {d['assign_id']}")
                print(f"   Link:   {d['url'] or d['action_url']}\n")

        elif args.command == "courses":
            courses = client.get_enrolled_courses(include_past=args.all)
            print(f"\n📚 Enrolled Courses ({len(courses)}):")
            for c in courses:
                print(f" • [ID {c['id']}] {c['fullname']} ({c['shortname']})")

        elif args.command == "course":
            contents = client.get_course_contents(args.id)
            print(f"\n📖 {contents['title']}:")
            for sec in contents.get("sections", []):
                print(f"\n  {'↳ 📂' if sec.get('is_subsection') else '📁'} {sec['section_name']}")
                for act in sec.get("activities", []):
                    print(f"    - [{act['type']} {act['module_id']}] {act['name']} -> {act['url']}")

        elif args.command == "assign":
            details = client.get_assignment_details(args.id)
            print(f"\n📝 {details['title']}")
            print(f"   Status: {details['status']}  |  Grading: {details['grading_status']}")
            print(f"   Due:    {details['due_date']} ({details['time_remaining']})")
            if details.get("cutoff_date"):
                print(f"   Cut-off: {details['cutoff_date']}")
            if details["is_draft"]:
                hint = "" if details["can_submit_for_grading"] else " (LMS isn't offering a submit button: ask your instructor)"
                print(f"   ⚠️  Draft saved but NOT submitted for grading{hint}")
            description = details["description"]
            print(f"\n   Description:\n   {description[:300]}{'...' if len(description) > 300 else ''}")
            for label, key in (("Attached Files", "attached_files"), ("Your Submitted Files", "submitted_files"), ("Links", "links")):
                if details.get(key):
                    print(f"\n   {label}:")
                    for f in details[key]:
                        print(f"    * {f.get('name') or f.get('text')} ({f['url']})")

        elif args.command == "download":
            files = client.download_assignment_assets(args.id, args.dest)
            print(f"✅ Downloaded {len(files)} files to {args.dest}:")
            for f in files:
                print(f"  * {f}")

        elif args.command == "submit":
            res = client.submit_assignment(args.id, args.file, finalize=args.finalize)
            print(f"🚀 {res['message']}")
            print(f"   Files in submission: {', '.join(res['submitted_files'])}")

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
