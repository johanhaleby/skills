#!/usr/bin/env python3
"""Run with: python3 -m unittest test_story2egn -v"""
import functools
import json
import re
import tempfile
import math
import unittest
import xml.dom.minidom
from unittest import mock
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import story2egn as S

ICONS = json.loads(S.ICONS_PATH.read_text(encoding="utf-8"))
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

STORY = {
    "title": "Order coffee",
    "scope": {"granularity": "coarse-grained", "time": "AS-IS", "purity": "pure"},
    "description": "The typical case.",
    "actors": [
        {"name": "Customer", "icon": "Person"},
        {"name": "Barista", "icon": "Person"},
    ],
    "sentences": [
        {"n": 1, "from": "Customer", "verb": "orders",
         "object": {"name": "Coffee", "icon": "Dining", "id": "coffee"},
         "prep": "from", "to": "Barista"},
        {"n": 2, "from": "Barista", "verb": "serves",
         "object": {"name": "Cup", "icon": "Dining"}, "prep": "to", "to": "Customer",
         "note": "Only for drinking in."},
    ],
    "relations": [{"from": "coffee", "verb": "is paid by", "to": "Customer"}],
    "groups": [{"name": "Counter", "members": ["Barista"]}],
}

REPEATED_EXCHANGE = {
    "title": "Check parking spot availability",
    "actors": [
        {"name": "Driver", "icon": "Person"},
        {"name": "Platform", "icon": "System"},
    ],
    "sentences": [
        {"n": 1, "from": "Driver", "verb": "requests",
         "object": {"name": "Parking zone details", "icon": "Document"},
         "prep": "from", "to": "Platform"},
        {"n": 2, "from": "Platform", "verb": "returns",
         "object": {"name": "Unavailable parking spots", "icon": "Place"},
         "prep": "to", "to": "Driver"},
        {"n": 3, "from": "Driver", "verb": "requests",
         "object": {"name": "Parking spot claim", "icon": "Document"},
         "prep": "from", "to": "Platform"},
        {"n": 4, "from": "Platform", "verb": "rejects",
         "object": {"name": "Rejected parking spot claim", "icon": "Thumb-down"},
         "prep": "to", "to": "Driver"},
    ],
}


BRANCHED = {
    "title": "Cancel a parking spot allocation",
    "description": "What the system frees depends on which ticket was cancelled.",
    "actors": [
        {"name": "Parking system", "icon": "System"},
        {"name": "Renewal job", "icon": "System"},
        {"name": "Scheduling service", "icon": "System"},
    ],
    "sentences": [
        {"n": 4, "from": "Parking system", "verb": "cancels the occupation on",
         "object": {"name": "Allocation", "icon": "Place"},
         "note": "The claim's own ticket, so no notice period."},
        {"n": 5, "from": "Parking system", "verb": "announces",
         "object": {"name": "Occupation cancelled", "icon": "Document"},
         "prep": "to", "to": "Renewal job"},
        {"n": 6, "from": "Parking system", "verb": "cancels the reservation on",
         "object": {"name": "Allocation", "icon": "Place"},
         "note": "Any other purchase cause, so the spot winds down over its notice period."},
        {"n": 7, "from": "Parking system", "verb": "books",
         "object": {"name": "Notice period expiry", "icon": "Document"},
         "prep": "in", "to": "Scheduling service"},
    ],
}


def render(spec, formats=("egn.svg",)):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "story.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        written = S.generate(path, list(formats), d, with_date=False)
        return {p.name: p.read_text(encoding="utf-8") for p in written}


# Three arrows leave one actor close together, and the middle one carries a verb far longer
# than the arrow, so a verb centred on it reaches across to the arrow beside it.
FANNING_OUT = {
    "title": "Terminate a claim",
    "actors": [
        {"name": "Backoffice", "icon": "Desktop"},
        {"name": "Platform", "icon": "System"},
        {"name": "Driver account history", "icon": "Folder"},
    ],
    "sentences": [
        {"n": 1, "from": "Backoffice", "verb": "sends", "object": {"name": "Termination", "icon": "Gavel"},
         "prep": "to", "to": "Platform"},
        {"n": 2, "from": "Platform", "verb": "ends the occupation when the last bought ticket ends",
         "object": {"name": "Allocation", "icon": "Key"}},
        {"n": 3, "from": "Platform", "verb": "announces", "object": {"name": "Claim terminated", "icon": "Flag"},
         "prep": "to", "to": "Driver account history"},
        {"n": 3, "from": "Platform", "verb": "announces", "object": {"name": "Claim ended", "icon": "Info"}},
    ],
}


def model(spec):
    S.validate(spec, ICONS["icons"])
    nodes, edges, notes, groups = S.build(spec)
    S.place(nodes, edges, notes)
    return nodes, edges, notes, groups


def broken(**changes):
    spec = json.loads(json.dumps(STORY))
    spec.update(changes)
    return spec


def names_reported(spec):
    nodes, _, _, _ = model(spec)
    with redirect_stderr(StringIO()):
        return S.check_names(nodes, spec["title"])


def first_object_named(name):
    spec = json.loads(json.dumps(STORY))
    spec["sentences"][0]["object"]["name"] = name
    return names_reported(spec)


class WritesWhatEgonReads(unittest.TestCase):
    def test_envelope_matches_the_v3_format(self):
        nodes, edges, notes, groups = model(STORY)
        payload = S.build_egn(STORY, nodes, edges, notes, groups, ICONS)
        self.assertEqual(set(payload), {"domain", "dst"})
        self.assertEqual(payload["dst"][-1], {"version": "3.0.0"})
        self.assertIn("info", payload["dst"][-2])
        shapes = [e for e in payload["dst"] if str(e.get("type", "")).startswith(
            ("domainStory:actor", "domainStory:workObject"))]
        self.assertTrue(shapes)
        for shape in shapes:
            self.assertEqual(set(shape), {"type", "name", "id", "x", "y"})

    def test_every_activity_reference_resolves(self):
        # Egon drops activities whose endpoints are missing and calls the import broken.
        nodes, edges, notes, groups = model(STORY)
        payload = S.build_egn(STORY, nodes, edges, notes, groups, ICONS)
        ids = {e["id"] for e in payload["dst"] if "id" in e}
        links = [e for e in payload["dst"]
                 if e.get("type") in ("domainStory:activity", "domainStory:connection")]
        self.assertTrue(links)
        for link in links:
            self.assertIn(link["source"], ids)
            self.assertIn(link["target"], ids)

    def test_the_scope_reaches_the_description(self):
        self.assertTrue(S.description_of(STORY).startswith("AS-IS · coarse-grained · pure"))

    def test_groups_carry_a_box_and_no_children(self):
        nodes, edges, notes, groups = model(STORY)
        payload = S.build_egn(STORY, nodes, edges, notes, groups, ICONS)
        g = next(e for e in payload["dst"] if e.get("type") == "domainStory:group")
        self.assertEqual(set(g), {"type", "name", "id", "pickedColor", "x", "y", "width", "height"})

    def test_the_icon_set_travels_with_the_file(self):
        nodes, edges, notes, groups = model(STORY)
        domain = S.build_egn(STORY, nodes, edges, notes, groups, ICONS)["domain"]
        self.assertIn("Dining", domain["workObjects"])
        self.assertIn("Person", domain["actors"])
        for name in ICONS["defaultWorkObjects"]:
            self.assertIn(name, domain["workObjects"])


# A CLI flag such as --share puts a double hyphen in a story, and an XML comment may not
# hold one. The note also names the markers Egon cuts the embedded story at.
DASHES = json.loads(json.dumps(STORY))
DASHES["description"] = "Run with --share, end with --quit-"
DASHES["sentences"][0]["object"]["name"] = "Coffee--decaf"
DASHES["sentences"][1]["note"] = "Pass --quit to stop; ---- or </DST> --> <!-- x"


