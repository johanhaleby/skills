#!/usr/bin/env python3
"""Turn an Egon.io story back into the spec that story2egn.py reads.

The generator only runs forwards, spec to picture, so a story drawn by hand has
no spec and cannot be marked with `"changed": true` to show where a change lands.
This closes that direction for the stories somebody already drew.

A person at egon.io can put one icon on both an actor and a work object, which story2egn
refuses, so such a story converts cleanly and then fails to render. The error names the icon
and the actor, and changing either one in the converted spec is the fix.

    python3 egn2spec.py "Send Message.dst" > "Send Message.json"
    python3 egn2spec.py --out-dir specs *.egn

Writes to stdout, or to --out-dir. Convert, mark the spec, render the marked copy
somewhere temporary, and leave the committed story where it is: regenerating a
hand-drawn story redraws its layout, and a picture somebody arranged by hand is
worth more than a tidy round trip.

Standard library only. Parsing, including the legacy string-wrapped .dst files,
is egn-to-text's.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "egn-to-text" / "scripts"))
import egn2text  # noqa: E402

SCOPE_LINE = re.compile(
    r"^(?P<time>AS-IS|TO-BE)\s*·\s*(?P<gran>[a-z-]+)\s*·\s*(?P<purity>[a-z-]+)\s*$",
    re.IGNORECASE,
)

# One-word prepositions, so a drawn one-word relation such as "notifies" is not taken
# for one.
PREPOSITIONS = {"about", "against", "at", "by", "for", "from", "in", "into", "of", "on",
                "onto", "over", "through", "to", "via", "with"}


def icon_for(kind):
    """`actorPerson` is a Person, `workObjectCredit-Card` is a Credit-Card."""
    for prefix in ("actorPerson", "actor", "workObject"):
        if kind.startswith(prefix) and prefix != "actorPerson":
            return kind[len(prefix):] or "Document"
    if kind == "actorPerson":
        return "Person"
    return kind


def slug(name, taken):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    taken.add(candidate)
    return candidate


def notes_by_shape(elements):
    """Egon links a note to its shape with a connection, so the note lands on the
    sentence that drew that shape rather than on the first one sharing its name."""
    text = {e["id"]: (e.get("text") or e.get("name") or "").strip()
            for e in elements if e.get("type", "").endswith("textAnnotation")}
    out = {}
    for e in elements:
        if e.get("type") == "domainStory:connection":
            note = text.get(e.get("target"))
            if note:
                out[e.get("source")] = note
    return out


def convert(path):
    elements, _meta = egn2text.load(path)
    data = egn2text.extract(path)
    notes = notes_by_shape(elements)
    warnings = []

    spec = {"title": data.get("title") or path.stem}

    description = (data.get("description") or "").strip()
    lines = [line for line in description.splitlines() if line.strip()]
    if lines:
        match = SCOPE_LINE.match(lines[0].strip())
        if match:
            spec["scope"] = {
                "granularity": match.group("gran"),
                "time": match.group("time").upper(),
                "purity": match.group("purity"),
            }
            lines = lines[1:]
        if lines:
            spec["description"] = " ".join(line.strip() for line in lines)


    # A three-part sentence is two arrows in the model: the numbered one carries
    # the verb, the unnumbered one leaving the same work object carries the
    # preposition. Two candidates means the drawing is ambiguous, so say so and
    # keep the sentence rather than guess which arrow was meant.
    by_source = {}
    for relation in data["relations"]:
        by_source.setdefault(relation["source"]["id"], []).append(relation)

    numbered_targets = {step["target"]["id"] for step in data["steps"]}
    # How many work objects point at each shape. The file does not say which of several
    # arrows is a sentence's own, so the first sentence to reach a shape takes it as a hop
    # and says so when the choice was not the only one.
    reached_by = {}
    for relation in data["relations"]:
        if not relation["source"]["kind"].startswith("actor"):
            reached_by.setdefault(relation["target"]["id"], set()).add(relation["source"]["id"])
    # A generated id may not equal an actor's name, which the generator rejects.
    ids = {p["name"] for p in data["participants"] if p["kind"].startswith("actor")}
    consumed, sentences = set(), []

    def eligible_hops(shape, visited=()):
        return [c for c in by_source.get(shape["id"], []) if id(c) not in consumed
                and c["target"]["id"] not in visited
                and not c["target"]["kind"].startswith("actor")
                and c["target"]["id"] not in numbered_targets and (c["target"]["name"] or "").strip()]

    def leaves_for_actor(shape):
        return any(c["target"]["kind"].startswith("actor")
                   for c in by_source.get(shape["id"], []) if id(c) not in consumed)

    def leads_to_actor(shape, seen):
        if shape["id"] in seen:
            return False
        if leaves_for_actor(shape):
            return True
        hops = eligible_hops(shape, seen | {shape["id"]})
        return len(hops) == 1 and leads_to_actor(hops[0]["target"], seen | {shape["id"]})

    # A chain hop is a shape a sentence draws, so a relation can point at it as well.
    hop_shapes = []
    for step in data["steps"]:
        target = step["target"]
        if not (target["name"] or "").strip() or not (step["source"]["name"] or "").strip():
            warnings.append(f"dropped sentence {step['number']}, which has an unnamed shape")
            continue
        sentence = {
            "n": step["number"],
            "from": step["source"]["name"],
            "verb": step["verb"],
            "object": {"name": target["name"], "icon": icon_for(target["kind"])},
        }
        # A sentence can pass through further work objects before it reaches an actor, as
        # in "purchases a ticket based on data from the property from the landlord". A hop
        # is a work object no numbered arrow points at, reached by the only arrow that
        # leaves the previous one.
        last = target
        # A hop another sentence already imported stays that sentence's, so an arrow
        # reaching it is left for the relation pass.
        own = {target["id"]}
        visited = own | {shape_id for _, shape_id in hop_shapes}
        while True:
            # An arrow back to a shape the sentence already passed is a relation, so it does not
            # count against the one forward hop.
            hops = eligible_hops(last, visited)
            # An arrow to an actor ends the chain, unless the single hop still leads to one,
            # in which case that arrow is a relation somebody drew and the relation pass keeps it.
            if len(hops) != 1 or (leaves_for_actor(last) and not leads_to_actor(hops[0]["target"], visited)):
                break
            hop = hops[0]["target"]
            visited.add(hop["id"])
            own.add(hop["id"])
            link = {"object": {"name": hop["name"], "icon": icon_for(hop["kind"])}}
            if hops[0]["label"]:
                link["prep"] = hops[0]["label"]
            sentence.setdefault("chain", []).append(link)
            hop_shapes.append((link["object"], hop["id"]))
            consumed.add(id(hops[0]))
            if hop["id"] in notes:
                warnings.append(f"dropped the note on {hop['name']!r} in sentence "
                                f"{step['number']}, since a note belongs to the sentence's first work object")
            last = hop
        for hop_id in own - {target["id"]}:
            if reached_by.get(hop_id, set()) - own:
                name = next(o["name"] for o, shape in hop_shapes if shape == hop_id)
                warnings.append(f"sentence {step['number']} took {name!r} as a hop, but another work "
                                f"object outside the sentence points at it, so check which sentence it belongs to")
        # The `to` of a sentence is an actor, so an arrow leaving the work object
        # for another work object is a relation somebody drew, not a preposition.
        candidates = [c for c in by_source.get(last["id"], [])
                      if c["target"]["kind"].startswith("actor")]
        # The .egn stores a preposition arrow and a drawn relation the same way, so this
        # guesses from the label. A single arrow labelled with a plain preposition, among
        # others that read as phrases like "is raised by", is taken as the sentence's own.
        if len(candidates) > 1:
            plain = [c for c in candidates if (c["verb"] or "").strip().lower() in PREPOSITIONS]
            if len(plain) == 1:
                candidates = plain
        if len(candidates) == 1:
            sentence["prep"] = candidates[0]["verb"] or "to"
            sentence["to"] = candidates[0]["target"]["name"]
            consumed.add(id(candidates[0]))
        elif len(candidates) > 1:
            warnings.append(
                f"sentence {step['number']} leaves {last['name']!r} by "
                f"{len(candidates)} arrows, so it kept no preposition"
            )
        # Sentences that start at one work object share what leaves it, and the first sentence takes
        # it. A later one that finds it already taken keeps nothing after the object, so say so.
        if "chain" not in sentence and "to" not in sentence and any(
                id(c) in consumed for c in by_source.get(target["id"], [])):
            warnings.append(f"sentence {step['number']} starts at {target['name']!r}, which an earlier "
                            f"sentence already continued from, so it kept no chain or destination")
        if target["id"] in notes:
            sentence["note"] = notes[target["id"]]
        sentences.append((sentence, target["id"]))

    spec["sentences"] = [sentence for sentence, _ in sentences]

    icons = {p["name"]: icon_for(p["kind"]) for p in data["participants"]
             if p["kind"].startswith("actor")}
    used = set()
    for sentence, _ in sentences:
        used.add(sentence["from"])
        if sentence.get("to"):
            used.add(sentence["to"])
    # First appearance rather than alphabetical, so the list reads in the order
    # the story introduces them. An actor no sentence names is a shape somebody
    # drew and never joined to the story, and the generator rejects it.
    order = []
    for sentence, _ in sentences:
        for name in (sentence["from"], sentence.get("to")):
            if name and name in used and name not in order:
                order.append(name)
    for name in icons:
        if name not in used:
            warnings.append(f"dropped the actor {name!r}, which no sentence names")
    spec["actors"] = [{"name": name, "icon": icons[name]}
                      for name in order if name in icons]


    # Only a work object a relation or a group points at needs an id.
    shape_ids = {}

    def reference(node):
        if node["kind"].startswith("actor"):
            return node["name"]
        if node["id"] not in shape_ids:
            shape_ids[node["id"]] = slug(node["name"], ids)
            # Sentences can share one drawn shape, but an id names one work object in the
            # spec, so the first sentence or hop to draw it takes the id and the rest stay bare.
            drawn_as = [sentence["object"] for sentence, target_id in sentences if target_id == node["id"]]
            drawn_as += [spec_object for spec_object, shape_id in hop_shapes if shape_id == node["id"]]
            if drawn_as:
                drawn_as[0]["id"] = shape_ids[node["id"]]
        return shape_ids[node["id"]]

    drawn = {target_id for _, target_id in sentences} | {shape_id for _, shape_id in hop_shapes}

    declared = {actor["name"] for actor in spec["actors"]}

    def expressible(node):
        # An actor is addressed by name, and only one the spec declares, since the
        # actors no sentence names were dropped above. A work object only exists in
        # the spec as some sentence's object, so one that no sentence draws has
        # nothing to point at and the relation has to go.
        if node["kind"].startswith("actor"):
            return node["name"] in declared
        return node["id"] in drawn

    leftovers = []
    for relation in data["relations"]:
        if id(relation) in consumed:
            continue
        if not (expressible(relation["source"]) and expressible(relation["target"])):
            warnings.append(
                f"dropped the relation {relation['source']['name']!r} to "
                f"{relation['target']['name']!r}, which no sentence draws"
            )
            continue
        entry = {"from": reference(relation["source"]), "to": reference(relation["target"])}
        if relation["verb"]:
            entry["verb"] = relation["verb"]
        leftovers.append(entry)
    if leftovers:
        spec["relations"] = leftovers

    # A group member is an actor name or a work object id, so a member the story
    # never drew has nothing to point at.
    known = {actor["name"] for actor in spec["actors"]} | set(shape_ids.values())
    by_name = {}
    for sentence, target_id in sentences:
        by_name.setdefault(sentence["object"]["name"], target_id)

    groups = []
    for group in data.get("groups") or []:
        if not isinstance(group, dict):
            continue
        name = group.get("name")
        members = []
        for member in group.get("members") or []:
            if member in known:
                members.append(member)
            elif member in by_name:
                members.append(reference({"kind": "workObject", "name": member,
                                          "id": by_name[member]}))
            else:
                warnings.append(f"dropped {member!r} from the group {name!r}, which no sentence draws")
        if name and members:
            groups.append({"name": name, "members": members})
        elif name:
            warnings.append(f"dropped the group {name!r}, which kept no members")
    if groups:
        spec["groups"] = groups

    return spec, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Turn an Egon.io story into a story2egn spec.")
    parser.add_argument("stories", nargs="+", type=Path)
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="write <title>.json here instead of printing")
    args = parser.parse_args(argv)

    failed = False
    for path in args.stories:
        try:
            spec, warnings = convert(path)
        except (OSError, ValueError, KeyError) as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            failed = True
            continue
        for warning in warnings:
            print(f"warning: {path.name}: {warning}", file=sys.stderr)
        text = json.dumps(spec, indent=2, ensure_ascii=False)
        if args.out_dir:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            out = args.out_dir / f"{spec['title']}.json"
            out.write_text(text + "\n", encoding="utf-8")
            print(out)
        else:
            print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
