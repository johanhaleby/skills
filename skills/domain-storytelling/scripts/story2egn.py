#!/usr/bin/env python3
"""Render a domain story spec into Egon.io files and an SVG picture.

Reads a compact JSON story spec and writes any of:
    .egn.svg  picture with the Egon model embedded (default)
    .svg      picture only
    .egn      Egon model only
    .png      best effort, needs rsvg-convert / cairosvg / inkscape, or macOS Quick Look
    .board.svg  picture for a page, every element tagged with its steps (default)

Usage:
    python3 story2egn.py story.json [more.json ...]
    python3 story2egn.py --format egn.svg,egn --out-dir docs story.json
    python3 story2egn.py --preview /tmp/look story.json   # plus a .png to look at

Standard library only. See references/spec.md for the spec schema.

Adding a layout check: a note box and its leader line can each land on a shape
or on an arrow, so cover all four pairs rather than the case in front of you.
Report a problem only when some placement could have avoided it, because a
check no author can fix is noise. Every piece of text needs a paper halo or an
opaque shape under it (node, group and edge labels carry a halo, sequence
numbers a filled circle, note text an opaque note, and the title block sits
above the drawing), so a new kind of text needs one of those four.
"""
import argparse
import datetime
import functools
import itertools
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zlib
from pathlib import Path

EGN_VERSION = "3.0.0"
ACCENT_EGN = "#c8341bff"    # Egon wants eight hex digits, not six
SHAPE = 76                  # Egon shape box, verified against real .egn files
HALF = SHAPE // 2
PIN_X, PIN_Y = 240, 190     # grid step for an explicit "at" override
BOW = 190                   # unit of sideways travel when an arrow has to dodge
GRID = 10                   # Egon snaps to a 10px grid
# The ring, the shapes and the reach between them are fixed pixels, so the type grows
# into whitespace that does not grow with it. Measured on the bundled stories, 1.25 costs
# 0.8% per dimension and 1.4 starts dropping labels onto shapes.
TYPE = 1.25
NAME_PT = round(13 * TYPE)  # a shape's name, a group's name, an arrow's verb
NOTE_PT = round(12 * TYPE)  # a note's body
LABEL_H = round(34 * TYPE)  # room under a shape for its name
NOTE_W = 210                # a note is a fixed width and grows downwards
NOTE_MIN_H = round(52 * TYPE)
NOTE_LINE_H = round(15 * TYPE)
NOTE_PAD_Y = round(26 * TYPE)   # the fold at the top plus a baseline of breathing room
NOTE_TOP = round(20 * TYPE)     # first baseline inside a note, the pair NOTE_PAD_Y is measured against
# A note wraps sooner as the type grows because its box is a hard 210px edge. A name is
# not held to its shape the same way, so wrap() keeps its own character budget and a long
# name is allowed to run wider than the icon above it.
NOTE_CHARS = int(34 / TYPE)     # characters per line in a 210px note, so it wraps sooner
CHAR_W = 6.6 * TYPE         # sans-serif average advance, for geometry checks only
BAND_CHAR_W = 6.6           # the title band keeps 13px, so it keeps the original advance
NAME_LINE_H = round(15 * TYPE)  # baseline to baseline for a two-line name
NAME_TOP = round(4 * TYPE)      # shape bottom to first baseline, the name's clearance from the icon
BADGE_ALONG = 0.16              # where a sequence number parks along its own arrow
SEATING_LIMIT = 8               # outer actors, past which the seatings outnumber what is worth ranking
TRIALS = 8                      # seatings worth drawing in full before keeping the one we have
CANVAS_PAD = 90                 # margin around the drawing, and the band's own left inset
COLUMN = 760.0                  # the width a review brief embeds a picture into
LEGIBLE = 7.0                   # the smallest a name may render at once it is force-fit there
BADGE_PT = round(12 * TYPE)     # the digit in the disc, sized with the disc and not with a note
BADGE_R = round(11 * TYPE)      # the disc a sequence number sits in
BADGE_BOX = 2 * BADGE_R + 4     # the room it needs, disc plus a hair of clearance
LABEL_BOX_H = round(20 * TYPE)  # room an arrow's verb needs above the line
LABEL_GAP = round(6 * TYPE)     # clearance between an arrow and a verb set beside it
LABEL_LIFT = round(7 * TYPE)    # verb baseline above the line, so the ink sits in the box reserved for it

ICONS_PATH = Path(__file__).resolve().parent / "icons.json"


class SpecError(Exception):
    pass


# --------------------------------------------------------------------------- spec


def _require(cond, msg):
    if not cond:
        raise SpecError(msg)


def load_spec(path: Path) -> dict:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path.name}: invalid JSON: {exc}") from exc
    _require(isinstance(spec, dict), f"{path.name}: top level must be an object")
    return spec


SPEC_KEYS = {"title", "scope", "description", "actors", "sentences", "relations", "groups"}
ACTOR_KEYS = {"name", "icon", "note", "at"}
SENTENCE_KEYS = {"n", "from", "verb", "object", "prep", "to", "note", "changed"}
OBJECT_KEYS = {"name", "icon", "id", "at"}
RELATION_KEYS = {"from", "to", "verb"}
GROUP_KEYS = {"name", "members"}
SCOPE_KEYS = {"granularity", "time", "purity"}


def _no_strays(obj, allowed, where):
    """A key the generator does not know is a key whose content never reaches the picture.

    A spec is hand-written and every field is optional-looking, so a plausible
    invention such as a top-level notes list reads as accepted and then draws
    nothing. Say so at the point the file is read rather than in the drawing.
    """
    stray = sorted(k for k in obj if k not in allowed)
    _require(not stray, f"{where}: unknown field{'s' if len(stray) > 1 else ''} "
                        f"{', '.join(repr(k) for k in stray)}. "
                        f"Known here: {', '.join(sorted(allowed))}")


def validate(spec: dict, icons: dict) -> None:
    _no_strays(spec, SPEC_KEYS, "story")
    if isinstance(spec.get("scope"), dict):
        _no_strays(spec["scope"], SCOPE_KEYS, "scope")
    _require(spec.get("title"), "title is required")
    actors = spec.get("actors") or []
    sentences = spec.get("sentences") or []
    _require(actors, "at least one actor is required")
    _require(sentences, "at least one sentence is required")

    seen = set()
    # First declarer wins, so a collision names one actor and names the same one every run.
    actor_icons = {}
    for a in actors:
        name = a.get("name")
        _require(name, "every actor needs a name")
        _no_strays(a, ACTOR_KEYS, f"actor {name!r}")
        _require(name not in seen, f"actor {name!r} is declared twice; actors appear once per story")
        seen.add(name)
        _require(a.get("icon") in icons, f"actor {name!r} uses unknown icon {a.get('icon')!r}")
        actor_icons.setdefault(a["icon"], name)

    wo_ids = {}
    for i, s in enumerate(sentences, 1):
        where = f"sentence {s.get('n', i)}"
        _no_strays(s, SENTENCE_KEYS, where)
        _require(s.get("from") in seen, f"{where}: 'from' names undeclared actor {s.get('from')!r}")
        if s.get("to") is not None:
            _require(s["to"] in seen, f"{where}: 'to' names undeclared actor {s['to']!r}")
        obj = s.get("object")
        if obj is not None:
            _no_strays(obj, OBJECT_KEYS, f"{where}: work object")
            _require(obj.get("name"), f"{where}: work object needs a name")
            _require(obj.get("icon") in icons, f"{where}: unknown icon {obj.get('icon')!r}")
            # The kind survives only as a type string in the .egn, so two shapes sharing an
            # icon are drawn identically and no reading of the picture tells them apart.
            owner = actor_icons.get(obj["icon"])
            _require(owner is None, f"{where}: work object {obj['name']!r} uses icon {obj['icon']!r}, "
                                    f"which actor {owner!r} already has, so one glyph would stand for "
                                    f"both kinds and the reader cannot tell them apart")
            oid = obj.get("id")
            if oid:
                _require(oid not in wo_ids, f"{where}: work object id {oid!r} is already used")
                _require(oid not in seen, f"{where}: work object id {oid!r} collides with an actor name")
                wo_ids[oid] = obj
        else:
            _require(s.get("to"), f"{where}: a sentence without a work object must still have a 'to'")

    used = {s.get("from") for s in sentences} | {s.get("to") for s in sentences}
    for name in seen:
        _require(name in used, f"actor {name!r} is declared but never used in a sentence")

    addressable = seen | set(wo_ids)
    for r in spec.get("relations") or []:
        _no_strays(r, RELATION_KEYS, f"relation {r.get('from')!r} -> {r.get('to')!r}")
        for end in ("from", "to"):
            _require(r.get(end) in addressable,
                     f"relation {r.get('from')!r} -> {r.get('to')!r}: {end} names "
                     f"{r.get(end)!r}, which is neither an actor nor a work object id")
    for g in spec.get("groups") or []:
        _no_strays(g, GROUP_KEYS, f"group {g.get('name')!r}")
        _require(g.get("name"), "every group needs a name")
        for m in g.get("members") or []:
            _require(m in addressable,
                     f"group {g['name']!r} has member {m!r}, which is neither an actor "
                     f"nor a work object id")


# --------------------------------------------------------------------------- model


class Node:
    def __init__(self, key, kind, name, icon, pinned=None, changed=False):
        self.key, self.kind, self.name, self.icon = key, kind, name, icon
        self.pinned = pinned
        self.changed = changed
        self.spoke = ()
        self.steps = set()      # numbers of the sentences that mention it, for the board's tags
        self.cx = self.cy = 0.0
        self.eid = None

    @property
    def box(self):
        return (self.cx - HALF, self.cy - HALF, SHAPE, SHAPE)

    @property
    def label_box(self):
        w = max(len(self.name) * CHAR_W, SHAPE)
        return (self.cx - w / 2, self.cy + HALF, w, LABEL_H)

    @property
    def text_box(self):
        """The box the name is drawn in, sized from its wrapped lines and not from the room label_box reserves."""
        lines = wrap(self.name)
        w = max(len(line) for line in lines) * CHAR_W
        return (self.cx - w / 2, self.cy + HALF + NAME_TOP - NAME_PT,
                w, NAME_PT * 1.3 + (len(lines) - 1) * NAME_LINE_H)


class Note:
    def __init__(self, key, text, anchor, steps=()):
        self.key, self.text, self.anchor = key, text, anchor
        self.steps = set(steps)
        self.cx = self.cy = 0.0
        self.eid = None
        self.leader = None
        # True when no clear route existed anywhere, so the crossing is the layout's
        # doing and not something the author of the story can move out of the way.
        self.leader_forced = False
        # Every line of the annotation is drawn. A truncated note is a note the
        # reader cannot act on, so the shape grows instead of the text shrinking.
        self.lines = wrap(text, width=NOTE_CHARS, lines=None)

    @property
    def name(self):
        return f"note on {self.anchor.name}"

    @property
    def height(self):
        return max(NOTE_MIN_H, len(self.lines) * NOTE_LINE_H + NOTE_PAD_Y)

    @property
    def box(self):
        return (self.cx - NOTE_W / 2, self.cy - self.height / 2, NOTE_W, self.height)


