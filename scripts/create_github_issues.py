#!/usr/bin/env python3
"""Create the project's GitHub labels, milestones and issues from issues.yaml.

Uses the GitHub CLI (`gh`), so you must be logged in first:

    gh auth login

Usage:

    python scripts/create_github_issues.py --dry-run          # show what would happen
    python scripts/create_github_issues.py                    # create everything
    python scripts/create_github_issues.py --assign-a @me --assign-b <colleague-login>

Safe to re-run: labels are updated in place, and existing milestones and
issues (matched by exact title) are skipped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

DEFAULT_REPO = "nmkearney/chatcde"
SPEC = Path(__file__).with_name("issues.yaml")


def gh(args: list[str], dry_run: bool) -> str:
    """Run a `gh` command, or print it when dry-running. Returns stdout."""
    if dry_run:
        print("DRY RUN: gh " + " ".join(args))
        return ""
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"gh {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def existing_titles(repo: str, kind: str) -> set[str]:
    """Titles of existing issues or milestones, so re-runs don't duplicate."""
    if kind == "issues":
        out = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "all",
                "--limit",
                "500",
                "--json",
                "title",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    else:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/milestones?state=all&per_page=100"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    return {item["title"] for item in json.loads(out or "[]")}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assign-a", help="GitHub login for track A issues (e.g. @me)")
    parser.add_argument("--assign-b", help="GitHub login for track B issues")
    args = parser.parse_args()

    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))

    for label in spec["labels"]:
        gh(
            [
                "label",
                "create",
                label["name"],
                "--repo",
                args.repo,
                "--force",
                "--color",
                label["color"],
                "--description",
                label["description"],
            ],
            args.dry_run,
        )

    have_ms = set() if args.dry_run else existing_titles(args.repo, "milestones")
    for title in spec["milestones"]:
        if title in have_ms:
            print(f"skip milestone (exists): {title}")
            continue
        gh(
            ["api", f"repos/{args.repo}/milestones", "-f", f"title={title}"],
            args.dry_run,
        )

    have_issues = set() if args.dry_run else existing_titles(args.repo, "issues")
    for issue in spec["issues"]:
        if issue["title"] in have_issues:
            print(f"skip issue (exists): {issue['title']}")
            continue
        cmd = [
            "issue",
            "create",
            "--repo",
            args.repo,
            "--title",
            issue["title"],
            "--body",
            issue["body"],
            "--milestone",
            issue["milestone"],
        ]
        for label in issue["labels"]:
            cmd += ["--label", label]
        if "track:A-data" in issue["labels"] and args.assign_a:
            cmd += ["--assignee", args.assign_a]
        if "track:B-assistant" in issue["labels"] and args.assign_b:
            cmd += ["--assignee", args.assign_b]
        url = gh(cmd, args.dry_run).strip()
        if not args.dry_run:
            print(f"created: {url}")


if __name__ == "__main__":
    main()
