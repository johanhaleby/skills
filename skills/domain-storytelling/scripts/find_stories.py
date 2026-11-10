#!/usr/bin/env python3
"""Find the domain stories a repository already has.

Discovery is mechanical, so it belongs in a script rather than in prose a
reviewer has to remember to follow. Judging which story matters is not
mechanical, so this prints every story with its title and actors and leaves that
call to the reader. Ranking by directory distance was tried and removed. In one
repository the payment stories and the property-owner stories were at the same
depth under one parent, so every story scored the same and the number only
looked useful.

    python3 find_stories.py [repo-root] [--json]

Stories are found anywhere in the tree, because the documentation directory has
no fixed name or depth. Some repositories use doc/, some use docs/, and some keep
their stories three levels down, in backend/web/doc for example.

Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SUFFIXES = (".egn.svg", ".egn", ".dst.svg", ".dst")

# Build output, dependencies, and an agent's own scratch space are not the
# repository's documentation. A story under .claude/ is a leftover, and linking
# one sends a reader somewhere that will not exist next week.
SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "out", "dist",
    ".gradle", ".idea", ".claude", "__pycache__", ".venv", "venv",
}

EGN2TEXT = Path(__file__).resolve().parents[2] / "egn-to-text" / "scripts" / "egn2text.py"


def find_story_files(root):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(SUFFIXES):
                hits.append(Path(dirpath) / name)
    return sorted(hits)


def describe(path):
    """Title, participants and step count, read through egn-to-text."""
    out = {"title": path.stem, "participants": [], "steps": None, "readable": False}
    if not EGN2TEXT.exists():
        return out
    try:
        proc = subprocess.run(
            [sys.executable, str(EGN2TEXT), "--json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return out
        data = json.loads(proc.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return out

    out["readable"] = True
    out["title"] = data.get("title") or path.stem
    steps = data.get("steps") or []
    out["steps"] = len(steps)
    names = []
    for step in steps:
        for end in ("source", "target"):
            node = step.get(end) or {}
            name = node.get("name")
            kind = node.get("kind") or ""
            if name and kind.startswith("actor") and name not in names:
                names.append(name)
    out["participants"] = names
    return out


def dedupe(paths):
    """A rendered story and its model are one story, so prefer the model."""
    by_stem = {}
    for p in paths:
        stem = p.name
        for suffix in SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        key = (p.parent, stem)
        current = by_stem.get(key)
        if current is None or len(p.name) < len(current.name):
            by_stem[key] = p
    return sorted(by_stem.values())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find domain stories already in a repository.")
    ap.add_argument("root", nargs="?", default=".", help="repository root (default: cwd)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    stories = dedupe(find_story_files(root))

    records = []
    for story in stories:
        info = describe(story)
        info["path"] = str(story.relative_to(root))
        records.append(info)

    if args.json:
        print(json.dumps({"root": str(root), "count": len(records), "stories": records}, indent=2))
        return 0

    if not records:
        print("No domain stories in this repository.")
        return 0

    print(f"{len(records)} domain story file(s) in {root}:\n")
    for r in records:
        print(f"- {r['path']}")
        print(f"    title: {r['title']}")
        if r["readable"]:
            who = ", ".join(r["participants"]) or "none named"
            print(f"    steps: {r['steps']}   actors: {who}")
        else:
            print("    could not be read by egn-to-text")
    print("\nLink an existing story rather than drawing a rival to it.")
    print("State what this search found even when it found nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
