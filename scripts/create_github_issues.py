#!/usr/bin/env python3
"""Create the project's GitHub labels, milestones and issues from a YAML spec.

Uses the GitHub CLI (`gh`), so you must be logged in first:

    gh auth login

Usage:

    python scripts/create_github_issues.py --dry-run          # show what would happen
    python scripts/create_github_issues.py                    # create everything
    python scripts/create_github_issues.py --assign-a @me --assign-b <colleague-login>
    python scripts/create_github_issues.py --spec scripts/issues_phase2.yaml --dry-run

`scripts/issues.yaml` holds the coursework's original issues (two tracks);
`scripts/issues_phase2.yaml` the list after the coursework (decision 0033).

A spec has `labels`, `milestones` and `issues`, and optionally:
- `update`: changes to existing issues by number (milestone, title, body,
  add_labels, remove_labels, comment), so a reorganisation is reviewable too;
- `close_milestones`: milestone titles to close once their issues have moved.

Safe to re-run: labels are updated in place, and existing milestones and
issues (matched by exact title) are skipped. Updates are re-applied, which
is harmless except that a `comment` is posted again, so remove comments from
the spec once they are posted.
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
    parser.add_argument("--assign", help="GitHub login for every new issue (e.g. @me)")
    parser.add_argument(
        "--spec", type=Path, default=SPEC, help="YAML spec (default: issues.yaml)"
    )
    args = parser.parse_args()

    spec = yaml.safe_load(args.spec.read_text(encoding="utf-8"))

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
        if args.assign:
            cmd += ["--assignee", args.assign]
        url = gh(cmd, args.dry_run).strip()
        if not args.dry_run:
            print(f"created: {url}")

    for change in spec.get("update", []):
        update_issue(args.repo, change, args.dry_run)

    for title in spec.get("close_milestones", []):
        close_milestone(args.repo, title, args.dry_run)


def update_issue(repo: str, change: dict, dry_run: bool) -> None:
    """Apply one `update` entry to an existing issue.

    Changes the milestone, title, body and labels with one `gh issue edit`, then
    posts the comment, if any. Used to move coursework issues into the new
    milestones without retyping them.
    """
    number = str(change["number"])
    cmd = ["issue", "edit", number, "--repo", repo]
    if "milestone" in change:
        cmd += ["--milestone", change["milestone"]]
    if "title" in change:
        cmd += ["--title", change["title"]]
    if "body" in change:
        cmd += ["--body", change["body"]]
    for label in change.get("add_labels", []):
        cmd += ["--add-label", label]
    for label in change.get("remove_labels", []):
        cmd += ["--remove-label", label]
    if len(cmd) > 5:
        gh(cmd, dry_run)
    if change.get("comment"):
        gh(
            ["issue", "comment", number, "--repo", repo, "--body", change["comment"]],
            dry_run,
        )
    if not dry_run:
        print(f"updated: #{number}")


def close_milestone(repo: str, title: str, dry_run: bool) -> None:
    """Close the milestone with this title, if it exists and has no open issues.

    A milestone that still has open issues is left open with a message, so an
    issue that was not moved is not hidden.
    """
    if dry_run:
        print(f"DRY RUN: close milestone {title!r} if it has no open issues")
        return
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/milestones?state=open&per_page=100"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for ms in json.loads(out or "[]"):
        if ms["title"] != title:
            continue
        if ms["open_issues"]:
            print(f"keep milestone open ({ms['open_issues']} open issues): {title}")
            return
        gh(
            [
                "api",
                "-X",
                "PATCH",
                f"repos/{repo}/milestones/{ms['number']}",
                "-f",
                "state=closed",
            ],
            False,
        )
        print(f"closed milestone: {title}")
        return
    print(f"skip milestone (not open): {title}")


if __name__ == "__main__":
    main()