def egon_import(svg):
    """A port of Egon's extractJsonFromSvgComment followed by its JSON.parse."""
    text = svg.replace("––", "--").replace("%3C", "<").replace("%3E", ">")
    body = text[:text.index("</DST>")][text.index("<DST>"):].replace("<DST>", "", 1)
    while "<!--" in body or "-->" in body:
        body = body.replace("<!--", "", 1).replace("-->", "", 1)
    return json.loads(body)


def texts_of(payload):
    return {e.get("text") or e.get("name") or e.get("info") for e in payload["dst"]}


class SurvivesDoubleHyphens(unittest.TestCase):
    def svg(self):
        return render(DASHES)["Order coffee.egn.svg"]

    def test_the_egn_svg_is_well_formed_xml(self):
        xml.dom.minidom.parseString(self.svg())

    def test_the_comment_holds_no_double_hyphen(self):
        svg = self.svg()
        body = svg[svg.index("<!--") + 4:svg.rindex("-->")]
        self.assertEqual([], re.findall(r".{0,15}--.{0,15}", body))
        # A comment may not end in "-" either, which the closing "</DST> " rules out.
        self.assertFalse(body.endswith("-"))

    def test_egon_reads_the_text_back_unchanged(self):
        texts = texts_of(egon_import(self.svg()))
        self.assertIn("Coffee--decaf", texts)
        self.assertIn(DASHES["sentences"][1]["note"], texts)

    def test_egn2spec_reads_the_text_back_unchanged(self):
        import egn2spec
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Order coffee.egn.svg"
            path.write_text(self.svg(), encoding="utf-8")
            spec, _ = egn2spec.convert(path)
        self.assertEqual(DASHES["description"], spec["description"])
        self.assertEqual("Coffee--decaf", spec["sentences"][0]["object"]["name"])
        self.assertEqual(DASHES["sentences"][1]["note"], spec["sentences"][1]["note"])


class MarksWhatChanged(unittest.TestCase):
    def marked(self):
        spec = json.loads(json.dumps(STORY))
        spec["sentences"][1]["changed"] = True
        return spec

    def test_the_egn_colours_only_the_marked_sentence(self):
        spec = self.marked()
        nodes, edges, notes, groups = model(spec)
        payload = S.build_egn(spec, nodes, edges, notes, groups, ICONS)
        coloured = [e for e in payload["dst"]
                    if isinstance(e, dict) and e.get("pickedColor") == S.ACCENT_EGN]
        # The work object of sentence 2, plus both of its arrows.
        self.assertEqual(sorted(e.get("name") for e in coloured), ["Cup", "serves", "to"])

    def test_egon_wants_eight_hex_digits(self):
        # Verified against WPS/egon.io-examples metropolis-1b-cinema-coarsegrained-colored.egn
        self.assertRegex(S.ACCENT_EGN, r"^#[0-9a-f]{8}$")

    def test_an_unmarked_story_carries_no_colour(self):
        nodes, edges, notes, groups = model(STORY)
        payload = S.build_egn(STORY, nodes, edges, notes, groups, ICONS)
        self.assertEqual([e for e in payload["dst"]
                          if isinstance(e, dict) and e.get("pickedColor") == S.ACCENT_EGN], [])

    def test_the_legend_appears_only_when_something_is_marked(self):
        self.assertIn("Highlighted:", render(self.marked())["Order coffee.egn.svg"])
        self.assertNotIn("Highlighted:", render(STORY)["Order coffee.egn.svg"])

    def test_an_unmarked_story_defines_no_accent_arrowhead(self):
        # Adding the marker unconditionally rewrote every committed example for nothing.
        self.assertNotIn("tip-accent", render(STORY)["Order coffee.egn.svg"])
        self.assertIn("tip-accent", render(self.marked())["Order coffee.egn.svg"])


class IsReproducible(unittest.TestCase):
    def test_rendering_twice_gives_the_same_bytes(self):
        self.assertEqual(render(STORY), render(STORY))

    def test_ids_are_positional_not_random(self):
        nodes, edges, notes, groups = model(STORY)
        payload = S.build_egn(STORY, nodes, edges, notes, groups, ICONS)
        self.assertEqual([e["id"] for e in payload["dst"] if "id" in e][:2],
                         ["shape_0001", "shape_0002"])


class RefusesBadSpecs(unittest.TestCase):
    def assert_rejected(self, spec, needle):
        with self.assertRaises(S.SpecError) as caught:
            S.validate(spec, ICONS["icons"])
        self.assertIn(needle, str(caught.exception))

    def test_undeclared_actor(self):
        spec = broken()
        spec["sentences"][0]["to"] = "Manager"
        self.assert_rejected(spec, "Manager")

    def test_unknown_icon(self):
        spec = broken()
        spec["sentences"][0]["object"]["icon"] = "Unicorn"
        self.assert_rejected(spec, "Unicorn")

    def test_a_work_object_drawn_with_an_actors_icon(self):
        spec = broken()
        spec["sentences"][0]["object"]["icon"] = "Person"
        self.assert_rejected(spec, "Person")

    def test_the_message_names_the_actor_already_wearing_the_icon(self):
        spec = broken()
        spec["sentences"][0]["object"]["icon"] = "Person"
        self.assert_rejected(spec, "Customer")

    def test_two_actors_may_share_one_icon(self):
        # The check must not leak into the actor loop, where one icon says nothing.
        spec = broken(actors=[{"name": "Customer", "icon": "Person"},
                              {"name": "Barista", "icon": "Person"}])
        S.validate(spec, ICONS["icons"])

    def test_two_work_objects_may_share_one_icon(self):
        # Two activities on one kind of thing, which is what the notation draws all the time.
        # The actors take distinct icons of their own, so only the work object rule is under test.
        spec = broken(actors=[{"name": "Customer", "icon": "Person"},
                              {"name": "Barista", "icon": "Group"}])
        for sentence in spec["sentences"]:
            sentence["object"]["icon"] = "Receipt"
        S.validate(spec, ICONS["icons"])

    def test_a_work_object_may_use_an_actor_icon_no_actor_holds(self):
        # The rule is per story. Tightening it to a global actor set would refuse four examples.
        spec = broken(actors=[{"name": "Till", "icon": "System"}], relations=[], groups=[])
        spec["sentences"] = [{"n": 1, "from": "Till", "verb": "seats",
                              "object": {"name": "Guest", "icon": "Person"}}]
        S.validate(spec, ICONS["icons"])

    def test_dangling_group_member(self):
        self.assert_rejected(broken(groups=[{"name": "X", "members": ["Nobody"]}]), "Nobody")

    def test_relation_to_an_unaddressable_work_object(self):
        # Work objects are per-sentence shapes, so only an explicit id can be referenced.
        self.assert_rejected(broken(relations=[{"from": "Cup", "to": "Customer"}]), "Cup")

    def test_actor_declared_twice(self):
        spec = broken()
        spec["actors"].append({"name": "Customer", "icon": "Person"})
        self.assert_rejected(spec, "declared twice")

    def test_actor_never_used(self):
        spec = broken()
        spec["actors"].append({"name": "Manager", "icon": "Person"})
        self.assert_rejected(spec, "never used")

    def test_a_top_level_field_the_generator_never_reads(self):
        # A notes list at the top reads like a real field and draws nothing.
        self.assert_rejected(broken(notes=[{"on": "Cup", "text": "Refilled once."}]), "'notes'")

    def test_a_misspelled_sentence_field(self):
        spec = broken()
        spec["sentences"][0]["annotation"] = "Only on weekdays."
        self.assert_rejected(spec, "'annotation'")

    def test_a_misspelled_work_object_field(self):
        spec = broken()
        spec["sentences"][0]["object"]["label"] = "Coffee"
        self.assert_rejected(spec, "'label'")

    def test_a_misspelled_actor_field(self):
        spec = broken()
        spec["actors"][0]["comment"] = "Regular."
        self.assert_rejected(spec, "'comment'")

    def test_a_misspelled_scope_field(self):
        spec = broken()
        spec["scope"]["detail"] = "coarse"
        self.assert_rejected(spec, "'detail'")

    def test_the_message_names_what_would_have_been_accepted(self):
        self.assert_rejected(broken(notes=[]), "sentences")


