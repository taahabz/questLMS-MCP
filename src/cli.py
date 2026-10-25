#!/usr/bin/env python3
"""QuestLMS Terminal CLI for manual testing and quick inspection."""

import sys
import json
import argparse
from client import NustLMSClient


def main():
    parser = argparse.ArgumentParser(description="QuestLMS Terminal Tool")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Status / login
    subparsers.add_parser("status", help="Check LMS login status and profile")

    # Deadlines
    dl_parser = subparsers.add_parser("deadlines", help="List upcoming assignment deadlines")
    dl_parser.add_argument("--limit", type=int, default=10, help="Number of deadlines to show")

    # Courses
    subparsers.add_parser("courses", help="List all enrolled courses")

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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = NustLMSClient()

    try:
        if args.command == "status":
            res = client.login()
            print(f"✅ Connected to NUST LMS as: {res.get('user')}")
            print(f"🔑 Session Key: {res.get('sesskey')}")

        elif args.command == "deadlines":
            deadlines = client.get_upcoming_deadlines(limit=args.limit)
            print(f"\n⚡ Found {len(deadlines)} Upcoming Deadlines:")
            for d in deadlines:
                print(f" • [{d['due_date']}] {d['name']}")
                print(f"   Course: {d['course_name']} ({d['course_code']})")
                print(f"   Link:   {d['action_url']}\n")

        elif args.command == "courses":
            courses = client.get_enrolled_courses()
            print(f"\n📚 Enrolled Courses ({len(courses)}):")
            for c in courses:
                print(f" • [ID {c['id']}] {c['fullname']} ({c['shortname']})")

        elif args.command == "course":
            contents = client.get_course_contents(args.id)
            print(f"\n📖 {contents['title']}:")
            for sec in contents.get("sections", []):
                print(f"\n  📁 {sec['section_name']}")
                for act in sec.get("activities", []):
                    print(f"    - [{act['type']}] {act['name']} -> {act['url']}")

        elif args.command == "assign":
            details = client.get_assignment_details(args.id)
            print(f"\n📝 {details['title']}")
            print(f"   Status: {details['status']}")
            print(f"   Due:    {details['due_date']} ({details['time_remaining']})")
            print(f"\n   Description:\n   {details['description'][:300]}...")
            if details.get("attached_files"):
                print("\n   Attached Files:")
                for f in details["attached_files"]:
                    print(f"    * {f['name']} ({f['url']})")

        elif args.command == "download":
            files = client.download_assignment_assets(args.id, args.dest)
            print(f"✅ Downloaded {len(files)} files to {args.dest}:")
            for f in files:
                print(f"  * {f}")

        elif args.command == "submit":
            res = client.submit_assignment(args.id, args.file)
            print(f"🚀 Submission result: {res['message']}")

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