class Edge:
    def __init__(self, src, dst, label="", number=None, structural=False, changed=False, step=None):
        self.src, self.dst, self.label = src, dst, label
        self.step = step
        self.number, self.structural = number, structural
        self.changed = changed
        self.path = None
        self.label_at = self.number_at = None
        self.label_box = None


def build(spec):
    """Turn a validated spec into nodes, edges, notes and groups."""
    nodes, edges, notes = {}, [], []
    for a in spec["actors"]:
        nodes[a["name"]] = Node(a["name"], "actor", a["name"], a["icon"], a.get("at"))

    for i, s in enumerate(spec["sentences"]):
        n = s.get("n", i + 1)
        src = nodes[s["from"]]
        obj = s.get("object")
        changed = bool(s.get("changed"))
        src.steps.add(n)
        if s.get("to"):
            nodes[s["to"]].steps.add(n)
        if obj is None:
            edges.append(Edge(src, nodes[s["to"]], s.get("verb", ""), n, changed=changed, step=n))
            continue
        key = obj.get("id") or f"__wo{i}"
        wo = Node(key, "wo", obj["name"], obj["icon"], obj.get("at"), changed=changed)
        wo.spoke = tuple(sorted(k for k in (s["from"], s.get("to")) if k))
        wo.steps.add(n)
        nodes[key] = wo
        edges.append(Edge(src, wo, s.get("verb", ""), n, changed=changed, step=n))
        if s.get("to"):
            edges.append(Edge(wo, nodes[s["to"]], s.get("prep", ""), changed=changed, step=n))
        if s.get("note"):
            notes.append(Note(f"__note{len(notes)}", s["note"], wo, (n,)))

    for a in spec["actors"]:
        if a.get("note"):
            notes.append(Note(f"__note{len(notes)}", a["note"], nodes[a["name"]]))

    for r in spec.get("relations") or []:
        edges.append(Edge(nodes[r["from"]], nodes[r["to"]], r.get("verb", ""), structural=True))

    groups = [(g["name"], [nodes[m] for m in g.get("members") or []])
              for g in spec.get("groups") or []]
    return nodes, edges, notes, groups


# --------------------------------------------------------------------------- layout


def _snap(v):
    return int(round(v / GRID) * GRID)


RING_STEP = 300      # arc length reserved for each outer actor
SPOKE_SPREAD = 150   # gap between work objects sharing a spoke
SOLO_REACH = 190     # how far a work object with no second actor sits from its actor
STRANGER_REACH = 420 # how far an object may slide to get clear of an actor it has no sentence with
STRANGER_STEP = 20   # how far each slide moves it



def _owners(edges):
    """Which actors each work object shares a sentence with."""
    own = {}
    for e in edges:
        if e.src.kind == "actor" and e.dst.kind != "actor":
            own.setdefault(id(e.dst), set()).add(e.src)
        if e.dst.kind == "actor" and e.src.kind != "actor":
            own.setdefault(id(e.src), set()).add(e.dst)
    return own


def _clear_of_strangers(mx, my, px, py, ends, actors):
    """Slide a work object off a stranger's doorstep, along its own chord.

    A reader attributes a work object to whoever it sits nearest rather than to
    whoever the arrow comes from, and two actors facing each other across the ring
    have their midpoint in the middle of the ring, where the protagonist stands.
    Sliding along the chord's outward normal keeps the two arrows symmetric.
    """
    strangers = [a for a in actors if a not in ends]
    if not strangers:
        return mx, my

    def nearest(x, y, group):
        return min(math.hypot(x - a.cx, y - a.cy) for a in group)

    # Outwards, because that is the side with canvas to spare. Sliding the other way
    # cleared nothing extra when it was measured, so it is not worth the code.
    ux, uy = (px, py) if (mx * px + my * py) >= 0 else (-px, -py)
    landing = (mx, my)
    for step in range(1, int(STRANGER_REACH / STRANGER_STEP) + 1):
        x, y = mx + ux * step * STRANGER_STEP, my + uy * step * STRANGER_STEP
        if nearest(x, y, ends) <= nearest(x, y, strangers):
            return x, y
        landing = (x, y)

    # Two actors sitting exactly opposite have the stranger on their midpoint, and no
    # perpendicular slide reaches past that, because the stranger stays on the shorter
    # side of every right triangle the slide makes. Move along the chord towards the
    # nearer of the object's own actors instead, which always clears and which leans the
    # object towards one of the two actors it belongs to rather than towards a third.
    near = min(ends, key=lambda a: math.hypot(landing[0] - a.cx, landing[1] - a.cy))
    span = math.hypot(near.cx - landing[0], near.cy - landing[1]) or 1.0
    vx, vy = (near.cx - landing[0]) / span, (near.cy - landing[1]) / span
    for step in range(1, int(span / STRANGER_STEP) + 1):
        x, y = landing[0] + vx * step * STRANGER_STEP, landing[1] + vy * step * STRANGER_STEP
        if nearest(x, y, ends) <= nearest(x, y, strangers):
            return x, y
    return landing


def _cast(nodes, edges):
    """Who holds the middle, who sits round it, and which actors each work object joins.

    The seating search asks the same question the layout does, and two answers built from
    two different counts once put a work object's own actor in nobody's seat.
    """
    actors = [n for n in nodes.values() if n.kind == "actor"]
    work = [n for n in nodes.values() if n.kind == "wo"]
    partners, order = {}, {}
    for i, e in enumerate(edges):
        for a, b in ((e.src, e.dst), (e.dst, e.src)):
            if b.kind == "wo" and a.kind == "actor":
                partners.setdefault(b.key, [])
                if a.key not in partners[b.key]:
                    partners[b.key].append(a.key)
                order.setdefault(a.key, i)
    for a in actors:
        order.setdefault(a.key, len(edges))
    load = {a.key: sum(1 for w in work if a.key in partners.get(w.key, [])) for a in actors}
    hero = max(actors, key=lambda a: (load[a.key], -order[a.key]))
    return hero, sorted((a for a in actors if a is not hero), key=lambda a: order[a.key]), partners


def layout(nodes, edges, seating=None):
    """Protagonist in the middle, other actors around it, work objects on the spokes.

    This is the layout the book's own examples use: the cinema story puts the
    moviegoer at the centre, cashier, concession stand, usher and projectionist
    around the outside, and every ticket, snack and movie on the line between the
    two actors that sentence involves. Sequence is carried by the numbers, not by
    position, which is why a left-to-right band reads wrong.
    """
    actors = [n for n in nodes.values() if n.kind == "actor"]
    work = [n for n in nodes.values() if n.kind == "wo"]
    hero, others, partners = _cast(nodes, edges)

    hero.cx = hero.cy = 0.0
    n = max(len(others), 1)
    # The ring has to grow with the number of work objects too, not just the
    # number of actors, or a long story packs its spokes until nothing fits.
    radius = max(430.0, RING_STEP * n / (2 * math.pi), 130.0 + 34.0 * len(work))
    # Wider than tall, because diagrams get read on landscape screens and pages,
    # and offset by half a step so two actors do not stack straight above one another.
    rx, ry = radius * 1.35, radius * 0.82
    # Sequence is carried by the numbers, but the eye still starts on the left, so
    # the ring is turned to open the story there. Whoever speaks first takes the
    # leftmost seat and the ring is walked leftwards from it. When the protagonist
    # is the one who speaks first it already holds the middle, so sentence one has
    # to run outwards instead and the ring is walked the other way.
    numbered = [e for e in edges if e.number is not None]
    opener = min(numbered, key=lambda e: e.number).src if numbered else hero
    outwards = opener is hero
    turn = 1.0 if outwards else -1.0
    first_seat = 0.0 if outwards else math.pi
    # Half a step, so that with an even cast no two actors stack up the same
    # vertical line. One actor round the hub has nothing to stand clear of, and
    # tilting it there would only push it off the reading line.
    tilt = turn * (math.pi / n - math.pi / 2) if n > 1 else 0.0
    seats = [(rx * math.cos(first_seat + tilt + turn * 2 * math.pi * i / n),
              ry * math.sin(first_seat + tilt + turn * 2 * math.pi * i / n)) for i in range(n)]
    if seating:
        by_key = {a.key: a for a in others}
        # A seating naming anyone else would drop an actor on the floor and leave it wherever
        # the last draw put it, which is usually somebody else's seat.
        _require(sorted(seating) == sorted(by_key), "seating must name every outer actor once")
        others = [by_key[k] for k in seating]
    for a, (x, y) in zip(others, seats):
        a.cx, a.cy = x, y

    spokes = {}
    for w in work:
        keys = w.spoke or tuple(sorted(partners.get(w.key, [])))
        spokes.setdefault(keys, []).append(w)

    for keys, members in spokes.items():
        ends = [nodes[k] for k in keys if k in nodes]
        k = len(members)
        if len(ends) >= 2:
            ax, ay, bx, by = ends[0].cx, ends[0].cy, ends[1].cx, ends[1].cy
            mx, my = (ax + bx) / 2, (ay + by) / 2
            dx, dy = bx - ax, by - ay
            span = math.hypot(dx, dy) or 1.0
            px, py = -dy / span, dx / span      # perpendicular to the spoke
        elif ends:
            a = ends[0]
            span = math.hypot(a.cx, a.cy) or 1.0
            ux, uy = (a.cx / span, a.cy / span) if span > 1 else (0.0, -1.0)
            mx, my = a.cx + ux * SOLO_REACH, a.cy + uy * SOLO_REACH
            px, py = -uy, ux
        else:
            mx, my, px, py = 0.0, -radius - SOLO_REACH, 1.0, 0.0
        if len(ends) >= 2:
            mx, my = _clear_of_strangers(mx, my, px, py, ends, actors)
        for j, w in enumerate(members):
            off = (j - (k - 1) / 2) * SPOKE_SPREAD
            w.cx, w.cy = mx + px * off, my + py * off

    for n in nodes.values():
        if n.pinned:
            col, row = n.pinned
            n.cx, n.cy = col * PIN_X, row * PIN_Y

    _separate(nodes)
    for n in nodes.values():
        n.cx, n.cy = _snap(n.cx), _snap(n.cy)


def _footprint(node):
    """The shape plus the room its name needs underneath it."""
    w = max(len(node.name) * CHAR_W + 16, SHAPE)
    return (node.cx - w / 2, node.cy - HALF, w, SHAPE + LABEL_H)


def _separate(nodes, rounds=1200, damping=0.55):
    """Relax work objects apart until nothing collides.

    Displacements are accumulated over a whole pass and then applied together.
    Applying them one pair at a time makes two shapes take turns shoving each
    other and the loop never settles.
    """
    # A pin is the author's last resort after a warning, so nothing may shove it. An
    # overlap it causes is reported rather than quietly relaxed away.
    movable = [n for n in nodes.values() if n.kind == "wo" and not n.pinned]
    everything = list(nodes.values())
    for _ in range(rounds):
        push = {n.key: [0.0, 0.0] for n in movable}
        worst = 0.0
        for a in movable:
            ax, ay, aw, ah = _footprint(a)
            for b in everything:
                if b is a:
                    continue
                bx, by, bw, bh = _footprint(b)
                ox = min(ax + aw, bx + bw) - max(ax, bx)
                oy = min(ay + ah, by + bh) - max(ay, by)
                if ox <= 0 or oy <= 0:
                    continue
                worst = max(worst, min(ox, oy))
                dx, dy = a.cx - b.cx, a.cy - b.cy
                if dx == 0 and dy == 0:
                    angle = (zlib.crc32(a.key.encode()) % 360) * math.pi / 180
                    dx, dy = math.cos(angle), math.sin(angle)
                dist = math.hypot(dx, dy)
                force = min(ox, oy) / 2 + 5
                weight = 1.0 if b.kind == "actor" else 0.5
                push[a.key][0] += dx / dist * force * weight
                push[a.key][1] += dy / dist * force * weight
        if worst == 0.0:
            return
        for n in movable:
            n.cx += push[n.key][0] * damping
            n.cy += push[n.key][1] * damping