class OpensOnTheLeft(unittest.TestCase):
    """Sequence is carried by the numbers, but the eye still starts on the left."""

    def test_the_actor_who_speaks_first_sits_left_of_the_protagonist(self):
        nodes, _, _, _ = model(REPEATED_EXCHANGE)
        self.assertLess(nodes["Driver"].cx, nodes["Platform"].cx)

    def test_the_opening_sentence_runs_towards_the_middle(self):
        nodes, edges, _, _ = model(REPEATED_EXCHANGE)
        first = next(e for e in edges if e.number == 1)
        self.assertLess(first.src.cx, first.dst.cx)

    def test_later_actors_do_not_take_the_opening_position(self):
        # Platform touches every work object, so it is the protagonist and holds the middle.
        spec = json.loads(json.dumps(REPEATED_EXCHANGE))
        spec["actors"].append({"name": "Parking spot owner", "icon": "Person"})
        spec["sentences"].append(
            {"n": 5, "from": "Parking spot owner", "verb": "closes",
             "object": {"name": "Parking zone", "icon": "Place"}, "prep": "in", "to": "Platform"})
        nodes, _, _, _ = model(spec)
        self.assertLess(nodes["Driver"].cx, nodes["Parking spot owner"].cx)


class KeepsTheTitleBlockInside(unittest.TestCase):
    """The title block is drawn, not laid out, so it has to be measured on purpose."""

    LONG = ("The settled two-step alternative when an administrator needs to remove a "
            "parking spot with an outstanding offer. This story is derived from the "
            "approved removal decision and the reviewed plan, not from a domain workshop.")

    def head(self, spec):
        svg = next(iter(render(spec, formats=("svg",)).values()))
        box = re.search(r'viewBox="(-?\d+) (-?\d+) (\d+) (\d+)"', svg)
        left, width = int(box.group(1)), int(box.group(3))
        rows = re.findall(r'<text x="(-?\d+)" y="-?\d+" font-size="13" '
                          r'fill="#6b6b6b">([^<]*)</text>', svg)
        return left + width, rows

    def test_a_long_description_stays_inside_the_canvas(self):
        right, rows = self.head(broken(description=self.LONG))
        self.assertTrue(rows)
        for x, line in rows:
            # The band keeps 13px while the drawing's type grew, so it is measured
            # with its own advance rather than the drawing's.
            self.assertLessEqual(int(x) + len(line) * S.BAND_CHAR_W, right, line)

    def test_a_long_description_keeps_every_word(self):
        _, rows = self.head(broken(description=self.LONG))
        self.assertEqual(" ".join(line for _, line in rows), self.LONG)

    def test_the_band_grows_instead_of_the_canvas(self):
        plain = next(iter(render(broken(description="Short.")).values()))
        long = next(iter(render(broken(description=self.LONG)).values()))
        widths = [int(re.search(r'viewBox="-?\d+ -?\d+ (\d+) (\d+)"', s).group(1))
                  for s in (plain, long)]
        heights = [int(re.search(r'viewBox="-?\d+ -?\d+ (\d+) (\d+)"', s).group(2))
                   for s in (plain, long)]
        self.assertEqual(widths[0], widths[1])
        self.assertGreater(heights[1], heights[0])


class KeepsTheDrawingLegible(unittest.TestCase):
    # The one example placement cannot rescue is the oversized one, whose ring is crowded
    # enough that an object stays nearer a stranger wherever it is put. That is the story
    # being too big rather than the placement being wrong, and "Grant a building permit" is
    # 20 sentences where the skill says split at fifteen. Counted rather than skipped, so
    # either fixing it or making it worse shows up here. Seating the cast in reading order
    # took it from two to one.
    MISATTRIBUTED = {"oversized.story.json": 1}

    def test_nothing_overlaps_in_the_examples(self):
        here = Path(__file__).resolve().parent.parent / "examples"
        for spec_file in sorted(here.glob("*.story.json")):
            with self.subTest(story=spec_file.name):
                spec = S.load_spec(spec_file)
                nodes, edges, notes, groups = model(spec)
                found = S.check_geometry(nodes, edges, notes, spec["title"])
                known = [w for w in found if "which is not in its sentence" in w]
                self.assertEqual([w for w in found if w not in known], [])
                self.assertEqual(len(known), self.MISATTRIBUTED.get(spec_file.name, 0))

    def test_a_label_never_lands_on_a_foreign_shape(self):
        nodes, edges, notes, _ = model(STORY)
        for e in edges:
            if not e.label:
                continue
            box = S._verb_box(e)
            for n in nodes.values():
                if n is e.src or n is e.dst:
                    continue
                self.assertFalse(S._overlap(box, n.box, pad=0),
                                 f"{e.label!r} sits on {n.name!r}")

    def test_a_long_verb_sits_beside_its_arrow_rather_than_across_a_sibling(self):
        nodes, edges, notes, _ = model(FANNING_OUT)
        long_verb = next(e for e in edges if e.label.startswith("ends the occupation"))

        crossing = [o.label for o in edges
                    if o is not long_verb and S._crosses_box(S._verb_box(long_verb), S.sample(o.path))]

        self.assertEqual(crossing, [])
        self.assertFalse(any("is crossed by the arrow" in w
                             for w in S.check_geometry(nodes, edges, notes, FANNING_OUT["title"])))

    def test_a_verb_is_measured_from_a_point_its_text_covers(self):
        nodes, edges, _, _ = model(FANNING_OUT)
        long_verb = next(e for e in edges if e.label.startswith("ends the occupation"))
        (x, y), (bx, by, bw, bh) = long_verb.label_at, long_verb.label_box
        own = S._near_path(x, y, S.sample(long_verb.path))
        nearest_other = min(S._near_path(x, y, S.sample(o.path)) for o in edges if o is not long_verb)

        self.assertTrue(bx <= x <= bx + bw and by <= y <= by + bh + 4, (x, y, long_verb.label_box))
        self.assertGreater(nearest_other - own, S.OWN_ARROW_MARGIN)

    def test_a_verb_crossed_by_another_arrow_is_reported(self):
        nodes, edges, notes, _ = model(FANNING_OUT)
        ends, announces = (next(e for e in edges if e.label.startswith(v))
                           for v in ("ends the occupation", "announces"))
        (x1, y1), (x2, y2) = S.sample(announces.path)[0], S.sample(announces.path)[-1]
        _, _, w, h = ends.label_box
        ends.label_box = ((x1 + x2) / 2 - w / 2, (y1 + y2) / 2 - h / 2, w, h)

        with redirect_stderr(StringIO()):
            warnings = S.check_geometry(nodes, edges, notes, FANNING_OUT["title"])

        self.assertTrue(any(w.startswith("verb 'ends the occupation") and "is crossed by the arrow" in w
                            for w in warnings), warnings)

    def test_repeated_exchanges_between_two_actors_do_not_cross(self):
        nodes, edges, notes, _ = model(REPEATED_EXCHANGE)

        crossings = [(one, two) for i, one in enumerate(edges)
                     for two in edges[i + 1:] if S._paths_cross(one, two)]

        self.assertEqual(crossings, [])
        self.assertEqual(S.check_geometry(nodes, edges, notes, REPEATED_EXCHANGE["title"]), [])

    def test_a_pin_that_introduces_an_arrow_crossing_is_reported(self):
        spec = json.loads(json.dumps(REPEATED_EXCHANGE))
        for sentence, at in zip(spec["sentences"], ((-1, -2), (1, -1), (-1, 1), (1, 2))):
            sentence["object"]["at"] = at
        nodes, edges, notes, _ = model(spec)

        with redirect_stderr(StringIO()):
            warnings = S.check_geometry(nodes, edges, notes, spec["title"])

        self.assertTrue(any("pinned layout" in warning for warning in warnings))

    def test_a_note_never_covers_the_name_of_the_object_it_is_tied_to(self):
        spec = {
            "title": "Count parking spots",
            "actors": [
                {"name": "Parking (platform)", "icon": "System"},
                {"name": "Metrics collector", "icon": "System"},
                {"name": "Graphite", "icon": "System"},
            ],
            "sentences": [
                {"n": 1, "from": "Parking (platform)", "verb": "publishes",
                 "object": {"name": "Parking spot event", "icon": "Email"},
                 "prep": "to", "to": "Metrics collector",
                 "note": "Created, Deleted, Occupied, Vacated or OccupationCanceled, on the "
                         "tracing exchange. The binding is unchanged."},
                {"n": 2, "from": "Metrics collector", "verb": "reports each minute's",
                 "object": {"name": "Spot change counts", "icon": "Assessment"},
                 "prep": "to", "to": "Graphite",
                 "note": "Added, removed, taken into use and freed, in total and per parking "
                         "zone (metrics.kpi.parking_spots.*). Created counts as added, Deleted "
                         "as removed, Occupied as taken into use, and Vacated or "
                         "OccupationCanceled as freed."},
            ],
        }
        nodes, edges, notes, _ = model(spec)

        for note in notes:
            for n in nodes.values():
                self.assertFalse(S._overlap(note.box, n.label_box, pad=0),
                                 f"{note.name} covers the name {n.name!r}")


