#!/usr/bin/env python3
"""Convert Egon.io domain stories into compact text for an LLM.

Handles .egn, .egn.svg (embedded story), and legacy .dst / .dst.svg files.
Strips icon SVGs, waypoint geometry, colors and internal IDs.

Usage:
    python3 egn2text.py story.egn [more.egn ...]
    python3 egn2text.py --json story.egn     # structured output instead of prose
"""
import argparse
import html
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ACTIVITY = "domainStory:activity"
CONNECTION = "domainStory:connection"
ANNOTATION = "domainStory:textAnnotation"
GROUP = "domainStory:group"


def _decode_json(value, path: Path):
    """Decode JSON, including the string-wrapped payload used by legacy .dst."""
    try:
        value = json.loads(value) if isinstance(value, str) else value
        if isinstance(value, str):
            value = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid embedded JSON: {exc}") from exc
    return value


def _svg_payload(raw: str, path: Path):
    """Find a JSON story embedded in an Egon SVG without parsing icon markup."""
    # Egon writes the payload as `<!-- <DST> ... </DST> -->`, in capitals, so the
    # tag match has to be case insensitive.
    tag_pattern = re.compile(
        r"<(?P<tag>dst(?::[^>\s]+)?)(?:\s[^>]*)?>(?P<body>.*?)</(?P=tag)>", re.S | re.I
    )
    for match in tag_pattern.finditer(raw):
        candidate = html.unescape(match.group("body")).strip()
        try:
            return _decode_json(candidate, path)
        except ValueError:
            continue

    # Keep the fallback deliberately narrow: only try JSON-looking values that
    # advertise the domain-story payload, so icon attributes are not candidates.
    # Walk candidate braces outermost first, because the nearest `{` before the
    # marker is `"workObjects":{` inside the icon set, not the top of the story.
    marker = raw.find('"dst"')
    if marker >= 0:
        decoder = json.JSONDecoder()
        starts = [i for i, ch in enumerate(raw[:marker]) if ch == "{"]
        for start in starts:
            try:
                # raw_decode stops at the end of the value, so the trailing
                # `</DST> -->` does not have to be stripped first.
                value, _ = decoder.raw_decode(raw[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and "dst" in value:
                return value
    raise ValueError(f"{path.name}: no embedded domain story found in SVG")


def load(path: Path):
    """Return the flat element list plus a metadata dict."""
    raw = path.read_text(encoding="utf-8")
    doc = _svg_payload(raw, path) if raw.lstrip().startswith("<") else _decode_json(raw, path)

    outer_meta = doc if isinstance(doc, dict) else {}
    elements = doc["dst"] if isinstance(doc, dict) and "dst" in doc else doc
    elements = _decode_json(elements, path)
    if not isinstance(elements, list):
        raise ValueError(f"{path.name}: unexpected structure")

    meta = {key: outer_meta[key] for key in ("info", "title", "version") if key in outer_meta}
    for e in elements:
        if isinstance(e, dict) and "id" not in e and "type" not in e:
            meta.update(e)
    return [e for e in elements if isinstance(e, dict) and "type" in e], meta


def extract(path: Path) -> dict:
    elements, meta = load(path)
    by_id = {e["id"]: e for e in elements if "id" in e}

    def label(ref):
        el = by_id.get(ref, {}) if ref is not None else {}
        name = (el.get("name") or "").strip()
        kind = el.get("type", "unknown").removeprefix("domainStory:")
        return {"name": name, "kind": kind, "id": ref}

    def fmt(ref):
        l = label(ref)
        if l["name"]:
            return f"{l['name']} ({l['kind']})"
        if ref is None or ref == "":
            return "<unknown>"
        if ref not in by_id:
            return f"<missing ref: {ref}>"
        return f"<unnamed {l['kind']}>"

    def has_number(value):
        return value is not None and str(value).strip() != ""

    def number_key(value):
        try:
            return (0, Decimal(str(value)))
        except (InvalidOperation, ValueError):
            return (1, str(value))

    steps, relations, annotations, groups = [], [], [], []

    for e in elements:
        t = e["type"]
        if t == ACTIVITY:
            verb = (e.get("name") or "").strip() or "→"
            source, target = e.get("source"), e.get("target")
            item = {"number": e.get("number"), "source": label(source),
                    "verb": verb, "target": label(target),
                    "parallel": bool(e.get("multipleNumberAllowed")),
                    "text": f"{fmt(source)} {verb} {fmt(target)}"}
            (steps if has_number(e.get("number")) else relations).append(item)
        elif t == CONNECTION:
            src, tgt = by_id.get(e.get("source"), {}), by_id.get(e.get("target"), {})
            annotation, attached = None, None
            if src.get("type") == ANNOTATION:
                annotation, attached = src, tgt
            elif tgt.get("type") == ANNOTATION:
                annotation, attached = tgt, src
            if annotation is not None:
                note = (annotation.get("text") or annotation.get("name") or e.get("name") or "").strip()
                if note:
                    annotations.append({"on": fmt(attached.get("id")), "note": note})
        elif t == GROUP:
            group_id = e.get("id")
            child_ids = set(e.get("children") or e.get("members") or [])
            members = [fmt(i) for i, el in by_id.items()
                       if el.get("parent") == group_id
                       or el.get("parentId") == group_id
                       or i in child_ids]
            groups.append({"name": (e.get("name") or "<unnamed group>").strip(),
                           "members": members})

    steps.sort(key=lambda s: number_key(s["number"]))

    # Actors and work objects, deduplicated by name — Egon allows the same actor
    # to appear as several shapes in one story.
    participants = {}
    for e in elements:
        t = e["type"]
        if t.startswith("domainStory:actor") or t.startswith("domainStory:workObject"):
            key = ((e.get("name") or "").strip(), t.removeprefix("domainStory:"))
            participants[key] = participants.get(key, 0) + 1

    return {
        "title": meta.get("title") or path.name.removesuffix(".svg").removesuffix(".egn").removesuffix(".dst"),
        "description": meta.get("info", ""),
        "version": meta.get("version", "unknown"),
        "steps": steps,
        "relations": relations,
        "annotations": annotations,
        "groups": groups,
        "participants": [{"name": n or "<unnamed>", "kind": k, "shapes": c}
                         for (n, k), c in sorted(participants.items())],
    }


def render(data: dict) -> str:
    out = [f"# Domain story: {data['title']}"]
    if data["description"]:
        out.append(f"\n{data['description']}")
    out.append(f"\n(Egon format {data['version']})")

    out.append("\n## Participants")
    for p in data["participants"]:
        dup = f"  [appears as {p['shapes']} shapes]" if p["shapes"] > 1 else ""
        out.append(f"- {p['name']} — {p['kind']}{dup}")

    if data["groups"]:
        out.append("\n## Groups")
        for g in data["groups"]:
            out.append(f"- {g['name']}: {', '.join(g['members']) or '(empty)'}")

    out.append("\n## Story (read in order)")
    if data["steps"]:
        out += [f"{s['number']}. {s['text']}" +
                (" [parallel sequence number allowed]" if s["parallel"] else "")
                for s in data["steps"]]
    else:
        out.append("(no numbered activities)")

    if data["relations"]:
        out.append("\n## Unnumbered relations (structural context, not part of the sequence)")
        out += [f"- {r['text']}" for r in data["relations"]]

    if data["annotations"]:
        out.append("\n## Annotations")
        out += [f"- on {a['on']}: {a['note']}" for a in data["annotations"]]

    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", help="emit structured JSON")
    args = ap.parse_args()

    results = []
    had_errors = False
    for f in args.files:
        try:
            results.append(extract(f))
        except Exception as exc:  # keep going across a batch of stories
            had_errors = True
            print(f"ERROR {f}: {exc}", file=sys.stderr)

    if not results:
        sys.exit("No domain stories could be parsed.")

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
    else:
        print("\n\n---\n\n".join(render(r) for r in results))
    if had_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