# Tried in order, so a note keeps its outward position when that position works
# and only swings round the anchor when it does not.
_NOTE_TURNS = tuple(
    t for pair in ((a, -a) for a in range(0, 181, 12)) for t in pair if -180 < t <= 180)


def _segments_cross(p1, p2, p3, p4):
    """Whether segment p1p2 crosses segment p3p4."""
    # Inlined rather than a nested helper, because layout calls this a few hundred thousand times
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = p1, p2, p3, p4
    if ((dx - cx) * (ay - cy) - (dy - cy) * (ax - cx) > 0) == ((dx - cx) * (by - cy) - (dy - cy) * (bx - cx) > 0):
        return False
    return ((bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0) != ((bx - ax) * (dy - ay) - (by - ay) * (dx - ax) > 0)


_LEADER_EXITS = tuple((math.cos(math.radians(d)), math.sin(math.radians(d)))
                      for d in range(0, 360, 30))


def _edge_points(e):
    """Sampled points along an arrow, with the box that contains them.

    Cached because the placement search asks thousands of times while a route is
    only sampled once per layout, and the box lets most arrows be dismissed
    without walking their segments at all.
    """
    cached = getattr(e, "_sampled", None)
    if cached is None or cached[0] is not e.path:
        pts = sample(e.path)
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        cached = (e.path, pts, (min(xs), min(ys), max(xs), max(ys)))
        e._sampled = cached
    return cached[1], cached[2]


def _boxes_apart(one, two):
    """Whether two min/max boxes cannot possibly touch."""
    ax0, ay0, ax1, ay1 = one
    bx0, by0, bx1, by1 = two
    return ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0


def _leader_shapes(anchor, cx, cy, rich=False):
    """The leader routes to try, cheapest and tidiest first.

    A straight line from the anchor's border is the tidiest and usually works. The
    centre is where every arrow touching the anchor converges, so starting there
    crosses them all and also draws the dashed line over the icon.

    When the anchor is hemmed in, the straight line and the two elbows all leave in
    the direction of the note and inherit whatever stands that way. The stub routes
    leave through a gap first and only then run to the note, which is what gets a
    leader out of a crowded middle.
    """
    a = border(anchor, cx, cy)
    b = (cx, cy)
    routes = [[a, b],
              [a, (b[0], a[1]), b],
              [a, (a[0], b[1]), b]]
    if not rich:
        return routes
    for ux, uy in _LEADER_EXITS:
        for reach in (SHAPE, SHAPE * 2):
            stub = (anchor.cx + ux * reach, anchor.cy + uy * reach)
            routes.append([border(anchor, *stub), stub, b])
    return routes


def _leader_blocked(pts, anchor, nodes, notes, edges=()):
    """Whether a leader route runs through anything but its own anchor.

    Four pairs have to be checked in all, because there are two kinds of thing to
    place and two kinds of obstacle. A note is a box and its leader is a line, and
    either can land on a shape or on an arrow. Three of the four were covered
    first, and the missing one put a leader straight across an arrow.
    """
    for start, end in zip(pts, pts[1:]):
        for i in range(1, 32):
            t = i / 32
            x = start[0] + (end[0] - start[0]) * t
            y = start[1] + (end[1] - start[1]) * t
            for other in nodes:
                if other is anchor:
                    continue
                for bx, by, bw, bh in (other.box, other.label_box):
                    if bx < x < bx + bw and by < y < by + bh:
                        return True
            for other in notes:
                bx, by, bw, bh = other.box
                if bx < x < bx + bw and by < y < by + bh:
                    return True

        seg_box = (min(start[0], end[0]), min(start[1], end[1]),
                   max(start[0], end[0]), max(start[1], end[1]))
        for e in edges:
            # An arrow that ends on the anchor is crossed by definition, because the
            # leader leaves from the anchor's border and the arrow stops there too.
            if e.path is None or e.src is anchor or e.dst is anchor:
                continue
            epts, ebox = _edge_points(e)
            if _boxes_apart(seg_box, ebox):
                continue
            for a, b in zip(epts, epts[1:]):
                if _segments_cross(start, end, a, b):
                    return True
    return False


def _clear_leader(anchor, cx, cy, nodes, notes, edges=(), rich=False):
    """The first leader route with nothing in its way, or None."""
    for pts in _leader_shapes(anchor, cx, cy, rich):
        if not _leader_blocked(pts, anchor, nodes, notes, edges):
            return pts
    return None


def _note_on_edge(box, edges):
    """Whether a note box has an arrow running under it."""
    bx, by, bw, bh = box
    for e in edges:
        if e.path is None:
            continue
        epts, ebox = _edge_points(e)
        if _boxes_apart((bx, by, bx + bw, by + bh), ebox):
            continue
        for x, y in epts:
            if bx < x < bx + bw and by < y < by + bh:
                return True
    return False


# Enough near candidates to find a stub route when one exists. They are collected
# in outward scan order, so trying them in order keeps a note close to its anchor.
_RICH_CANDIDATES = 250


def _search(note, nodes, shapes, others, edges):
    """Scan outward for a position, allowing only a straight or single-elbow leader.

    Returns the first position that needs nothing further, the fallbacks for when
    there is none, and the near positions worth retrying with a stub route.
    """
    a = note.anchor
    span = math.hypot(a.cx, a.cy)
    base = math.atan2(a.cy, a.cx) if span > 1 else math.pi / 2
    clear = off_arrows = boxes_only = None
    candidates = []
    for step in range(0, 1500, 50):
        for turn in _NOTE_TURNS:
            ang = base + math.radians(turn)
            note.cx = a.cx + math.cos(ang) * (SOLO_REACH + step)
            note.cy = a.cy + math.sin(ang) * (SOLO_REACH + step)
            if any(_overlap(note.box, o.box) for o in shapes + others):
                continue
            # A name hangs under its shape, the anchor's included, and a note must stay off it too.
            if any(_overlap(note.box, s.text_box) for s in shapes):
                continue
            if boxes_only is None:
                boxes_only = (note.cx, note.cy)
            if _note_on_edge(note.box, edges):
                continue
            if off_arrows is None:
                off_arrows = (note.cx, note.cy)
            if len(candidates) < _RICH_CANDIDATES:
                candidates.append((note.cx, note.cy))
            if _clear_leader(a, note.cx, note.cy, nodes.values(), others, edges):
                clear = (note.cx, note.cy)
                break
        if clear:
            break
    return clear, off_arrows, boxes_only, candidates


def _place_one(note, nodes, shapes, others, edges):
    """Put one note where nothing is in the way, and record the leader it gets.

    Three tiers, best first. Nothing in the way beats a crossed leader line, and a
    crossed leader beats a note overlapping an arrow.

    One outward scan, then the stub routes are retried over the near positions that
    scan already found, nearest first. Rescanning every position with the stub
    routes was both an order of magnitude slower and worse, because it would find a
    clear route a thousand pixels out and exile the note there rather than keep it
    beside its anchor.
    """
    a = note.anchor
    clear, off_arrows, boxes_only, candidates = _search(note, nodes, shapes, others, edges)
    if clear is None:
        for cx, cy in candidates:
            if _clear_leader(a, cx, cy, nodes.values(), others, edges, rich=True):
                clear = (cx, cy)
                break
    note.cx, note.cy = clear or off_arrows or boxes_only or (a.cx, a.cy + SOLO_REACH)
    # Egon snaps to a 10px grid, and the shift is inside the overlap padding.
    note.cx, note.cy = _snap(note.cx), _snap(note.cy)
    route = _clear_leader(a, note.cx, note.cy, nodes.values(), others, edges, rich=True)
    note.leader_forced = route is None
    note.leader = route or [border(a, note.cx, note.cy), (note.cx, note.cy)]


def _place_notes(nodes, notes, edges=()):
    """Notes sit outside their anchor, turned and pushed until nothing is in the way.

    Two passes, because the first one places each note against the notes already
    down, so a note put down late can land across the leader of an early one. The
    second pass re-places every note knowing where all the others ended up.
    """
    shapes = list(nodes.values())
    placed = []
    for note in notes:
        _place_one(note, nodes, shapes, placed, edges)
        placed.append(note)

    for note in notes:
        others = [n for n in notes if n is not note]
        if _leader_blocked(note.leader, note.anchor, nodes.values(), others, edges):
            _place_one(note, nodes, shapes, others, edges)


def _overlap(a, b, pad=8):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (ax - pad < bx + bw and bx - pad < ax + aw
            and ay - pad < by + bh and by - pad < ay + ah)


# --------------------------------------------------------------------------- geometry


def border(node, tx, ty):
    """Where the line from node's centre towards (tx, ty) leaves its box."""
    dx, dy = tx - node.cx, ty - node.cy
    if dx == 0 and dy == 0:
        return node.cx, node.cy
    sx = HALF / abs(dx) if dx else math.inf
    sy = HALF / abs(dy) if dy else math.inf
    s = min(sx, sy)
    return node.cx + dx * s, node.cy + dy * s


def endpoints(edge):
    return (border(edge.src, edge.dst.cx, edge.dst.cy),
            border(edge.dst, edge.src.cx, edge.src.cy))


def _bow(e, sign, amp):
    """Arc the arrow sideways off the straight line, whichever way that points.

    The offset is perpendicular to the arrow itself rather than vertical, because
    a radial layout has no privileged up.
    """
    (x1, y1), (x2, y2) = endpoints(e)
    dx, dy = x2 - x1, y2 - y1
    span = math.hypot(dx, dy) or 1.0
    px, py = -dy / span * sign * amp * BOW, dx / span * sign * amp * BOW
    return ("cubic", (x1, y1),
            (x1 + dx / 3 + px, y1 + dy / 3 + py),
            (x1 + 2 * dx / 3 + px, y1 + 2 * dy / 3 + py),
            (x2, y2))


def _candidates(e):
    yield ("line", *endpoints(e))
    for amp in (0.3, 0.5, 0.75, 1.0, 1.35, 1.8, 2.3):
        for sign in (-1, 1):
            yield _bow(e, sign, amp)


def _hits(r, e, nodes):
    pts = sample(r)
    x0, y0, x1, y1 = _extent(r)
    count = 0
    for n in nodes.values():
        if n is e.src or n is e.dst:
            continue
        bx, by, bw, bh = n.box
        if x1 <= bx or x0 >= bx + bw or y1 <= by or y0 >= by + bh:
            continue
        if any(bx < x < bx + bw and by < y < by + bh for x, y in pts):
            count += 1
    return count


def _paths_cross(one, two):
    """Whether two routed arrows cross away from a shared endpoint."""
    if one.src in (two.src, two.dst) or one.dst in (two.src, two.dst):
        return False
    return _routes_cross(one.path, two.path)


def _routes_cross(one, two, n=48):
    """Whether two sampled routes cross, skipping every segment outside the other's box."""
    box = _extent(two, n)
    if _boxes_apart(_extent(one, n), box):
        return False
    one, two = sample(one, n), sample(two, n)
    for i in range(len(one) - 1):
        (ax, ay), (bx, by) = one[i], one[i + 1]
        if _boxes_apart((min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)), box):
            continue
        if any(_segments_cross(one[i], one[i + 1], two[j], two[j + 1]) for j in range(len(two) - 1)):
            return True
    return False


