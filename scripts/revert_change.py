#!/usr/bin/env python3
"""
Undo any single change made to this repository.

Every change in CLAUDE_CHANGES.md carries an ID (C001, C002, ...). This tool
takes that ID and reverses it, either by reverting the commit that introduced it
or by telling you the runtime flag that switches it off without touching code.

    python scripts/revert_change.py list                  # what can be reverted
    python scripts/revert_change.py show C012             # what it changed
    python scripts/revert_change.py revert C012 --dry-run # rehearse it
    python scripts/revert_change.py revert C012           # do it
    python scripts/revert_change.py verify                # registry vs git

Nothing here rewrites history. A revert is a new commit that undoes an old one,
so the record of what happened stays intact and the revert itself can be
reverted. Your own uncommitted work is never touched: the tool refuses to run
against a dirty tree unless you pass --allow-dirty.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "CHANGES_REGISTRY.json"
DOC_PATH = ROOT / "CLAUDE_CHANGES.md"

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise SystemExit(f"{RED}git {' '.join(args)} failed:{RESET}\n{result.stderr}")
    return result.stdout.strip()


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        raise SystemExit(f"{RED}No registry at {REGISTRY_PATH}{RESET}")
    return json.loads(REGISTRY_PATH.read_text())


def find_change(registry: dict, change_id: str) -> dict:
    wanted = change_id.upper()
    for change in registry["changes"]:
        if change["id"].upper() == wanted:
            return change
    known = ", ".join(c["id"] for c in registry["changes"]) or "(none yet)"
    raise SystemExit(f"{RED}Unknown change {change_id}{RESET}\nKnown: {known}")


def working_tree_is_clean() -> bool:
    return git("status", "--porcelain") == ""


# ─────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────
def cmd_list(args) -> int:
    registry = load_registry()
    changes = registry["changes"]
    if not changes:
        print("No changes recorded yet.")
        return 0

    print(f"\n{BOLD}Reversible changes{RESET}\n")
    print(f"  {'ID':<6} {'HOW':<6} {'COMMIT':<9} TITLE")
    print(f"  {'-'*6} {'-'*6} {'-'*9} {'-'*50}")
    for change in changes:
        commit = (change.get("commit") or "")[:8] or "-"
        how = change.get("reversible", "git")
        colour = GREEN if how in ("flag", "both") else (YELLOW if how == "manual" else "")
        print(f"  {change['id']:<6} {colour}{how:<6}{RESET} {commit:<9} {change['title']}")

    flagged = [c for c in changes if c.get("flag")]
    if flagged:
        print(f"\n{BOLD}Switchable at runtime — no code change needed{RESET}\n")
        for change in flagged:
            print(f"  {change['id']}  {change['flag']}=false   {DIM}{change['title']}{RESET}")
    print()
    return 0


def cmd_show(args) -> int:
    change = find_change(load_registry(), args.change_id)

    print(f"\n{BOLD}{change['id']} — {change['title']}{RESET}\n")
    print(f"  Reversible by : {change.get('reversible', 'git')}")
    if change.get("flag"):
        print(f"  Runtime flag  : {change['flag']}")
    print(f"  Commit        : {change.get('commit') or '(not yet committed)'}")
    print(f"  Tag           : {change.get('tag') or '-'}")
    if change.get("risk"):
        print(f"  {YELLOW}If reverted   : {change['risk']}{RESET}")

    files = change.get("files") or []
    if files:
        print(f"\n  {BOLD}Files{RESET}")
        for path in files:
            print(f"    {path}")

    commit = change.get("commit")
    if commit:
        print(f"\n  {BOLD}Diff summary{RESET}")
        stat = git("show", "--stat", "--oneline", commit, check=False)
        for line in stat.splitlines()[1:]:
            print(f"    {line}")
    print()
    return 0


def cmd_revert(args) -> int:
    change = find_change(load_registry(), args.change_id)
    commit = change.get("commit")

    if change.get("reversible") == "manual":
        print(f"\n{YELLOW}{change['id']} has no commit of its own.{RESET}")
        if change.get("risk"):
            print(f"  {RED}{change['risk']}{RESET}")
        print(f"\n  {BOLD}Undo it by hand:{RESET}")
        for step in change.get("manual_steps", []):
            print(f"    {step}")
        print(f"\n  {DIM}Not run automatically — this one is destructive.{RESET}\n")
        return 0

    if change.get("reversible") == "flag":
        print(
            f"\n{YELLOW}{change['id']} is reversed by configuration, not by git.{RESET}\n"
        )
        print(f"  Set {BOLD}{change['flag']}=false{RESET} in your .env and restart the API.")
        print(f"  {DIM}No code changes, no redeploy of the codebase.{RESET}\n")
        return 0

    if not commit:
        raise SystemExit(f"{RED}{change['id']} has no commit recorded yet.{RESET}")

    if not working_tree_is_clean() and not args.allow_dirty:
        raise SystemExit(
            f"{RED}Working tree has uncommitted changes.{RESET}\n"
            "Commit or stash them first, or pass --allow-dirty if you are sure."
        )

    print(f"\n{BOLD}Reverting {change['id']} — {change['title']}{RESET}")
    print(f"  commit {commit}")
    if change.get("risk"):
        print(f"  {YELLOW}{change['risk']}{RESET}")

    if args.dry_run:
        print(f"\n{DIM}Dry run — nothing changed. This would run:{RESET}")
        print(f"  git revert --no-edit {commit}\n")
        print(f"{DIM}Files it would touch:{RESET}")
        for line in git("show", "--stat", "--oneline", commit, check=False).splitlines()[1:]:
            print(f"  {line}")
        print()
        return 0

    result = subprocess.run(
        ["git", "-C", str(ROOT), "revert", "--no-edit", commit],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"\n{RED}Revert did not apply cleanly.{RESET}")
        print(result.stderr or result.stdout)
        print(
            f"\n{DIM}Resolve the conflicts, then 'git revert --continue',"
            f" or abandon with 'git revert --abort'.{RESET}\n"
        )
        return 1

    print(f"\n{GREEN}Reverted.{RESET} New commit: {git('rev-parse', '--short', 'HEAD')}")
    if change.get("flag"):
        print(
            f"{DIM}This change also had a runtime flag ({change['flag']}); "
            f"the revert removes it entirely.{RESET}"
        )
    print(f"\n{DIM}To undo this revert: git revert --no-edit HEAD{RESET}\n")
    return 0


def cmd_verify(args) -> int:
    """Check the registry against git and the narrative document."""
    registry = load_registry()
    problems: list[str] = []
    doc = DOC_PATH.read_text() if DOC_PATH.exists() else ""

    seen: set[str] = set()
    for change in registry["changes"]:
        cid = change["id"]

        if cid in seen:
            problems.append(f"{cid}: duplicate id in registry")
        seen.add(cid)

        commit = change.get("commit")
        if commit:
            if git("cat-file", "-t", commit, check=False) != "commit":
                problems.append(f"{cid}: commit {commit} not found in this repository")
        elif change.get("reversible") not in ("flag", "manual"):
            problems.append(
                f"{cid}: no commit recorded, and not reversible by flag or manual steps"
            )

        if change.get("reversible") == "manual" and not change.get("manual_steps"):
            problems.append(f"{cid}: marked manual but lists no steps")

        tag = change.get("tag")
        if tag and git("tag", "-l", tag, check=False) != tag:
            problems.append(f"{cid}: tag '{tag}' missing")

        if doc and f"#### {cid} " not in doc:
            problems.append(f"{cid}: no matching section in CLAUDE_CHANGES.md")

        for path in change.get("files") or []:
            # A deleted file is a legitimate outcome of a change, so absence is
            # only reported, never treated as a failure.
            if not (ROOT / path).exists():
                print(f"{DIM}  note: {cid} references {path}, which no longer exists{RESET}")

    if problems:
        print(f"\n{RED}Registry problems:{RESET}")
        for problem in problems:
            print(f"  - {problem}")
        print()
        return 1

    print(f"\n{GREEN}Registry is consistent.{RESET} {len(seen)} changes recorded.\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show and reverse individual changes to StockLens.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list every reversible change")

    show = sub.add_parser("show", help="show what one change did")
    show.add_argument("change_id")

    revert = sub.add_parser("revert", help="reverse one change")
    revert.add_argument("change_id")
    revert.add_argument("--dry-run", action="store_true", help="rehearse without changing anything")
    revert.add_argument("--allow-dirty", action="store_true", help="proceed despite uncommitted work")

    sub.add_parser("verify", help="check the registry against git and the changelog")

    args = parser.parse_args()
    return {
        "list": cmd_list,
        "show": cmd_show,
        "revert": cmd_revert,
        "verify": cmd_verify,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