class SaysWhenANameIsCut(unittest.TestCase):
    LONG = "Subscription and the parking it follows"

    def named(self, name):
        return first_object_named(name)

    def test_a_work_object_the_picture_cannot_show_in_full_is_reported(self):
        self.assertTrue(any(self.LONG in warning for warning in self.named(self.LONG)))

    def test_the_warning_shows_what_the_reader_will_actually_see(self):
        self.assertIn("Subscription and the parking it…", self.named(self.LONG)[0])

    def test_the_warning_says_which_kind_of_shape_it_is(self):
        self.assertIn("work object", self.named(self.LONG)[0])

    def test_an_actor_is_reported_as_an_actor(self):
        long_actor = "Customer standing at the counter downstairs"
        spec = json.loads(json.dumps(STORY))
        spec["actors"][0]["name"] = long_actor
        spec["sentences"][0]["from"] = long_actor
        spec["sentences"][1]["to"] = long_actor
        spec["relations"][0]["to"] = long_actor
        nodes, _, _, _ = model(spec)

        with redirect_stderr(StringIO()):
            warnings = S.check_names(nodes, spec["title"])

        self.assertTrue(any(warning.startswith("actor ") for warning in warnings))

    def test_a_name_that_fits_is_not_reported(self):
        self.assertEqual([], self.named("Coffee"))

    def test_an_ellipsis_the_author_wrote_is_not_mistaken_for_a_cut(self):
        # Compared against the unlimited wrap rather than a trailing ellipsis, so a
        # name carrying one of its own still reads as complete.
        self.assertEqual([], self.named("Waiting…"))

    def test_renaming_the_work_object_clears_the_warning(self):
        self.assertEqual([], self.named("Subscription"))


class SaysWhenAShapeSharesAName(unittest.TestCase):
    """A domain may call a role and a thing by one noun, so this is worth saying and
    not worth refusing. The glyph and the position still tell the two shapes apart."""

    def named(self, name):
        return first_object_named(name)

    def test_a_work_object_with_an_actors_name_is_reported(self):
        self.assertTrue(any("Customer" in warning for warning in self.named("Customer")))

    def test_it_is_a_warning_and_not_a_refusal(self):
        spec = json.loads(json.dumps(STORY))
        spec["sentences"][0]["object"]["name"] = "Customer"
        S.validate(spec, ICONS["icons"])

    def test_a_distinct_name_is_not_reported(self):
        self.assertEqual([], self.named("Coffee"))

    def test_the_same_name_in_two_sentences_is_reported_once(self):
        # A work object is redrawn per activity, so the repeat is the normal case.
        spec = json.loads(json.dumps(STORY))
        for sentence in spec["sentences"]:
            sentence["object"]["name"] = "Customer"

        self.assertEqual(1, len(dict.fromkeys(names_reported(spec))))


class SeatsTheCastInReadingOrder(unittest.TestCase):
    """Sequence is carried by the numbers, but the eye still has to travel between them,
    and first-appearance order can put sentence five on the right and six on the left."""

    def seating(self, spec):
        nodes, _, _, _ = model(spec)
        actors = [n for n in nodes.values() if n.kind == "actor"]
        return [a.name for a in sorted(actors, key=lambda a: math.atan2(a.cy, a.cx))]

    def travel(self, spec):
        nodes, edges, _, _ = model(spec)
        return S._eye_travel(S._steps(edges), {n.key: (n.cx, n.cy) for n in nodes.values()})

    def test_every_seating_considered_keeps_the_opener_in_its_seat(self):
        # The one hard constraint. Every candidate has to respect it, not just the winner.
        spec = json.loads((EXAMPLES / "oversized.story.json").read_text())
        nodes, edges, _, _ = model(spec)
        opener = min((e for e in edges if e.number is not None), key=lambda e: e.number).src
        _, ranked = S._seatings(nodes, edges)

        self.assertTrue(ranked)
        for _, seating in ranked:
            self.assertEqual(opener.key, seating[0])

    def test_a_better_seating_is_taken_when_one_exists(self):
        # Asserted as a comparison, so it survives the layout being retuned.
        spec = json.loads((EXAMPLES / "oversized.story.json").read_text())
        nodes, edges, notes, _ = model(spec)
        steps = S._steps(edges)
        chosen = S._eye_travel(steps, {n.key: (n.cx, n.cy) for n in nodes.values()})

        today, _ = S._seatings(nodes, edges)
        with redirect_stderr(StringIO()):
            S.place(nodes, edges, notes, seating=today)
        first_appearance = S._eye_travel(steps, {n.key: (n.cx, n.cy) for n in nodes.values()})

        self.assertLess(chosen, first_appearance)

    def test_a_story_with_one_outer_actor_is_left_alone(self):
        # Nothing to permute, so the search must not touch the drawing.
        nodes, _, _, _ = model(REPEATED_EXCHANGE)
        outer = [n for n in nodes.values() if n.kind == "actor" and (n.cx or n.cy)]

        self.assertEqual(1, len(outer))

    def test_the_seating_never_costs_the_drawing(self):
        # Compared against the seating the ring gave, not against a ceiling, so a search
        # that took a clean story to two faults would fail here.
        for spec_file in sorted(EXAMPLES.glob("*.story.json")):
            with self.subTest(story=spec_file.name):
                spec = json.loads(spec_file.read_text())
                nodes, edges, notes, _ = model(spec)
                with redirect_stderr(StringIO()):
                    chosen = S.check_geometry(nodes, edges, notes, "", quiet=True)
                    today, _ = S._seatings(nodes, edges)
                    S.place(nodes, edges, notes, seating=today)
                    before = S.check_geometry(nodes, edges, notes, "", quiet=True)

                self.assertLessEqual(len(chosen), len(before))

    def test_a_story_with_relations_seats_every_actor(self):
        # The layout and the search once counted a protagonist two different ways, because a
        # relation joins a work object to an actor without appearing in any sentence. They
        # then disagreed about who sits in the middle and one actor kept a stale seat.
        spec = json.loads(json.dumps(STORY))
        spec["relations"] = [{"from": "coffee", "verb": "is paid by", "to": "Customer"},
                             {"from": "coffee", "verb": "is made by", "to": "Barista"}]
        nodes, edges, _, _ = model(spec)

        hero, others, _ = S._cast(nodes, edges)
        seats = [(n.cx, n.cy) for n in nodes.values() if n.kind == "actor"]

        self.assertEqual(len(seats), len(set(seats)))
        self.assertEqual({hero.key} | {a.key for a in others},
                         {n.key for n in nodes.values() if n.kind == "actor"})

    def test_a_seating_that_names_the_wrong_cast_is_refused(self):
        nodes, edges, notes, _ = model(STORY)

        with self.assertRaises(S.SpecError):
            S.place(nodes, edges, notes, seating=["Nobody"])

    def test_a_cast_too_big_to_rank_is_left_alone(self):
        # Ranking is factorial, so past the cap the story keeps the seating the ring gave it
        # rather than the generator hanging on a story the skill says to split anyway.
        actors = [{"name": "Hub", "icon": "System"}]
        sentences = []
        for i in range(S.SEATING_LIMIT + 2):
            actors.append({"name": f"A{i}", "icon": "Person"})
            sentences.append({"n": i + 1, "from": f"A{i}", "verb": "asks",
                              "object": {"name": f"W{i}", "icon": "Document"},
                              "prep": "of", "to": "Hub"})
        spec = {"title": "A crowd", "actors": actors, "sentences": sentences}
        nodes, edges, _, _ = model(spec)

        today, ranked = S._seatings(nodes, edges)

        self.assertEqual([], ranked)
        self.assertEqual(S.SEATING_LIMIT + 2, len(today))

    def test_a_crossing_free_seating_is_taken_when_one_exists(self):
        # Long reach keeps two crossings under first-appearance order and none under the
        # seating the search settles on, which is the whole reason it judges faults first.
        nodes, edges, _, _ = model(json.loads((EXAMPLES / "long-reach.story.json").read_text()))

        self.assertEqual(0, S._crossings(edges))

    def test_the_seating_never_adds_a_crossing(self):
        # check_geometry only reports a crossing when a shape is pinned, so the guard has to
        # count them itself. Without that it once took a clean story to one crossing unseen.
        for spec_file in sorted(EXAMPLES.glob("*.story.json")):
            with self.subTest(story=spec_file.name):
                spec = json.loads(spec_file.read_text())
                nodes, edges, notes, _ = model(spec)
                chosen = S._crossings(edges)
                today, _ = S._seatings(nodes, edges)
                with redirect_stderr(StringIO()):
                    S.place(nodes, edges, notes, seating=today)

                self.assertLessEqual(chosen, S._crossings(edges))

    def test_a_pinned_work_object_stays_where_it_was_pinned(self):
        # A pin is the last resort after a warning, so the relaxation must not shove it.
        spec = json.loads(json.dumps(STORY))
        spec["sentences"][0]["object"]["at"] = [0, 0]
        nodes, _, _, _ = model(spec)
        pinned = nodes["coffee"]

        self.assertEqual((0.0, 0.0), (pinned.cx, pinned.cy))

    def test_the_same_story_seats_the_same_way_twice(self):
        spec = json.loads((EXAMPLES / "oversized.story.json").read_text())

        self.assertEqual(self.seating(spec), self.seating(spec))