def _route_crossings(route, edge, routed):
    return sum(_routes_cross(route, other.path, n=18)
               for other in routed
               if edge.src not in (other.src, other.dst)
               and edge.dst not in (other.src, other.dst))


def assign_routes(nodes, edges):
    """Choose a short route that avoids shapes and already routed arrows.

    Shape collisions dominate the score. Among equally clear routes, fewer arrow
    crossings win, then the earlier and therefore straighter candidate wins.
    Trying and measuring means the drawing and the geometry check agree about
    where a line goes.
    """
    routed = []
    for e in edges:
        scored = [((_hits(cand, e, nodes), _route_crossings(cand, e, routed), i), cand)
                  for i, cand in enumerate(_candidates(e))]
        _, best = min(scored, key=lambda item: item[0])
        e.path = best
        routed.append(e)


@functools.lru_cache(maxsize=4096)
def sample(r, n=48):
    """Points along the real path, so checks and labels agree with the drawing.

    Cached, and so a tuple nobody can change under the next caller, because layout scores
    every candidate route against the same routed arrows again and again.
    """
    if r[0] == "line":
        (x1, y1), (x2, y2) = r[1], r[2]
        return tuple((x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1))
    _, p0, c1, c2, p3 = r
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p3[1]))
    return tuple(pts)


@functools.lru_cache(maxsize=4096)
def _extent(r, n=48):
    """The min/max box of a sampled route."""
    pts = sample(r, n)
    return (min(x for x, _ in pts), min(y for _, y in pts),
            max(x for x, _ in pts), max(y for _, y in pts))


# A verb or a sequence number this much nearer its own arrow than any other arrow reads as
# belonging to that arrow. Below it the reader has to guess, which is the same fault as an
# unreadable label even though every glyph is perfectly crisp.
OWN_ARROW_MARGIN = 12

# How far the wrong way round it has to be before it is worth reporting. Two arrows leaving
# one actor run side by side near it, so a label there is a few pixels nearer one or the
# other by luck and the reader still traces it from the actor. Past a label's own height the
# thing has stopped being near its arrow at all and is sitting on a different one.
MISREAD_GAP = 20
MISATTRIBUTION_GAP = 60  # how much nearer a stranger must be before the object reads as theirs

# The share of a label's own area that counts as a graze rather than being buried. Used by
# the geometry check to decide what to report, and by placement to decide what to accept.
GRAZE = 0.3


def _near_path(x, y, pts):
    """Closest approach from a point to a sampled path."""
    best = float("inf")
    for px, py in pts:
        d = (px - x) ** 2 + (py - y) ** 2
        if d < best:
            best = d
    return math.sqrt(best)


def _own_arrow_margin(x, y, own, foreign, box=None):
    """How much nearer this spot is to its own arrow than to the nearest other one.

    Negative means a foreign arrow is the closer of the two, which is the case a reader
    misreads outright. A foreign arrow running through the label's box counts as the
    worst case whatever the anchor says, because a long verb is centred on its anchor
    and its far end can sit on the next arrow out of the same actor, where the reader
    cannot tell which of the two arrows it names.
    """
    if box is not None and any(_crosses_box(box, p) for p in foreign):
        return -float("inf")
    mine = _near_path(x, y, own)
    nearest = min((_near_path(x, y, p) for p in foreign), default=float("inf"))
    return nearest - mine


def _crosses_box(box, pts):
    """Whether a sampled path passes through a box, including between two samples."""
    bx, by, bw, bh = box
    right, bottom = bx + bw, by + bh
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if bx < x1 < right and by < y1 < bottom:
            return True
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if bx < mx < right and by < my < bottom:
            return True
    return bool(pts) and bx < pts[-1][0] < right and by < pts[-1][1] < bottom


def path_d(r):
    if r[0] == "line":
        return f"M{r[1][0]:.0f} {r[1][1]:.0f} L{r[2][0]:.0f} {r[2][1]:.0f}"
    _, p0, c1, c2, p3 = r
    return (f"M{p0[0]:.0f} {p0[1]:.0f} C{c1[0]:.0f} {c1[1]:.0f} "
            f"{c2[0]:.0f} {c2[1]:.0f} {p3[0]:.0f} {p3[1]:.0f}")


def _verb_box(edge):
    """The box an arrow's verb is drawn in, the same one placement measured."""
    return edge.label_box


def _beside(own, ends, at, box):
    """A verb set beside its own arrow, or None when the arrow runs through it after all.

    The verb is measured from the end of its text nearest the arrow, because that is where
    a reader joins the two. The point on the arrow it was moved off is no part of the text.
    A centred verb may sit against its own source or target, but one set beside the arrow
    runs along it for its whole length, so it has to clear both ends, names included.
    """
    if _crosses_box(box, own) or any(_overlap_area(box, end) for end in ends):
        return None
    return at, box


def _obstacle(node, edge):
    """What a label must dodge: everything about a foreign shape, and the name
    under its own endpoints. Sitting beside your own arrow is fine; sitting on
    top of the actor's name is not."""
    if node is edge.src or node is edge.dst:
        return node.label_box
    return _footprint(node)


def assign_labels(nodes, edges, notes=()):
    """Park each verb and each sequence number where it does not land on anything.

    A label is only measured against shapes it does not belong to. Sitting close
    to your own source or target is what a verb is supposed to do, the arrow is
    short, and refusing that would leave nowhere to put it at all.

    Notes count as obstacles. They are placed first and are the larger shape, so
    the label is the one that moves.
    """
    placed = []
    note_boxes = [n.box for n in notes]
    paths = {id(e): sample(e.path) for e in edges}
    for e in edges:
        pts = paths[id(e)]
        foreign = [p for o, p in paths.items() if o != id(e)]
        near_own = lambda x, y, box: _own_arrow_margin(x, y, pts, foreign, box)
        others = [_obstacle(n, e) for n in nodes.values()] + note_boxes
        if e.label:
            w, h = len(e.label) * CHAR_W + 10, LABEL_BOX_H
            centred = lambda x, y: ((x, y), (x - w / 2, y - h - 4, w, h))
            ends = [_footprint(e.src), _footprint(e.dst)]
            after = lambda x, y: _beside(pts, ends, (x + LABEL_GAP, y), (x + LABEL_GAP, y - h / 2, w, h))
            before = lambda x, y: _beside(pts, ends, (x - LABEL_GAP, y), (x - LABEL_GAP - w, y - h / 2, w, h))
            e.label_at, e.label_box = _free_spot(
                pts, (0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.18, 0.82),
                centred, others + placed, margin=near_own, rescues=(after, before))
            placed.append(e.label_box)
        if e.number is not None:
            e.number_at, box = _free_spot(pts, (0.16, 0.23, 0.10, 0.30, 0.37, 0.44,
                                                    0.52, 0.60, 0.68, 0.76),
                                          lambda x, y: ((x, y), (x - BADGE_BOX / 2, y - BADGE_BOX / 2,
                                                                 BADGE_BOX, BADGE_BOX)),
                                          others + placed, offsets=(0, 18, -18, 32, -32),
                                          margin=near_own)
            placed.append(box)


def _spots(pts, fractions, offsets):
    """Positions along the path, and to either side of it."""
    last = len(pts) - 1
    for t in fractions:
        i = int(last * t)
        x, y = pts[i]
        a, b = pts[max(i - 2, 0)], pts[min(i + 2, last)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        span = math.hypot(dx, dy) or 1.0
        px, py = -dy / span, dx / span
        for off in offsets:
            yield x + px * off, y + py * off


def _free_spot(pts, fractions, box_at, taken, offsets=(0, 22, -22, 40, -40, 60, -60, 82, -82),
               margin=None, rescues=()):
    """First clear spot wins, and with a margin test, the first clear and unambiguous one.

    box_at gives the point a label is measured from and the box it takes at a spot. Each of
    rescues does the same for another shape, or gives None where that shape does not fit.
    A rescue is tried only once no spot passes. Short of passing, it wins only where every
    clear spot of the first shape has another arrow running through it, because a crossing
    is a certain misread and a tie is not. A verb is centred on its arrow, and
    its rescues set it beside the arrow, for the long verb on a short arrow whose centred
    box reaches a sibling arrow at every spot. Two arrows drawn along one line tie at every
    spot whichever shape is used, and a rescue that won such a tie would carry the verb off
    its line on a rounding difference.

    Clearing the shapes is not enough on its own. Arrows are not obstacles, because a verb
    belongs beside an arrow, so a spot that dodges every shape can still land against
    somebody else's arrow and read as that arrow's label. When margin is given, a clear
    spot has to be nearer its own arrow than any other before it is taken, and if none of
    them manages that the least ambiguous clear spot wins.

    Without margin this is the old first-clear-spot rule exactly, and with it the answer
    only changes for a label that was ambiguous anyway.
    """
    best, best_cost = None, None
    clear, scored = [], []
    for spot in _spots(pts, fractions, offsets):
        (x, y), box = box_at(*spot)
        cost = sum(_overlap_area(box, b) for b in taken)
        m = margin(x, y, box) if margin else 0.0
        if cost == 0:
            if margin is None or m >= OWN_ARROW_MARGIN:
                return (x, y), box
            clear.append(((x, y), box, m))
        scored.append(((x, y), box, cost, m))
        if best_cost is None or cost < best_cost:
            best, best_cost = ((x, y), box), cost
    rescued = []
    for placed in (rescue(*spot) for rescue in rescues for spot in _spots(pts, fractions, offsets)):
        if placed is None or sum(_overlap_area(placed[1], b) for b in taken) > 0:
            continue
        pos, box = placed
        m = margin(*pos, box) if margin else 0.0
        if margin is None or m >= OWN_ARROW_MARGIN:
            return pos, box
        rescued.append((pos, box, m))
    uncrossed = [c for c in clear if c[2] > -math.inf] or [c for c in rescued if c[2] > -math.inf]
    if uncrossed or clear:
        pos, box, _ = max(uncrossed or clear, key=lambda c: c[2])
        return pos, box
    if margin is not None and scored:
        # Nothing came out clear, so every option is a graze and the old rule spent the
        # whole budget shaving the last few square pixels off one. A graze is not a fault,
        # the geometry check says as much with the same fraction, but a verb parked against
        # somebody else's arrow is, so spend the slack on saying whose verb it is.
        _, box0, _, _ = scored[0]
        room = best_cost + GRAZE * box0[2] * box0[3]
        near = [s for s in scored if s[2] <= room]
        if near:
            pos, box, _, _ = max(near, key=lambda s: s[3])
            return pos, box
    return best


def _overlap_area(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ox = min(ax + aw, bx + bw) - max(ax, bx)
    oy = min(ay + ah, by + bh) - max(ay, by)
    return ox * oy if ox > 0 and oy > 0 else 0.0


def place(nodes, edges, notes, seating=None):
    """Draw the whole thing, in the one order the steps depend on.

    Nodes settle first. Arrows are routed against the settled nodes. Notes come
    next, because keeping one off an arrow needs the arrow to exist already.
    Labels go last, since a label is the easiest thing to move out of the way.

    A seating fixes where the cast sits and stops the search below, which is how
    that search draws a candidate without calling itself.
    """
    layout(nodes, edges, seating)
    assign_routes(nodes, edges)
    _place_notes(nodes, notes, edges)
    assign_labels(nodes, edges, notes)
    if seating is None:
        _keep_the_best_seating(nodes, edges, notes)


def _seatings(nodes, edges):
    """The seating the ring gave, and every alternative ranked by how far the eye travels.

    The opener keeps the leftmost seat, because that is what opens the story there.
    Ranking is cheap and ranks travel well, but it cannot see whether a seating drops one
    shape on another, so the caller draws the best few for real before believing any of them.
    """
    hero, others, _ = _cast(nodes, edges)
    today = [a.key for a in others]
    # Ranking is cheap for one seating and factorial in how many there are. Eleven actors is
    # twenty seconds, and the skill tells an author to split long before a cast gets there.
    if not 2 <= len(others) <= SEATING_LIMIT:
        return today, []

    # The seats themselves, in ring order, which is the order layout just sat them in.
    places = [(a.cx, a.cy) for a in others]
    numbered = [e for e in edges if e.number is not None]
    opener = min(numbered, key=lambda e: e.number).src if numbered else hero
    pinned = today.index(opener.key) if opener.key in today else None
    steps = _steps(edges)

    ranked = []
    for perm in itertools.permutations(range(len(others))):
        if pinned is not None and perm[pinned] != 0:
            continue
        seat = {today[i]: places[perm[i]] for i in range(len(others))}
        seat[hero.key] = (0.0, 0.0)
        ranked.append((_eye_travel(steps, seat),
                       [today[j] for j in sorted(range(len(others)), key=lambda j: perm[j])]))
    ranked.sort(key=lambda r: r[0])
    return today, ranked


def _crossings(edges):
    """How many pairs of activity arrows cross.

    check_geometry only reports a crossing when a shape carries a manual pin, so an
    automatic layout is never checked against it. The seating search has to count them
    itself, because moving actors is the surest way to introduce one.
    """
    drawn = [e for e in edges if not e.structural]
    return sum(1 for i, one in enumerate(drawn)
               for two in drawn[i + 1:] if _paths_cross(one, two))


def _keep_the_best_seating(nodes, edges, notes):
    """Draw the seatings that read better and keep the best of them.

    A candidate is judged on how badly it draws first and how far the eye travels second,
    because a crossing costs a reader more than a long hop does. Only a drawn candidate can
    say either, since ranking sees positions and not what they collide with.

    Candidates are drawn without their annotations, which decides nothing about where the
    cast sits and is the one step whose cost runs away, at 75 seconds against 0.3 for the
    same story. Only the winner is drawn in full.
    """
    today, ranked = _seatings(nodes, edges)
    if not ranked:
        return

    steps = _steps(edges)

    def cost():
        layout_only = check_geometry(nodes, edges, [], "", quiet=True)
        return (len(layout_only) + _crossings(edges),
                _eye_travel(steps, {n.key: (n.cx, n.cy) for n in nodes.values()}))

    def draw_without_notes(seating):
        layout(nodes, edges, seating)
        assign_routes(nodes, edges)
        assign_labels(nodes, edges)

    best, winner = cost(), today
    for _, seating in ranked[:TRIALS]:
        if seating == today:
            continue
        draw_without_notes(seating)
        here = cost()
        if here < best:
            best, winner = here, seating

    if winner == today:
        # The annotations never moved, because no candidate was drawn with them, so putting
        # the cast back is the whole of the restoration.
        draw_without_notes(today)
    else:
        place(nodes, edges, notes, seating=winner)


def _steps(edges):
    """Each numbered sentence as the actor it leaves and the actor it reaches.

    Only the arrow out of the actor carries the number, so that arrow names the
    sentence and the work object's own spoke names the far end.
    """
    out = []
    for i, e in enumerate(edges):
        if e.number is None:
            continue
        # A sentence with no work object runs straight to its second actor, so the arrow's
        # own destination is the far end and the spoke has nothing to say about it.
        far = (e.dst.key if e.dst.kind == "actor"
               else next((k for k in (e.dst.spoke or ()) if k != e.src.key), None))
        out.append((e.number, i, e.src.key, far))
    return [(src, far) for _, _, src, far in sorted(out)]


def _eye_travel(steps, seat):
    """How far the eye covers in total between consecutive sentences.

    A sentence is read from its number to the work object, so the hop that matters runs
    from one sentence's object to the next sentence's number. A sentence whose actors are
    the same shape has no hop of its own and only contributes where the next one starts.
    """
    marks = []
    for src, far in steps:
        sx, sy = seat[src]
        fx, fy = seat[far] if far in seat else (sx, sy - SOLO_REACH)
        mid = ((sx + fx) / 2, (sy + fy) / 2)
        marks.append(((sx + BADGE_ALONG * (mid[0] - sx), sy + BADGE_ALONG * (mid[1] - sy)), mid))
    return sum(math.hypot(b[0][0] - a[1][0], b[0][1] - a[1][1])
               for a, b in zip(marks, marks[1:]))


def check_geometry(nodes, edges, notes, title, quiet=False):
    """Report layout faults on stderr so an agent with no eyes can still see them.

    The seating search counts faults on candidates nobody will see, so it asks quietly.
    """
    warnings = []
    boxes = [(n.name, n.box) for n in list(nodes.values()) + notes]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _overlap(boxes[i][1], boxes[j][1], pad=0):
                warnings.append(f"shapes overlap: {boxes[i][0]!r} and {boxes[j][0]!r}")

    for n in nodes.values():
        for other in nodes.values():
            if other is not n and _overlap(n.label_box, other.box, pad=0):
                warnings.append(f"label {n.name!r} sits on shape {other.name!r}")

    for note in notes:
        for n in nodes.values():
            if _overlap(note.box, n.text_box, pad=0):
                warnings.append(f"{note.name} covers the label {n.name!r}")

    # A verb or a number that ended up nearer somebody else's arrow than its own. Placement
    # tries to avoid this and cannot always, and the reader has no way to recover the
    # answer, so it is worth saying out loud even though every glyph is legible.
    paths = {id(e): sample(e.path) for e in edges}
    for e in edges:
        mine = paths[id(e)]
        rivals = [(o, paths[id(o)]) for o in edges if o is not e]
        for what, at in ((f"verb {e.label!r}", e.label_at if e.label else None),
                         (f"number {e.number}", e.number_at if e.number is not None else None)):
            if at is None:
                continue
            box = (_verb_box(e) if what.startswith("verb") else
                   (at[0] - BADGE_BOX / 2, at[1] - BADGE_BOX / 2, BADGE_BOX, BADGE_BOX))
            through = [o for o, p in rivals if _crosses_box(box, p)]
            if through:
                o = through[0]
                warnings.append(
                    f"{what} on {e.src.name!r} -> {e.dst.name!r} is crossed by the arrow "
                    f"{o.src.name!r} -> {o.dst.name!r}")
                continue
            near = _near_path(at[0], at[1], mine)
            closer = [(o, d) for o, d in ((o, _near_path(at[0], at[1], p)) for o, p in rivals)
                      if near - d > MISREAD_GAP]
            if closer:
                o, _ = min(closer, key=lambda c: c[1])
                warnings.append(
                    f"{what} on {e.src.name!r} -> {e.dst.name!r} sits nearer the arrow "
                    f"{o.src.name!r} -> {o.dst.name!r}")

    # A reader attributes a work object to whoever it sits nearest. Placement slides an
    # object clear of an actor it has no sentence with, and says so when it could not get
    # far enough, because nothing in the picture then tells the reader whose work it is.
    own = _owners(edges)
    actors = [n for n in nodes.values() if n.kind == "actor"]
    for n in nodes.values():
        mine = own.get(id(n))
        if n.kind == "actor" or not mine:
            continue
        def away(a):
            return math.hypot(n.cx - a.cx, n.cy - a.cy)
        stranger = min((a for a in actors if a not in mine), key=away, default=None)
        if stranger is not None and min(away(a) for a in mine) - away(stranger) > MISATTRIBUTION_GAP:
            warnings.append(
                f"work object {n.name!r} sits nearer {stranger.name!r}, which is not in its sentence")

    labels = []
    for e in edges:
        if e.label:
            box = _verb_box(e)
            w = box[2]
            blockers = labels + [_obstacle(n, e) for n in nodes.values()] + [n.box for n in notes]
            # A graze is not a fault; report a label only when it is properly buried.
            buried = sum(_overlap_area(box, b) for b in blockers) > GRAZE * w * LABEL_BOX_H
            if buried:
                warnings.append(f"label {e.label!r} on {e.src.name!r} -> {e.dst.name!r} has nowhere clear to sit")
            labels.append(box)

    for e in edges:
        pts = sample(e.path)
        for n in nodes.values():
            if n is e.src or n is e.dst:
                continue
            bx, by, bw, bh = n.box
            if any(bx < x < bx + bw and by < y < by + bh for x, y in pts):
                warnings.append(
                    f"arrow {e.src.name!r} -> {e.dst.name!r} crosses shape {n.name!r}")

    for i, one in enumerate(edges):
        for two in edges[i + 1:]:
            endpoints_are_pinned = any(n.pinned for n in (one.src, one.dst, two.src, two.dst))
            if endpoints_are_pinned and _paths_cross(one, two):
                warnings.append(
                    f"pinned layout makes arrow {one.src.name!r} -> {one.dst.name!r} cross arrow "
                    f"{two.src.name!r} -> {two.dst.name!r}")

    for note in notes:
        others = [n for n in notes if n is not note]
        route = note.leader or [(note.anchor.cx, note.anchor.cy), (note.cx, note.cy)]
        # A work object inside the ring of actors cannot always be reached without
        # crossing a spoke, so only report a crossing that had an alternative.
        if not note.leader_forced and _leader_blocked(route, note.anchor, nodes.values(), others, edges):
            warnings.append(f"{note.name} has a leader line crossing a shape or an arrow")
        if _note_on_edge(note.box, edges):
            warnings.append(f"{note.name} overlaps an arrow")

    return warnings if quiet else _report(title, "layout", warnings)


_REPORTED = []


def _report(title, prefix, warnings):
    """Print each distinct warning once under its prefix and return them in order."""
    warnings = list(dict.fromkeys(warnings))
    for w in warnings:
        print(f"{title}: {prefix}: {w}", file=sys.stderr)
        _REPORTED.append(w)
    return warnings


def canvas_box(nodes, notes):
    """The drawn extent plus its margin, which is the picture's own size.

    The reading check and the renderer both need it, and a picture whose reported
    width is not the width it renders at would be worse than no check at all.
    """
    boxes = [b for n in nodes.values() for b in (n.box, n.label_box)]
    boxes += [n.box for n in notes]
    boxes = boxes or [(0.0, 0.0, float(SHAPE), float(SHAPE))]
    return (min(b[0] for b in boxes) - CANVAS_PAD, min(b[1] for b in boxes) - CANVAS_PAD,
            max(b[0] + b[2] for b in boxes) + CANVAS_PAD, max(b[1] + b[3] for b in boxes) + CANVAS_PAD)


def check_reading(nodes, notes, title):
    """Report a picture too wide to read where it lands.

    A markdown image is force-fit to the column it sits in, so width is what decides
    whether the names survive the trip. Nothing else measures that.

    There is no companion check on how far the eye jumps between sentences. Seating the
    cast already minimises that, so whatever is left is the best the ring allows and
    naming it would only report something nobody can act on.
    """
    warnings = []
    min_x, _, max_x, _ = canvas_box(nodes, notes)
    width = max_x - min_x
    if width > COLUMN * NAME_PT / LEGIBLE:
        bare = canvas_box(nodes, [])
        from_notes = width - (bare[2] - bare[0])
        # Naming the widest part rather than advising a split, because the advice that
        # helps depends on which one it is and a split does nothing to a wall of notes.
        many = len(notes) != 1
        blame = (f"{len(notes)} annotation{'s' if many else ''} add{'' if many else 's'} "
                 f"{from_notes:.0f}px of it" if from_notes > width / 5
                 else f"the cast and its work objects set that on their own")
        warnings.append(f"the picture is {width:.0f}px wide, so its names render at "
                        f"{NAME_PT * COLUMN / width:.1f}px in a {COLUMN:.0f}px column, and "
                        f"{blame}, so the file beside the picture is the copy anyone studies")

    return _report(title, "reading", warnings)


def check_names(nodes, title):
    """Report a name the picture cannot show in full, or one that two shapes answer to.

    A name sits under its icon in two lines and anything past that is replaced by an
    ellipsis, so a word can leave the picture while the render still succeeds. Annotations
    and group names are wrapped without a limit and never reach this.

    A cut name hides from both channels. Stderr said nothing, and a trailing ellipsis at
    thumbnail size reads as part of the name rather than as a cut.
    """
    warnings = []
    actor_names = {n.name for n in nodes.values() if n.kind == "actor"}
    for node in nodes.values():
        # A name is not an address, and a domain may use one noun for a role and a thing,
        # so this is said rather than refused the way a shared icon is.
        if node.kind != "actor" and node.name in actor_names:
            warnings.append(f"work object {node.name!r} carries an actor's name, so two "
                            f"shapes in the picture answer to it")
        drawn = wrap(node.name)
        # Against the unlimited wrap rather than a trailing ellipsis, so a name the
        # author wrote with one of their own is not reported as cut.
        if drawn == wrap(node.name, lines=None):
            continue
        kind = "work object" if node.kind == "wo" else "actor"
        warnings.append(f"{kind} {node.name!r} is drawn as {' '.join(drawn)!r}, so the rest of the name is lost")

    return _report(title, "name", warnings)


# Wording that says one story is holding two paths, which is the thing to report.
BRANCH_WORDS = ("otherwise", "or else", "any other", "depending on", "depends on which")
# Wording that reads as a condition and carries an ordinary sentence just as often, so
# it counts only next to the structure below. "alternative" sits here because a
# description naming one is usually a story that was already split off correctly, as
# "the settled two-step alternative when an administrator ..." is.
CONDITION_WORDS = ("branch", "alternative", "alternatively", "either",
                   "if", "when", "unless", "unchanged")
# Both arms of a fork named in one breath, which bare "either" is not. Kept to one
# sentence by [^.] so it cannot pair a word here with a word two sentences away, and
# "whether or not" is left out on purpose, since it means the step happens regardless.
BRANCH_PHRASES = ((r"\beither\b[^.]{0,80}\bor\b", "either ... or"),)


def _reads_as_branch(text, words, phrases=()):
    """The first of `phrases` or `words` in `text`, on word boundaries so 'neither' is
    not 'either'. A note that is not text carries no wording and rendered before this
    check existed, so it is passed over rather than raising."""
    if not isinstance(text, str):
        return None
    for pattern, label in phrases:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    found = re.search(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b",
                      text, re.IGNORECASE)
    return found.group(0).lower() if found else None


def _rival_sentences(sentences):
    """Pairs where one actor works twice on a same-named object under a different verb.

    Ordinary in a linear story, where an actor opens a case and closes it later, so this
    is never a fault by itself. It is what tells a branch apart from a story that merely
    mentions a condition.
    """
    by_object = {}
    for i, s in enumerate(sentences):
        obj = s.get("object")
        if obj is not None:
            by_object.setdefault((s["from"], obj["name"]), []).append(i)
    pairs = []
    for (actor, name), group in by_object.items():
        for x, one in enumerate(group):
            for two in group[x + 1:]:
                if sentences[one].get("verb") != sentences[two].get("verb"):
                    pairs.append((one, two, actor, name))
    return sorted(pairs)


def check_branching(spec, title):
    """Report a story that numbers two alternatives into one path.

    The notation has no conditional, so branches written as one sequence make the picture
    contradict itself, and a caption cannot undo what the numbers say. The failure this
    comes from declared no notice period in sentence 4 and booked a notice period expiry
    in sentence 7, which a reader caught on sight.

    Wording is the signal that holds. Three of the six examples pair an actor with a
    same-named object under two verbs and every one of them is a plain sequence, so
    structure earns a warning only alongside a word, and the weaker words only there.
    """
    warnings = []
    sentences = spec.get("sentences") or []
    numbers = [s.get("n", i) for i, s in enumerate(sentences, 1)]
    description = spec.get("description") or ""

    both = BRANCH_WORDS + CONDITION_WORDS
    for one, two, actor, name in _rival_sentences(sentences):
        # One number across both sentences is the spec's way of saying they happen at
        # once, so the author has already ruled out one or the other.
        if numbers[one] == numbers[two]:
            continue
        word = (_reads_as_branch(description, both, BRANCH_PHRASES)
                or _reads_as_branch(sentences[one].get("note"), both, BRANCH_PHRASES)
                or _reads_as_branch(sentences[two].get("note"), both, BRANCH_PHRASES))
        if word:
            warnings.append(
                f"sentences {numbers[one]} and {numbers[two]} look like alternative branches "
                f"rather than a sequence, both have {actor!r} acting on {name!r} and {word!r} "
                f"reads as a branch, the notation has no conditional so draw one story per branch")

    if not warnings:
        sites = [("the description", description)]
        sites += [(f"the note on sentence {numbers[i]}", s.get("note"))
                  for i, s in enumerate(sentences)]
        for where, text in sites:
            word = _reads_as_branch(text, BRANCH_WORDS, BRANCH_PHRASES)
            if word:
                warnings.append(
                    f"{where} says {word!r}, which reads as a branch, the notation has no "
                    f"conditional so draw one story per branch")

    return _report(title, "branch", warnings)


# Actors drawn as people, who can act without anything handed to them. The rest of
# icons.json's defaultActors, System, needs a trigger.
SELF_STARTING_ICONS = ("Person", "Group")


def check_gaps(spec, title):
    """Report an actor that starts working without anything handed to it.

    The story this comes from had a notification job and an expiry deleter numbered into
    one sequence, and the deleter first acted at sentence 4 with nothing arriving at it.
    It really ran days later, triggered by an event the story never drew.

    A person may step in on their own initiative, as the fleet manager in
    `Onboard a fleet customer` does, so only actors that need a trigger are reported.
    """
    warnings = []
    self_starting = {a["name"] for a in spec.get("actors") or [] if a.get("icon") in SELF_STARTING_ICONS}
    sentences = spec.get("sentences") or []
    numbered = sorted(((s.get("n", i), s) for i, s in enumerate(sentences, 1)), key=lambda pair: pair[0])
    if not numbered:
        return warnings
    opening = numbered[0][0]
    started, handed_to = set(), set()
    # One number is one moment, so a hand-over shares no number with the step it enables.
    for n, group in itertools.groupby(numbered, key=lambda pair: pair[0]):
        group = [s for _, s in group]
        for s in group:
            actor = s["from"]
            if actor in started:
                continue
            started.add(actor)
            if n != opening and actor not in self_starting and actor not in handed_to:
                warnings.append(
                    f"{actor!r} first acts in sentence {n} and no earlier sentence hands it "
                    f"anything, so the story is missing the sentence that hands it the work, "
                    f"or it is two stories")
        handed_to.update(s["to"] for s in group if s.get("to"))

    return _report(title, "gap", warnings)


# Stems of a verb that reaches somebody, so a sentence with one needs a `to`. A guess
# from English prefixes, which "asks for a refund" or "offers a discount" can trip.
COMMUNICATION_STEMS = ("notif", "send", "sent", "tell", "told", "inform", "e-mail", "email",
                       "ask", "offer", "invit", "remind")
COMMUNICATION_VERB = re.compile(r"\b(" + "|".join(map(re.escape, COMMUNICATION_STEMS)) + r")",
                                re.IGNORECASE)


def check_verbs(spec, title):
    """Report a verb holding two activities, or one that reaches nobody.

    `and` in a work object's name is the SKILL.md tell for two objects, and a comma or an
    `and` in a verb is the same tell for two activities. A verb that notifies with no `to`
    usually means the actor it reaches was never modelled.
    """
    warnings = []
    sentences = spec.get("sentences") or []
    for i, s in enumerate(sentences, 1):
        n, verb = s.get("n", i), s.get("verb") or ""
        if "," in verb or re.search(r"\band\b", verb, re.IGNORECASE):
            warnings.append(
                f"sentence {n} has the verb {verb!r}, which reads as two activities, so write "
                f"two sentences, sharing a number only when neither waits for the other")
        if s.get("to") is None and COMMUNICATION_VERB.search(verb):
            warnings.append(
                f"sentence {n} has the verb {verb!r} and no 'to', so the actor it reaches is "
                f"missing from the story")

    return _report(title, "verb", warnings)


def check_icons(spec, actor_icons, title):
    """Report a work object drawn with an icon Egon gives actors by default.

    The icon is all that tells a reader which kind a shape is, so a `Person` on a driver
    account reads as a driver. Said rather than refused, because notation.md calls the
    icon split a habit rather than a law. An icon one of this story's actors uses is
    refused at load instead.
    """
    warnings = []
    for i, s in enumerate(spec.get("sentences") or [], 1):
        obj = s.get("object")
        if obj is not None and obj.get("icon") in actor_icons:
            warnings.append(
                f"work object {obj['name']!r} in sentence {s.get('n', i)} is drawn as "
                f"{obj['icon']!r}, an actor icon, so it reads as an actor, pick a work-object icon")

    return _report(title, "icon", warnings)


# --------------------------------------------------------------------------- .egn


def _type_of(node):
    return ("domainStory:actor" if node.kind == "actor" else "domainStory:workObject") + node.icon


def build_egn(spec, nodes, edges, notes, groups, icons):
    """Emit the Egon v3 element list plus the icon-set block it needs to stand alone."""
    els = []
    counter = [0]

    def next_id(prefix):
        counter[0] += 1
        return f"{prefix}_{counter[0]:04d}"

    for node in nodes.values():
        node.eid = next_id("shape")
        el = {"type": _type_of(node), "name": node.name, "id": node.eid,
              "x": int(node.cx - HALF), "y": int(node.cy - HALF)}
        if node.changed:
            el["pickedColor"] = ACCENT_EGN
        els.append(el)
    for name, members in groups:
        if not members:
            continue
        xs = [m.cx for m in members]
        ys = [m.cy for m in members]
        x, y = min(xs) - HALF - 30, min(ys) - HALF - 40
        els.append({"type": "domainStory:group", "name": name, "id": next_id("shape"),
                    "pickedColor": "black", "x": int(x), "y": int(y),
                    "width": int(max(xs) + HALF + 30 - x),
                    "height": int(max(ys) + HALF + LABEL_H + 10 - y)})
    for note in notes:
        note.eid = next_id("shape")
        els.append({"type": "domainStory:textAnnotation", "name": "", "id": note.eid,
                    "x": int(note.cx - NOTE_W / 2), "y": int(note.cy - note.height / 2),
                    "text": note.text, "number": note.height, "width": NOTE_W,
                    "height": note.height})

    for e in edges:
        (x1, y1), (x2, y2) = endpoints(e)
        el = {"type": "domainStory:activity", "name": e.label, "id": next_id("connection"),
              "pickedColor": ACCENT_EGN if e.changed else "black",
              "waypoints": [
                  {"original": {"x": int(e.src.cx), "y": int(e.src.cy)}, "x": int(x1), "y": int(y1)},
                  {"original": {"x": int(e.dst.cx), "y": int(e.dst.cy)}, "x": int(x2), "y": int(y2)}],
              "source": e.src.eid, "target": e.dst.eid,
              "multipleNumberAllowed": False}
        if e.number is not None:
            el["number"] = e.number
        els.append(el)

    for note in notes:
        a = note.anchor
        els.append({"type": "domainStory:connection", "name": "", "id": next_id("connection"),
                    "waypoints": [
                        {"original": {"x": int(a.cx), "y": int(a.cy)},
                         "x": int(a.cx), "y": int(a.cy)},
                        {"original": {"x": int(note.cx), "y": int(note.cy)},
                         "x": int(note.cx), "y": int(note.cy)}],
                    "source": a.eid, "target": note.eid})

    els.append({"info": description_of(spec)})
    els.append({"version": EGN_VERSION})

    actors, work_objects = {}, {}
    lib = icons["icons"]
    for name in icons["defaultActors"]:
        actors[name] = lib[name]
    for name in icons["defaultWorkObjects"]:
        work_objects[name] = lib[name]
    for node in nodes.values():
        (actors if node.kind == "actor" else work_objects)[node.icon] = lib[node.icon]
    return {"domain": {"name": "default", "actors": actors, "workObjects": work_objects},
            "dst": els}


def description_of(spec):
    scope = spec.get("scope") or {}
    parts = [scope.get("time"), scope.get("granularity"), scope.get("purity")]
    line = " · ".join(p for p in parts if p)
    body = spec.get("description") or ""
    return f"{line}\n{body}".strip() if line else body


def subtitle_of(spec):
    """What goes under the title in the picture.

    The scope stays out of it. AS-IS, coarse-grained and digitalized are the
    method's words for the modeller, and a reader of the picture cannot decode
    them. They still travel in the Egon description, which is where a modeller
    reading the model will look for them.
    """
    return spec.get("description") or ""


# --------------------------------------------------------------------------- svg


INK = "#1b1b1b"
MUTED = "#6b6b6b"
PAPER = "#ffffff"
ACCENT = "#c8341b"          # marks the sentences a change touched
NOTE = "#fffbe6"            # the note's paper
ICON_BOX = 58
_SVG_INNER = re.compile(r"<svg[^>]*>(.*)</svg>", re.S)
_VIEWBOX = re.compile(r'viewBox="([\d.\s-]+)"')


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def wrap(name, width=17, lines=2):
    words, out, cur = str(name).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if len(trial) > width and cur:
            out.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        out.append(cur)
    if lines is not None and len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][:width - 1] + "…"
    return out


def icon_markup(icon_svg, cx, cy, colour=None, paint=None):
    inner = _SVG_INNER.search(icon_svg)
    if not inner:
        return ""
    vb = _VIEWBOX.search(icon_svg)
    vw, vh = (float(v) for v in vb.group(1).split()[2:4]) if vb else (24.0, 26.0)
    scale = ICON_BOX / max(vw, vh)
    tx = cx - vw * scale / 2
    ty = cy - vh * scale / 2
    return (f'<g transform="translate({tx:.1f},{ty:.1f}) scale({scale:.3f})" '
            f'{(paint or EGON).paint(fill=colour or INK)}>{inner.group(1)}</g>')


# The board names a palette role per colour so one style block can retheme it.
# Each role maps to the host page's CSS custom property of that name, with the
# literal beside it as the fallback for a page that defines none.
_LIGHT = {"ink": INK, "ink-2": MUTED, "surface": PAPER, "accent": ACCENT, "wash": NOTE}
_ROLES = {literal: role for role, literal in _LIGHT.items()}
_DARK = {"ink": "#ffffff", "ink-2": "#c3c2b7", "surface": "#1a1a19", "accent": "#ff8a73",
         "wash": "#4e372e"}
_HALO = 'paint-order="stroke" stroke-width="5" stroke-linejoin="round"'


class Paint:
    """Where a colour lands in the markup.

    Egon gets the literal in a presentation attribute, exactly as before.
    A board gets a class from its own prefixed style block, because a var()
    in a presentation attribute is not valid and would drop to black.
    """

    def __init__(self, pid=None):
        self.pid = pid

    def paint(self, fill=None, stroke=None):
        pairs = [(k, v) for k, v in (("fill", fill), ("stroke", stroke)) if v]
        if self.pid is None:
            return " ".join(f'{k}="{v}"' for k, v in pairs)
        attrs = [f'{k}="none"' for k, v in pairs if v == "none"]
        classes = [f"{self.pid}-{k[0]}-{_ROLES[v]}" for k, v in pairs if v != "none"]
        if classes:
            attrs.append(f'class="{" ".join(classes)}"')
        return " ".join(attrs)

    def halo(self, fill):
        """Text with a paper outline, so a name stays legible where an arrow runs under it."""
        if self.pid is None:
            return f'fill="{fill}" paint-order="stroke" stroke="{PAPER}" stroke-width="5" stroke-linejoin="round"'
        return f'{self.paint(fill=fill, stroke=PAPER)} {_HALO}'

    def tip(self, suffix=""):
        return f"tip{suffix}" if self.pid is None else f"{self.pid}-tip{suffix}"


EGON = Paint()


def board_id(title):
    """A prefix for ids and classes, so two boards inlined in one page cannot reach into each other."""
    return "sb" + format(zlib.crc32(str(title).encode("utf-8")), "08x")


def board_style(pid):
    def props(palette):
        return "".join(f"--{pid}-{role}:var(--{role},{literal});" for role, literal in palette.items())

    dark = props(_DARK)
    classes = "\n".join(f".{pid}-{k[0]}-{role}{{{k}:var(--{pid}-{role})}}"
                        for k in ("fill", "stroke") for role in _LIGHT)
    return (f"<style>\n.{pid}{{{props(_LIGHT)}}}\n"
            f'@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]) .{pid}{{{dark}}}}}\n'
            f':root[data-theme="dark"] .{pid}{{{dark}}}\n'
            f"{classes}\n"
            f".{pid}-ring{{fill:none;stroke:var(--{pid}-accent);stroke-width:3}}\n</style>")


def _step_key(n):
    return (0, n, "") if isinstance(n, (int, float)) and not isinstance(n, bool) else (1, 0, str(n))


def _tag(kind, steps, name, inner):
    listed = ",".join(str(n) for n in sorted(steps, key=_step_key))
    return f'<g data-{kind}="{esc(listed)}" data-name="{esc(name)}">{inner}</g>'


def render_svg(spec, nodes, edges, notes, groups, icons, board=False):
    """Draw the story. With board=True the same pass also tags every element and themes it by class."""
    P = Paint(board_id(spec["title"])) if board else EGON
    # Measured from real boxes rather than from centres, because a note is as
    # tall as its text and a fixed half-shape would crop the long ones.
    min_x, min_y, max_x, max_y = canvas_box(nodes, notes)

    marked = any(e.changed for e in edges)
    # The title block is not a shape, so nothing in the boxes above reserves room
    # for it and a one-line description ran off the right edge with the viewBox
    # cutting the sentence in half. Wrapping it to the drawing's own width keeps
    # the canvas as wide as the story and lets the band grow downwards instead.
    sub = [row for line in subtitle_of(spec).splitlines()
           for row in wrap(line, max(int((max_x - min_x - 2 * CANVAS_PAD) / BAND_CHAR_W), 20), None)]
    head = 58 + max(len(sub) + (1 if marked else 0), 2) * 19
    min_y -= head
    w, h = max_x - min_x, max_y - min_y

    page = "" if board else f'style="background-color:{PAPER}" '
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
         f'viewBox="{min_x:.0f} {min_y:.0f} {w:.0f} {h:.0f}" '
         f'{page}version="1.2">']
    font = 'font-family="Helvetica Neue,Helvetica,Arial,sans-serif"'
    if board:
        # The root group carries the palette and the identity, so it opens first and holds the rest.
        p.append(board_style(P.pid))
        p.append(f'<g class="{P.pid}" data-name="{esc(spec["title"])}" {font}>')
    p.append(f'<rect x="{min_x:.0f}" y="{min_y:.0f}" width="{w:.0f}" height="{h:.0f}" {P.paint(fill=PAPER)}/>')
    # Only define the accent arrowhead when something uses it, so a story with
    # nothing marked renders byte for byte the way it did before.
    heads = [("", INK)] + ([("-accent", ACCENT)] if marked else [])
    tips = "".join(
        f'<marker id="{P.tip(suffix)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" {P.paint(fill=colour)}/>'
        "</marker>"
        for suffix, colour in heads)
    p.append(f"<defs>{tips}</defs>")
    if not board:
        p.append(f"<g {font}>")

    ty = min_y + 34
    p.append(f'<text x="{min_x + CANVAS_PAD:.0f}" y="{ty:.0f}" font-size="24" font-weight="600" '
             f'{P.paint(fill=INK)}>{esc(spec["title"])}</text>')
    for i, line in enumerate(sub):
        p.append(f'<text x="{min_x + CANVAS_PAD:.0f}" y="{ty + 24 + i * 19:.0f}" font-size="13" '
                 f'{P.paint(fill=MUTED)}>{esc(line)}</text>')
    if marked:
        ly = ty + 24 + len(sub) * 19
        p.append(f'<line x1="{min_x + CANVAS_PAD:.0f}" y1="{ly - 4:.0f}" x2="{min_x + CANVAS_PAD + 22:.0f}" '
                 f'y2="{ly - 4:.0f}" {P.paint(stroke=ACCENT)} stroke-width="3"/>')
        p.append(f'<text x="{min_x + CANVAS_PAD + 30:.0f}" y="{ly:.0f}" font-size="13" {P.paint(fill=ACCENT)}>'
                 "Highlighted: the part of the process this change affects.</text>")

    for name, members in groups:
        if not members:
            continue
        gxs = [m.cx for m in members]
        gys = [m.cy for m in members]
        gx, gy = min(gxs) - HALF - 30, min(gys) - HALF - 40
        gw = max(gxs) + HALF + 30 - gx
        gh = max(gys) + HALF + LABEL_H + 10 - gy
        p.append(f'<rect x="{gx:.0f}" y="{gy:.0f}" width="{gw:.0f}" height="{gh:.0f}" rx="14" '
                 f'{P.paint(fill="none", stroke=MUTED)} stroke-width="1.5" stroke-dasharray="7 5"/>')
        p.append(f'<text x="{gx + 12:.0f}" y="{gy + 20:.0f}" font-size="{NAME_PT}" font-weight="600" '
                 f'{P.halo(MUTED)}>{esc(name)}</text>')

    for e in edges:
        markup = edge_markup(e, P)
        p.append(_tag("s", () if e.step is None else (e.step,), e.label, markup) if board else markup)

    def leader_of(note):
        a = note.anchor
        route = note.leader or [(a.cx, a.cy), (note.cx, note.cy)]
        d = f"M{route[0][0]:.0f} {route[0][1]:.0f}" + "".join(
            f" L{x:.0f} {y:.0f}" for x, y in route[1:])
        return (f'<path d="{d}" {P.paint(stroke=MUTED)} stroke-width="1.2" '
                f'stroke-dasharray="4 4" fill="none"/>')

    if not board:
        for note in notes:
            p.append(leader_of(note))

    for node in nodes.values():
        colour = ACCENT if node.changed else INK
        out = [icon_markup(icons["icons"][node.icon], node.cx, node.cy, colour, P)]
        for i, line in enumerate(wrap(node.name)):
            # The same paper halo the edge labels get. A name sits under its icon and
            # cannot move, so when an arrow runs through that spot the name has to win.
            out.append(f'<text x="{node.cx:.0f}" y="{node.cy + HALF + NAME_TOP + i * NAME_LINE_H:.0f}" '
                       f'font-size="{NAME_PT}" text-anchor="middle" {P.halo(colour)}>{esc(line)}</text>')
        if board:
            p.append(_tag("a" if node.kind == "actor" else "o", node.steps, node.name, "".join(out)))
        else:
            p.extend(out)

    for note in notes:
        nh = note.height
        nx, ny = note.cx - NOTE_W / 2, note.cy - nh / 2
        fold = 14
        # A board keeps the leader with its note so one group owns both, which lifts the leader
        # above the shapes. The layout already routes it clear of them.
        out = [leader_of(note)] if board else []
        out.append(f'<path d="M{nx:.0f} {ny:.0f} H{nx + NOTE_W - fold:.0f} L{nx + NOTE_W:.0f} '
                   f'{ny + fold:.0f} V{ny + nh:.0f} H{nx:.0f} Z" {P.paint(fill=NOTE, stroke=MUTED)} '
                   f'stroke-width="1.2"/>')
        for i, line in enumerate(note.lines):
            out.append(f'<text x="{nx + 10:.0f}" y="{ny + NOTE_TOP + i * NOTE_LINE_H:.0f}" '
                       f'font-size="{NOTE_PT}" {P.paint(fill=INK)}>{esc(line)}</text>')
        if board:
            p.append(_tag("n", note.steps, note.name, "".join(out)))
        else:
            p.extend(out)

    p.append("</g></svg>")
    return "\n".join(p)