class SaysWhenAPictureIsTooWideToRead(unittest.TestCase):
    """A markdown image is force-fit to its column, so width decides whether the names
    survive. Nothing else in the generator measures that."""

    def reading(self, spec):
        nodes, _, notes, _ = model(spec)
        with redirect_stderr(StringIO()):
            return S.check_reading(nodes, notes, spec["title"])

    def test_a_story_too_wide_to_read_is_reported(self):
        warnings = self.reading(json.loads((EXAMPLES / "oversized.story.json").read_text()))

        self.assertTrue(any("wide" in w for w in warnings), warnings)

    def wide(self, with_notes):
        # Built by hand rather than rendered, because the stories that reach this width
        # carry enough annotations that drawing one takes the best part of a minute.
        left = S.Node("Left", "actor", "Left", "Person", None)
        right = S.Node("Right", "actor", "Right", "Person", None)
        left.cx, left.cy, right.cx, right.cy = -900.0, 0.0, 900.0, 0.0
        notes = []
        if with_notes:
            note = S.Note("__n0", "x" * 200, left)
            note.cx, note.cy = -1400.0, 0.0
            notes.append(note)
        with redirect_stderr(StringIO()):
            return S.check_reading({"Left": left, "Right": right}, notes, "Wide")

    def test_it_names_the_annotations_when_they_are_what_widened_it(self):
        # A split does nothing to a wall of notes, so the advice depends on which it is.
        self.assertTrue(any("annotation adds" in w for w in self.wide(with_notes=True)))

    def test_it_blames_the_cast_when_the_notes_are_not_the_problem(self):
        self.assertTrue(any("work objects set that" in w for w in self.wide(with_notes=False)))

    def test_the_warning_says_what_the_reader_will_get(self):
        warnings = self.reading(json.loads((EXAMPLES / "oversized.story.json").read_text()))

        self.assertRegex(warnings[0], r"\d+px wide")
        self.assertRegex(warnings[0], r"render at \d+\.\dpx")

    def test_an_ordinary_story_is_not_reported(self):
        self.assertEqual([], self.reading(STORY))
        self.assertEqual([], self.reading(REPEATED_EXCHANGE))

    def test_every_other_example_reads_in_a_column(self):
        for spec_file in sorted(EXAMPLES.glob("*.story.json")):
            if spec_file.name == "oversized.story.json":
                continue
            with self.subTest(story=spec_file.name):
                self.assertEqual([], self.reading(json.loads(spec_file.read_text())))

    def test_it_reaches_stderr_under_a_reading_prefix(self):
        spec = json.loads((EXAMPLES / "oversized.story.json").read_text())
        nodes, _, notes, _ = model(spec)
        buffer = StringIO()
        with redirect_stderr(buffer):
            S.check_reading(nodes, notes, spec["title"])

        self.assertIn("reading:", buffer.getvalue())

    def test_the_width_it_reports_is_the_width_it_renders(self):
        spec = json.loads((EXAMPLES / "oversized.story.json").read_text())
        nodes, _, notes, _ = model(spec)
        min_x, _, max_x, _ = S.canvas_box(nodes, notes)
        svg = next(iter(render(spec, formats=("svg",)).values()))

        self.assertIn(f'width="{max_x - min_x:.0f}"', svg)