def edge_markup(e, P=EGON):
    r = e.path
    dash = ' stroke-dasharray="6 4"' if e.structural else ""
    colour = ACCENT if e.changed else (MUTED if e.structural else INK)
    tip = P.tip("-accent" if e.changed else "")
    out = [f'<path d="{path_d(r)}" {P.paint(fill="none", stroke=colour)} stroke-width="1.6"{dash} '
           f'marker-end="url(#{tip})"/>']
    if e.label:
        bx, by, bw, bh = e.label_box
        lx, ly = bx + bw / 2, by + bh + 4
        out.append(f'<text x="{lx:.0f}" y="{ly - LABEL_LIFT:.0f}" font-size="{NAME_PT}" text-anchor="middle" '
                   f'{P.halo(colour)}>{esc(e.label)}</text>')
    if e.number is not None:
        nx, ny = e.number_at
        out.append(f'<circle cx="{nx:.0f}" cy="{ny:.0f}" r="{BADGE_R}" {P.paint(fill=colour)}/>')
        out.append(f'<text x="{nx:.0f}" y="{ny + 5:.0f}" font-size="{BADGE_PT}" text-anchor="middle" '
                   f'{P.paint(fill=PAPER)} font-weight="600">{esc(e.number)}</text>')
    return "".join(out)


# --------------------------------------------------------------------------- output


FORMATS = ("egn.svg", "board.svg", "svg", "egn", "png")
DEFAULT_FORMATS = "egn.svg,board.svg"
_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]+')


def safe_name(title):
    """Egon reads the story title back out of the filename, so keep it readable."""
    name = unicodedata.normalize("NFC", str(title)).strip()
    name = _UNSAFE.sub("-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name or "domain-story"


def embed_dst(svg, payload):
    # An XML comment may not hold "--", and Egon ends the payload at the first "</DST>", so
    # both go out as JSON escapes that Egon and egn2text read back as the original text.
    # Outside a string, JSON has no "--", so the replace changes only text inside strings.
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    text = text.replace("--", "-\\u002d").replace("</DST>", "\\u003c/DST>")
    return svg + "\n<!-- <DST>\n" + text + "\n </DST> -->"


def to_png(svg_path, png_path):
    for tool, args in (("rsvg-convert", ["-o", str(png_path), str(svg_path)]),
                       ("cairosvg", [str(svg_path), "-o", str(png_path)]),
                       ("inkscape", [str(svg_path), "--export-type=png",
                                     f"--export-filename={png_path}"])):
        if shutil.which(tool):
            subprocess.run([tool, *args], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    if shutil.which("qlmanage"):
        return _quick_look_png(svg_path, png_path)
    print("png: no converter found; install one of rsvg-convert, cairosvg, inkscape",
          file=sys.stderr)
    return False


_ROOT_SIZE = re.compile(r'(<svg\b[^>]*?\s)width="([\d.]+)" height="([\d.]+)" viewBox="([-\d. ]+)"')


def _quick_look_png(svg_path, png_path):
    """Rasterise with macOS Quick Look, which every Mac has and no other converter here needs.

    Quick Look always draws a square thumbnail and crops a wide picture to it, so the viewBox
    is squared first, centred on the same picture, and nothing falls off the edge.
    """
    svg = Path(svg_path).read_text(encoding="utf-8")
    side = 1600
    m = _ROOT_SIZE.search(svg)
    if m:
        w, h = float(m.group(2)), float(m.group(3))
        x, y = (float(v) for v in m.group(4).split()[:2])
        side = max(w, h)
        svg = (svg[:m.start()] + m.group(1)
               + f'width="{side:g}" height="{side:g}" '
               + f'viewBox="{x - (side - w) / 2:g} {y - (side - h) / 2:g} {side:g} {side:g}"'
               + svg[m.end():])
    with tempfile.TemporaryDirectory() as tmp:
        square = Path(tmp) / "picture.svg"
        square.write_text(svg, encoding="utf-8")
        subprocess.run(["qlmanage", "-t", "-s", str(math.ceil(side)), "-o", tmp, str(square)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        thumb = Path(tmp) / "picture.svg.png"
        if not thumb.exists():
            print("png: qlmanage wrote no thumbnail", file=sys.stderr)
            return False
        shutil.move(str(thumb), str(png_path))
    return True


def generate(path, formats, out_dir, with_date, preview_dir=None):
    icons = json.loads(ICONS_PATH.read_text(encoding="utf-8"))
    spec = load_spec(path)
    validate(spec, icons["icons"])

    nodes, edges, notes, groups = build(spec)
    place(nodes, edges, notes)
    check_geometry(nodes, edges, notes, spec["title"])
    check_names(nodes, spec["title"])
    check_reading(nodes, notes, spec["title"])
    check_branching(spec, spec["title"])
    check_gaps(spec, spec["title"])
    check_verbs(spec, spec["title"])
    check_icons(spec, icons["defaultActors"], spec["title"])

    payload = build_egn(spec, nodes, edges, notes, groups, icons)
    svg = render_svg(spec, nodes, edges, notes, groups, icons)
    board = render_svg(spec, nodes, edges, notes, groups, icons, board=True) if "board.svg" in formats else None

    stem = safe_name(spec["title"])
    if with_date:
        stem += "_" + datetime.date.today().isoformat()
    target = Path(out_dir) if out_dir else path.parent
    target.mkdir(parents=True, exist_ok=True)

    written = []
    if "egn" in formats:
        p = target / f"{stem}.egn"
        p.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        written.append(p)
    if "svg" in formats:
        p = target / f"{stem}.svg"
        p.write_text(svg, encoding="utf-8")
        written.append(p)
    if "egn.svg" in formats:
        p = target / f"{stem}.egn.svg"
        p.write_text(embed_dst(svg, payload), encoding="utf-8")
        written.append(p)
    if board is not None:
        p = target / f"{stem}.board.svg"
        p.write_text(board, encoding="utf-8")
        written.append(p)
    pngs = [target / f"{stem}.png"] if "png" in formats else []
    if preview_dir:
        Path(preview_dir).mkdir(parents=True, exist_ok=True)
        pngs.append(Path(preview_dir) / f"{stem}.png")
    if pngs:
        # A board themes by custom property, which the converters do not read, so it is never the source.
        src = next((p for p in written if p.suffix == ".svg" and not p.name.endswith(".board.svg")), None)
        tmp = None
        if src is None:
            tmp = src = target / f"{stem}.__tmp.svg"
            src.write_text(svg, encoding="utf-8")
        try:
            for png in pngs:
                if to_png(src, png):
                    written.append(png)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render domain story specs into Egon.io files and SVG.")
    ap.add_argument("specs", nargs="+", type=Path, help="story spec .json files")
    ap.add_argument("--format", default=DEFAULT_FORMATS,
                    help=f"comma separated, any of {', '.join(FORMATS)} (default: {DEFAULT_FORMATS})")
    ap.add_argument("--out-dir", default=None, help="where to write (default: next to the spec)")
    ap.add_argument("--date", action="store_true", help="append _YYYY-MM-DD, as Egon's export does")
    ap.add_argument("--preview", metavar="DIR", default=None,
                    help="also write <Title>.png into DIR to look at, away from the files that ship")
    args = ap.parse_args(argv)
    _REPORTED.clear()       # one run's warnings, however often main() is called in a process

    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        ap.error(f"unknown format(s): {', '.join(unknown)}; choose from {', '.join(FORMATS)}")

    failed = False
    for path in args.specs:
        try:
            for written in generate(path, formats, args.out_dir, args.date, args.preview):
                print(written)
        except (SpecError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            failed = True
    if _REPORTED:
        print("warnings: references/warnings.md in the skill folder says how to fix each kind",
              file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