class SaysWhenAStoryBranches(unittest.TestCase):
    """The notation has no conditional, so two branches numbered into one sequence
    draw a process that contradicts itself and no caption can undo it."""

    def warnings(self, spec):
        with redirect_stderr(StringIO()):
            return S.check_branching(spec, spec.get("title", "probe"))

    def linear(self, **sentence):
        spec = json.loads(json.dumps(STORY))
        spec["sentences"][0].update(sentence)
        return spec

    def test_the_story_that_shipped_wrong_is_reported(self):
        self.assertEqual(len(self.warnings(BRANCHED)), 1)

    def test_the_warning_names_both_sentences(self):
        self.assertIn("sentences 4 and 6", self.warnings(BRANCHED)[0])

    def test_the_warning_names_the_fix_and_not_only_the_smell(self):
        self.assertIn("one story per branch", self.warnings(BRANCHED)[0])

    def test_the_warning_shows_the_evidence_it_found(self):
        warning = self.warnings(BRANCHED)[0]
        self.assertIn("'Parking system' acting on 'Allocation'", warning)
        self.assertIn("depends on which", warning)

    def test_it_reaches_stderr_under_a_branch_prefix(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            S.check_branching(BRANCHED, BRANCHED["title"])
        self.assertIn("Cancel a parking spot allocation: branch: sentences 4 and 6",
                      stderr.getvalue())

    def test_a_note_carrying_the_branch_is_enough(self):
        spec = json.loads(json.dumps(BRANCHED))
        del spec["description"]
        self.assertIn("any other", self.warnings(spec)[0])

    def test_branch_wording_alone_is_reported_when_no_two_sentences_rival(self):
        spec = json.loads(json.dumps(STORY))
        spec["description"] = "Card or cash, depending on what the customer has."
        self.assertIn("the description says 'depending on'", self.warnings(spec)[0])

    def test_one_warning_per_pair_rather_than_one_per_word(self):
        # The description and both notes all read as a branch, and the reader needs the
        # pair once, not three times over.
        self.assertEqual(len(self.warnings(BRANCHED)), 1)

    def test_an_ordinary_story_is_not_reported(self):
        self.assertEqual([], self.warnings(STORY))

    def test_the_examples_are_not_reported(self):
        here = Path(__file__).resolve().parent.parent / "examples"
        specs = sorted(here.glob("*.story.json"))
        self.assertTrue(specs, "no example specs found, so this control checked nothing")
        for spec_file in specs:
            with self.subTest(story=spec_file.name):
                spec = S.load_spec(spec_file)
                self.assertEqual([], self.warnings(spec))

    def test_when_in_a_note_does_not_warn_on_a_linear_story(self):
        spec = self.linear(note="Only when the customer is drinking in.")
        self.assertEqual([], self.warnings(spec))

    def test_unchanged_in_a_note_does_not_warn_on_a_linear_story(self):
        self.assertEqual([], self.warnings(self.linear(note="The price is unchanged.")))

    def test_neither_is_not_read_as_either(self):
        spec = json.loads(json.dumps(STORY))
        spec["description"] = "Neither step waits for the other."
        self.assertEqual([], self.warnings(spec))

    def test_one_actor_twice_on_one_object_is_a_sequence_not_a_branch(self):
        # Sales opens a case and closes it nine steps later. Three of the six examples
        # do this, so the structure alone must stay silent.
        spec = json.loads(json.dumps(STORY))
        spec["sentences"].append(
            {"n": 3, "from": "Customer", "verb": "returns",
             "object": {"name": "Coffee", "icon": "Dining"}})
        self.assertEqual([], self.warnings(spec))

    def test_a_condition_word_turns_that_pair_into_a_warning(self):
        spec = json.loads(json.dumps(STORY))
        spec["sentences"].append(
            {"n": 3, "from": "Customer", "verb": "returns",
             "object": {"name": "Coffee", "icon": "Dining"},
             "note": "Unless the cup was already empty."})
        self.assertIn("sentences 1 and 3", self.warnings(spec)[0])

    def rivals_numbered(self, n_two):
        """A rival pair whose only branch signal is a condition word on a note.

        A condition word never warns on its own, so the pair is the only thing that
        can, which is what makes the two tests below discriminate.
        """
        spec = json.loads(json.dumps(STORY))
        spec["sentences"].append(
            {"n": n_two, "from": "Customer", "verb": "returns",
             "object": {"name": "Coffee", "icon": "Dining"},
             "note": "Unless the cup was already empty."})
        return spec

    def test_a_repeated_number_is_parallel_and_never_reported_for_it(self):
        # `n` repeated across two sentences is how the spec says they happen at once,
        # so the author has already ruled out one or the other.
        self.assertEqual([], self.warnings(self.rivals_numbered(1)))

    def test_the_same_pair_under_two_numbers_is_reported(self):
        # The other half of the test above, so a pass cannot come from the pair never
        # having been a rival in the first place.
        self.assertIn("sentences 1 and 3", self.warnings(self.rivals_numbered(3))[0])

    def test_both_arms_of_a_fork_named_in_one_breath_are_reported(self):
        spec = json.loads(json.dumps(STORY))
        spec["description"] = "Either approval is recorded or rejection is sent."
        self.assertIn("either ... or", self.warnings(spec)[0])

    def test_whether_or_not_means_it_happens_regardless(self):
        spec = json.loads(json.dumps(STORY))
        spec["sentences"][0]["note"] = "Sent whether or not the customer opted in."
        self.assertEqual([], self.warnings(spec))

    def test_a_note_that_is_not_text_renders_rather_than_raising(self):
        # A numeric note drew a picture before this check existed and still has to.
        self.assertEqual([], self.warnings(self.linear(note=500)))

    def test_a_description_naming_an_alternative_is_usually_the_split_already_done(self):
        # This is the description on KeepsTheTitleBlockInside.LONG, written for a real
        # story, and it warned until "alternative" stopped counting on its own.
        spec = json.loads(json.dumps(STORY))
        spec["description"] = ("The settled two-step alternative when an administrator "
                               "needs to remove a parking spot with an outstanding offer.")
        self.assertEqual([], self.warnings(spec))

    def test_rendering_a_branched_story_still_succeeds(self):
        with redirect_stderr(StringIO()):
            written = render(BRANCHED)
        self.assertTrue(written)


# Rendered without any warning during a PR review, although it held two processes in one
# sequence, a compound verb, a notification to nobody and a work object drawn as a person.
INACTIVE_DRIVER = {
    "title": "Inactive driver with queue place kept",
    "scope": {"granularity": "fine-grained", "time": "TO-BE", "purity": "digitalized"},
    "actors": [{"name": "Notify job", "icon": "Time"}, {"name": "Offer service", "icon": "Store"},
               {"name": "Queueing", "icon": "System"}, {"name": "Expiry deleter", "icon": "Security"}],
    "sentences": [
        {"n": 1, "from": "Notify job", "verb": "reads every",
         "object": {"name": "Subscription queue", "icon": "Folder"}, "prep": "from",
         "to": "Offer service", "note": "Once per non-empty batch", "changed": True},
        {"n": 2, "from": "Notify job", "verb": "asks per driver for",
         "object": {"name": "Parking spot queue positions", "icon": "Place"}, "prep": "from",
         "to": "Queueing", "changed": True},
        {"n": 3, "from": "Notify job", "verb": "skips queue holders, notifies the rest",
         "object": {"name": "Expiration attempt", "icon": "Document"}, "changed": True},
        {"n": 4, "from": "Expiry deleter", "verb": "locks for update",
         "object": {"name": "Driver account", "icon": "Person"}, "changed": True},
        {"n": 5, "from": "Expiry deleter", "verb": "asks for rankings",
         "object": {"name": "Queue places", "icon": "Place"}, "prep": "from",
         "to": "Offer service", "note": "Holder: refused, notification removed", "changed": True},
    ],
}


def quietly(check, spec, *args):
    with redirect_stderr(StringIO()):
        return check(spec, *args, spec.get("title", "probe"))


@functools.lru_cache(maxsize=None)
def example_specs():
    specs = sorted(EXAMPLES.glob("*.story.json"))
    assert specs, "no example specs found, so a control over them checks nothing"
    return tuple((f.name, S.load_spec(f)) for f in specs)


class SaysWhenAStoryHoldsTwoProcesses(unittest.TestCase):

    def gaps(self, spec):
        return quietly(S.check_gaps, spec)

    def test_the_story_that_shipped_wrong_is_reported(self):
        self.assertEqual(
            ["'Expiry deleter' first acts in sentence 4 and no earlier sentence hands it "
             "anything, so the story is missing the sentence that hands it the work, "
             "or it is two stories"],
            self.gaps(INACTIVE_DRIVER))

    def test_a_system_handed_work_earlier_is_not_reported(self):
        # Platform first acts in sentence 2, after the driver's request reaches it in sentence 1.
        self.assertEqual([], self.gaps(REPEATED_EXCHANGE))

    def test_the_same_system_with_nothing_handed_to_it_is_reported(self):
        # The other half of the test above, so its silence comes from the hand-over.
        spec = json.loads(json.dumps(REPEATED_EXCHANGE))
        for s in spec["sentences"]:
            if s.get("to") == "Platform":
                del s["to"], s["prep"]
        self.assertIn("'Platform' first acts in sentence 2", self.gaps(spec)[0])

    def test_actors_sharing_the_opening_number_are_not_reported(self):
        spec = json.loads(json.dumps(INACTIVE_DRIVER))
        spec["sentences"][3]["n"] = 1
        spec["sentences"][4]["n"] = 1
        self.assertEqual([], self.gaps(spec))

    def test_a_person_stepping_in_on_their_own_is_not_reported(self):
        spec = json.loads(json.dumps(INACTIVE_DRIVER))
        spec["actors"][3]["icon"] = "Person"
        spec["sentences"][3]["object"]["icon"] = "Document"
        self.assertEqual([], self.gaps(spec))


class SaysWhenAVerbIsWrong(unittest.TestCase):

    def verbs(self, spec):
        return quietly(S.check_verbs, spec)

    def with_verb(self, verb, to=True):
        spec = json.loads(json.dumps(STORY))
        spec["sentences"][0]["verb"] = verb
        if not to:
            del spec["sentences"][0]["to"], spec["sentences"][0]["prep"]
        return spec

    def test_the_story_that_shipped_wrong_is_reported_twice_for_sentence_three(self):
        self.assertEqual(
            ["sentence 3 has the verb 'skips queue holders, notifies the rest', which reads "
             "as two activities, so write two sentences, sharing a number only when neither "
             "waits for the other",
             "sentence 3 has the verb 'skips queue holders, notifies the rest' and no 'to', "
             "so the actor it reaches is missing from the story"],
            self.verbs(INACTIVE_DRIVER))

    def test_a_standalone_and_is_two_activities(self):
        self.assertIn("two activities", self.verbs(self.with_verb("checks and approves"))[0])

    def test_and_inside_a_word_is_one_activity(self):
        self.assertEqual([], self.verbs(self.with_verb("hands over")))
        self.assertEqual([], self.verbs(self.with_verb("understands")))

    def test_a_null_to_is_no_recipient(self):
        spec = self.with_verb("notifies", to=False)
        spec["sentences"][0]["to"] = None
        self.assertIn("no 'to'", self.verbs(spec)[0])

    def test_a_communication_verb_with_a_recipient_is_not_reported(self):
        self.assertEqual([], self.verbs(self.with_verb("notifies")))

    def test_a_communication_stem_is_matched_whatever_its_ending(self):
        for verb in ("Sends", "reminded", "e-mails", "invites"):
            with self.subTest(verb=verb):
                self.assertIn("no 'to'", self.verbs(self.with_verb(verb, to=False))[0])

    def test_a_stem_inside_another_word_is_not_communication(self):
        self.assertEqual([], self.verbs(self.with_verb("represents", to=False)))


class SaysWhenAWorkObjectLooksLikeAnActor(unittest.TestCase):

    def icons(self, spec):
        return quietly(S.check_icons, spec, ICONS["defaultActors"])

    def test_the_story_that_shipped_wrong_is_reported(self):
        self.assertEqual(
            ["work object 'Driver account' in sentence 4 is drawn as 'Person', an actor "
             "icon, so it reads as an actor, pick a work-object icon"],
            self.icons(INACTIVE_DRIVER))

    def test_an_icon_only_usually_an_actor_is_not_reported(self):
        # `Business` is the actor Bank in one example and the work object Account in another.
        spec = json.loads(json.dumps(INACTIVE_DRIVER))
        spec["sentences"][3]["object"]["icon"] = "Business"
        self.assertEqual([], self.icons(spec))

    def test_rendering_the_story_still_succeeds(self):
        with redirect_stderr(StringIO()):
            written = render(INACTIVE_DRIVER)
        self.assertTrue(written)


class ReportsModellingFaultsUnderTheirOwnPrefix(unittest.TestCase):

    CHECKS = (("gap", S.check_gaps, ()), ("verb", S.check_verbs, ()),
              ("icon", S.check_icons, (ICONS["defaultActors"],)))

    def test_each_check_reaches_stderr_under_its_prefix(self):
        for prefix, check, args in self.CHECKS:
            with self.subTest(prefix=prefix):
                stderr = StringIO()
                with redirect_stderr(stderr):
                    check(INACTIVE_DRIVER, *args, INACTIVE_DRIVER["title"])
                self.assertIn(f"Inactive driver with queue place kept: {prefix}: ", stderr.getvalue())

    def test_the_examples_are_not_reported(self):
        for prefix, check, args in self.CHECKS:
            for name, spec in example_specs():
                with self.subTest(prefix=prefix, story=name):
                    self.assertEqual([], quietly(check, spec, *args))


class SeatsWorkObjectsWithTheirOwnActors(unittest.TestCase):
    """Two actors facing each other across the ring used to drop their object on the hub."""

    STORY = {
        "title": "Across the ring",
        "actors": [{"name": "Hub", "icon": "System"}, {"name": "Left", "icon": "Person"},
                   {"name": "Right", "icon": "Person"}],
        "sentences": [
            {"n": 1, "from": "Left", "verb": "sends", "object": {"name": "One", "icon": "Document"}, "prep": "to", "to": "Hub"},
            {"n": 2, "from": "Hub", "verb": "sends", "object": {"name": "Two", "icon": "Document"}, "prep": "to", "to": "Right"},
            {"n": 3, "from": "Right", "verb": "hands", "object": {"name": "Parcel", "icon": "Document"}, "prep": "to", "to": "Left"},
        ],
    }

    def _distances(self):
        nodes, edges, notes, _ = model(self.STORY)
        parcel = next(n for n in nodes.values() if n.name == "Parcel")
        actors = {n.name: n for n in nodes.values() if n.kind == "actor"}
        return {name: math.hypot(parcel.cx - a.cx, parcel.cy - a.cy) for name, a in actors.items()}

    def test_the_object_sits_nearer_its_own_actors_than_a_stranger(self):
        d = self._distances()
        self.assertLessEqual(max(d["Right"], d["Left"]), d["Hub"])

    def test_the_check_names_the_stranger_when_placement_cannot_reach(self):
        nodes, edges, notes, _ = model(self.STORY)
        parcel = next(n for n in nodes.values() if n.name == "Parcel")
        stranger = next(n for n in nodes.values() if n.kind == "actor" and n.name == "Hub")
        parcel.cx, parcel.cy = stranger.cx, stranger.cy + 40   # where the midpoint used to land
        found = S.check_geometry(nodes, edges, notes, "Across the ring")
        self.assertIn("work object 'Parcel' sits nearer 'Hub', which is not in its sentence", found)


class ReadsTheSentencePrepositionBackFromADrawnStory(unittest.TestCase):

    REFUND = EXAMPLES / "Refund a disputed parking.egn"

    def convert(self, relabel=None):
        import egn2spec
        data = egn2spec.egn2text.extract(self.REFUND)
        for relation in data["relations"]:
            relation["verb"] = (relabel or {}).get(relation["verb"], relation["verb"])
        with mock.patch.object(egn2spec.egn2text, "extract", return_value=data):
            return egn2spec.convert(self.REFUND)

    def test_the_one_word_arrow_is_the_preposition_beside_a_drawn_relation(self):
        # Dispute and Refund each also carry an arrow back to an actor, "is raised by" and
        # "is approved by", which a reader draws and no sentence says
        spec, _ = self.convert()
        found = {s["n"]: (s.get("prep"), s.get("to")) for s in spec["sentences"]}
        self.assertEqual(("in", "Case tool"), found[2])
        self.assertEqual(("in", "Case tool"), found[4])

    def test_the_converted_story_has_no_gap(self):
        spec, _ = self.convert()
        self.assertEqual([], quietly(S.check_gaps, spec))

    def test_a_one_word_relation_is_not_taken_for_a_preposition(self):
        spec, warnings = self.convert({"in": "together with", "is raised by": "notifies"})
        self.assertNotIn("to", spec["sentences"][1])
        self.assertIn("sentence 2 leaves 'Dispute' by 2 arrows, so it kept no preposition", warnings)

    def test_two_one_word_arrows_stay_ambiguous(self):
        spec, warnings = self.convert({"is raised by": "from"})
        self.assertNotIn("to", spec["sentences"][1])
        self.assertIn("sentence 2 leaves 'Dispute' by 2 arrows, so it kept no preposition", warnings)


class PreviewsAwayFromTheFilesThatShip(unittest.TestCase):
    def generate(self, formats):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec = root / "story.json"
            spec.write_text(json.dumps(STORY), encoding="utf-8")

            def fake_png(svg_path, png_path):
                Path(png_path).write_bytes(b"png")
                return True

            with mock.patch.object(S, "to_png", side_effect=fake_png) as to_png:
                written = S.generate(spec, formats, root / "out", False, preview_dir=root / "look" / "here")
            return (root, written, sorted(p.name for p in (root / "out").iterdir()),
                    sorted(p.name for p in (root / "look" / "here").iterdir()), to_png)

    def test_one_png_lands_in_the_preview_directory_without_png_among_the_formats(self):
        root, written, _, previewed, to_png = self.generate(["egn.svg"])
        self.assertEqual(["Order coffee.png"], previewed)
        self.assertEqual(1, to_png.call_count)
        self.assertIn(root / "look" / "here" / "Order coffee.png", written)

    def test_no_png_lands_in_the_out_directory_without_png_among_the_formats(self):
        _, written, shipped, _, _ = self.generate(["egn.svg"])
        self.assertEqual(["Order coffee.egn.svg"], shipped)
        self.assertEqual(1, sum(p.suffix == ".png" for p in written))

    def test_a_png_format_still_ships_its_own_png_next_to_the_preview(self):
        root, written, shipped, previewed, to_png = self.generate(["egn.svg", "png"])
        self.assertIn("Order coffee.png", shipped)
        self.assertEqual(["Order coffee.png"], previewed)
        self.assertEqual(2, to_png.call_count)
        self.assertEqual(2, sum(p.suffix == ".png" for p in written))

    def test_the_command_line_prints_the_preview_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec = root / "story.json"
            spec.write_text(json.dumps(STORY), encoding="utf-8")
            out = StringIO()
            with mock.patch.object(S, "to_png", side_effect=lambda s, p: Path(p).write_bytes(b"png") or True), \
                    mock.patch("sys.stdout", out):
                code = S.main([str(spec), "--format", "egn.svg", "--out-dir", str(root / "out"),
                               "--preview", str(root / "look")])
            self.assertEqual(0, code or 0)
            printed = out.getvalue().splitlines()
            self.assertIn(str(root / "look" / "Order coffee.png"), printed)
            self.assertFalse((root / "out" / "Order coffee.png").exists())


def viewbox_of(svg):
    root = re.search(r"<svg\b[^>]*>", svg).group(0)
    width, height = (float(v) for v in re.search(r'width="([\d.]+)" height="([\d.]+)"', root).groups())
    x, y, w, h = (float(v) for v in re.search(r'viewBox="([^"]+)"', root).group(1).split())
    return width, height, (x, y, w, h)


class ScalesQuickLookToASquare(unittest.TestCase):
    def thumbnail(self, svg):
        """Run _quick_look_png against a stand-in qlmanage, and say what it was handed."""
        seen = {}

        def qlmanage(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["svg"] = Path(cmd[-1]).read_text(encoding="utf-8")
            (Path(cmd[cmd.index("-o") + 1]) / "picture.svg.png").write_bytes(b"thumbnail")

        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "in.svg", Path(d) / "out.png"
            src.write_text(svg, encoding="utf-8")
            with mock.patch.object(S.subprocess, "run", side_effect=qlmanage):
                self.assertTrue(S._quick_look_png(src, dst))
            seen["png"] = dst.read_bytes()
        return seen

    def wide(self):
        return render(STORY, ("svg",))["Order coffee.svg"]

    def test_the_picture_handed_over_is_square(self):
        width, height, (_, _, w, h) = viewbox_of(self.thumbnail(self.wide())["svg"])
        self.assertEqual(width, height)
        self.assertEqual(w, h)

    def test_the_square_keeps_the_whole_wide_picture_centred(self):
        _, _, (x, y, w, h) = viewbox_of(self.wide())
        self.assertGreater(w, h)
        _, _, (sx, sy, sw, sh) = viewbox_of(self.thumbnail(self.wide())["svg"])
        self.assertEqual(max(w, h), sw)
        self.assertLessEqual(sx, x)
        self.assertLessEqual(sy, y)
        self.assertGreaterEqual(sx + sw, x + w)
        self.assertGreaterEqual(sy + sh, y + h)
        self.assertAlmostEqual(sx + sw / 2, x + w / 2)
        self.assertAlmostEqual(sy + sh / 2, y + h / 2)

    def test_a_tall_picture_is_squared_sideways(self):
        tall = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="600" '
                'viewBox="-10 30 200 600" version="1.2"><rect/></svg>')
        _, _, (sx, sy, sw, sh) = viewbox_of(self.thumbnail(tall)["svg"])
        self.assertEqual((600, 600), (sw, sh))
        self.assertEqual((-210, 30), (sx, sy))

    def test_the_thumbnail_size_is_the_side_of_the_square(self):
        seen = self.thumbnail(self.wide())
        width, _, _ = viewbox_of(seen["svg"])
        self.assertEqual("qlmanage", seen["cmd"][0])
        self.assertEqual(str(math.ceil(width)), seen["cmd"][seen["cmd"].index("-s") + 1])

    def test_the_size_rounds_up_to_whole_pixels(self):
        odd = ('<svg xmlns="http://www.w3.org/2000/svg" width="300.4" height="100" '
               'viewBox="0 0 300.4 100"></svg>')
        seen = self.thumbnail(odd)
        self.assertEqual("301", seen["cmd"][seen["cmd"].index("-s") + 1])

    def test_the_thumbnail_ends_up_at_the_png_path(self):
        self.assertEqual(b"thumbnail", self.thumbnail(self.wide())["png"])

    def test_a_missing_thumbnail_is_reported_and_leaves_no_png(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "in.svg", Path(d) / "out.png"
            src.write_text(self.wide(), encoding="utf-8")
            err = StringIO()
            with mock.patch.object(S.subprocess, "run"), redirect_stderr(err):
                self.assertFalse(S._quick_look_png(src, dst))
            self.assertFalse(dst.exists())
            self.assertIn("qlmanage wrote no thumbnail", err.getvalue())


class FallsBackToQuickLookLast(unittest.TestCase):
    def to_png(self, present):
        def which(tool):
            return f"/usr/bin/{tool}" if tool in present else None

        with mock.patch.object(S.shutil, "which", side_effect=which), \
                mock.patch.object(S.subprocess, "run") as run, \
                mock.patch.object(S, "_quick_look_png", return_value=True) as quick_look, \
                redirect_stderr(StringIO()) as err:
            result = S.to_png("in.svg", "out.png")
        return result, run, quick_look, err.getvalue()

    def test_quick_look_serves_when_only_qlmanage_is_present(self):
        result, run, quick_look, _ = self.to_png({"qlmanage"})
        self.assertTrue(result)
        quick_look.assert_called_once_with("in.svg", "out.png")
        run.assert_not_called()

    def test_each_other_converter_wins_over_quick_look(self):
        for tool in ("rsvg-convert", "cairosvg", "inkscape"):
            with self.subTest(tool=tool):
                result, run, quick_look, _ = self.to_png({tool, "qlmanage"})
                self.assertTrue(result)
                quick_look.assert_not_called()
                self.assertEqual(tool, run.call_args.args[0][0])

    def test_nothing_present_says_which_converters_to_install(self):
        result, run, quick_look, err = self.to_png(set())
        self.assertFalse(result)
        quick_look.assert_not_called()
        run.assert_not_called()
        self.assertIn("no converter found", err)


class ListsEveryIconInTheSpec(unittest.TestCase):
    def listed(self):
        spec = (Path(__file__).resolve().parent.parent / "references" / "spec.md").read_text(encoding="utf-8")
        section = spec.split("\n## Icons\n", 1)[1].split("\n## ", 1)[0]
        line = next(l for l in section.splitlines() if l.startswith("`") and l.endswith("."))
        return line[:-1].split(", ")

    def test_the_list_holds_exactly_the_icons_the_generator_draws(self):
        listed = [n.strip("`") for n in self.listed()]
        self.assertEqual(sorted(ICONS["icons"]), sorted(listed))


if __name__ == "__main__":
    unittest.main()
