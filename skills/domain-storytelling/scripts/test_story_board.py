#!/usr/bin/env python3
"""Run with: python3 -m unittest test_story_board -v

Pins the tagged board that story2egn.py writes as <stem>.board.svg and the promise that
the Egon files stay as they were. Every check runs the real CLI on real files.
"""
import collections
import contextlib
import datetime
import io
import functools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "story2egn.py"
EXAMPLES = HERE.parent / "examples"
FIXTURES = HERE / "fixtures"

sys.path.insert(0, str(HERE))
import story2egn  # noqa: E402

# The five roles and the literal each falls back to, the Egon colours
LIGHT = {"ink": "#1b1b1b", "ink-2": "#6b6b6b", "surface": "#ffffff", "accent": "#c8341b",
         "wash": "#fffbe6"}
ROLE_OF = {literal: role for role, literal in LIGHT.items()}
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
TAG_KINDS = "saon"


def cli(*args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, env=env)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def title_of(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["title"]


def story_files():
    files = sorted(EXAMPLES.glob("*.story.json")) + sorted(FIXTURES.glob("*.story.json"))
    assert files, "no story files found, so every loop over them would check nothing"
    return files


def expected_tags(spec):
    """What each tag kind should hold, read straight off the spec without the generator."""
    steps_of = collections.defaultdict(set)
    arrows, objects, notes = (collections.Counter() for _ in range(3))
    for i, s in enumerate(spec["sentences"], 1):
        n = s.get("n", i)
        steps_of[s["from"]].add(n)
        if s.get("to"):
            steps_of[s["to"]].add(n)
        arrows[(str(n), s.get("verb", ""))] += 1
        obj = s.get("object")
        if obj:
            objects[(str(n), obj["name"])] += 1
            if s.get("to"):
                arrows[(str(n), s.get("prep", ""))] += 1
            if s.get("note"):
                notes[(str(n), f"note on {obj['name']}")] += 1
    for a in spec["actors"]:
        if a.get("note"):
            notes[("", f"note on {a['name']}")] += 1
    actors = collections.Counter(
        (",".join(str(n) for n in sorted(steps_of[a["name"]])), a["name"]) for a in spec["actors"])
    for r in spec.get("relations") or []:
        arrows[("", r.get("verb", ""))] += 1
    return {"a": actors, "o": objects, "s": arrows, "n": notes}


def changed_steps(spec):
    return {str(s.get("n", i)) for i, s in enumerate(spec["sentences"], 1) if s.get("changed")}


def tagged(root):
    """(kind, steps, name, element) for every tag group, in document order."""
    found = []
    for el in root.iter():
        if local(el.tag) != "g":
            continue
        kinds = [k for k in TAG_KINDS if el.get(f"data-{k}") is not None]
        if kinds:
            found.append((kinds, el.get(f"data-{kinds[0]}"), el.get("data-name"), el))
    return found


def classes(el):
    return set((el.get("class") or "").split())


def children(el, name):
    return [c for c in el if local(c.tag) == name]


def css_blocks(css):
    """(selector, body, media) for every rule, with the one level of @media flattened."""
    out = []

    def read(i, media):
        while i < len(css):
            while i < len(css) and css[i].isspace():
                i += 1
            if i >= len(css):
                break
            if css[i] == "}":
                return i + 1
            j = css.index("{", i)
            head = css[i:j].strip()
            if head.startswith("@media"):
                i = read(j + 1, head)
            else:
                k = css.index("}", j)
                out.append((head, css[j + 1:k], media))
                i = k + 1
        return i

    read(0, None)
    return out


def custom_props(body):
    return dict(re.findall(r"(--[\w-]+):([^;]+);", body))


def paint_roles(el, pid):
    """The fill and stroke of one element as roles, whether a literal or a class says it."""
    out = {}
    for key in ("fill", "stroke"):
        value = el.get(key)
        if value is not None:
            out[key] = value if value == "none" else ROLE_OF.get(value.lower(), value)
    if pid:
        for token in classes(el):
            m = re.fullmatch(re.escape(pid) + r"-([fs])-(ink-2|ink|surface|accent|wash)", token)
            if m:
                out["fill" if m.group(1) == "f" else "stroke"] = m.group(2)
    return out


def drawn(root, pid=None):
    """Every drawn leaf with its colours named by role, so the two renderings compare."""
    skip = {"fill", "stroke", "class", "marker-end", "style"}
    tip = re.compile(r"url\(#(?:" + re.escape(pid or "") + r"-)?(tip(?:-accent)?)\)")
    seen = collections.Counter()
    for el in root.iter():
        name = local(el.tag)
        if name not in {"path", "rect", "text", "circle", "line"} and not (name == "g" and el.get("transform")):
            continue
        attrs = tuple(sorted((k, v) for k, v in el.attrib.items() if k not in skip))
        marker = tip.sub(r"\1", el.get("marker-end", ""))
        roles = tuple(sorted(paint_roles(el, pid).items()))
        seen[(name, attrs, roles, marker, (el.text or "").strip())] += 1
    return seen


class Rendered:
    """One story put through the real CLI, in the default format and in the Egon-only one.

    Files are read on first use, so one broken output fails its own tests and not the whole run.
    """

    def __init__(self, source, default_dir, egon_dir):
        self.source = source
        self.spec = json.loads(source.read_text(encoding="utf-8"))
        self.title = self.spec["title"]
        self.default_dir, self.egon_dir = default_dir, egon_dir

    @functools.cached_property
    def board_text(self):
        return (self.default_dir / f"{self.title}.board.svg").read_text(encoding="utf-8")

    @functools.cached_property
    def board(self):
        return ET.fromstring(self.board_text)

    @functools.cached_property
    def egn_default(self):
        return (self.default_dir / f"{self.title}.egn.svg").read_bytes()

    @functools.cached_property
    def egn_only(self):
        return (self.egon_dir / f"{self.title}.egn.svg").read_bytes()

    @functools.cached_property
    def egn(self):
        return ET.fromstring(self.egn_default.decode("utf-8"))

    @functools.cached_property
    def pid(self):
        return self.board.find(".//{*}g[@data-name]").get("class")


STORIES = {}
TMP = None


def setUpModule():
    global TMP
    TMP = Path(tempfile.mkdtemp(prefix="story-board-"))
    sources = story_files()
    default_dir, egon_dir, legacy_dir = TMP / "default", TMP / "egon", TMP / "legacy"
    for args in ((default_dir,), (egon_dir, "--format", "egn.svg")):
        out = cli(*sources, "--out-dir", args[0], *args[1:])
        assert out.returncode == 0, out.stderr
    fixtures = sorted(FIXTURES.glob("*.story.json"))
    out = cli(*fixtures, "--out-dir", legacy_dir, "--format", "egn.svg,svg,egn")
    assert out.returncode == 0, out.stderr
    for source in sources:
        STORIES[source.name] = Rendered(source, default_dir, egon_dir)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def each_story():
    assert STORIES, "no stories rendered, so every loop over them would check nothing"
    return STORIES.items()


class KeepsTheEgonFilesAsTheyWere(unittest.TestCase):

    def test_every_example_is_byte_identical_to_the_checked_in_file(self):
        for name, r in each_story():
            if r.source.parent != EXAMPLES:
                continue
            checked_in = (EXAMPLES / f"{r.title}.egn.svg").read_bytes()
            with self.subTest(story=name, format="egn.svg"):
                self.assertEqual(checked_in, r.egn_only)
            with self.subTest(story=name, format="default"):
                self.assertEqual(checked_in, r.egn_default)

    def test_the_fixture_baselines_come_from_the_generator_before_the_board_existed(self):
        legacy = TMP / "legacy"
        for source in sorted(FIXTURES.glob("*.story.json")):
            title = title_of(source)
            for suffix in ("egn.svg", "svg", "egn"):
                with self.subTest(story=source.name, format=suffix):
                    baseline = (FIXTURES / f"{title}.{suffix}").read_bytes()
                    self.assertEqual(baseline, (legacy / f"{title}.{suffix}").read_bytes())
            with self.subTest(story=source.name, format="default"):
                baseline = (FIXTURES / f"{title}.egn.svg").read_bytes()
                self.assertEqual(baseline, STORIES[source.name].egn_default)

    def test_the_changed_fixture_covers_what_the_examples_do_not(self):
        spec = STORIES["reroute-a-refund.story.json"].spec
        baseline = (FIXTURES / f"{spec['title']}.egn.svg").read_text(encoding="utf-8")
        with self.subTest("a sentence is marked"):
            self.assertTrue(any(s.get("changed") for s in spec["sentences"]))
        with self.subTest("the accent colour and arrowhead are in the baseline"):
            self.assertIn("#c8341bff", baseline)
            self.assertIn('id="tip-accent"', baseline)
        with self.subTest("no example marks a sentence, so the fixture is the only cover"):
            for name, r in each_story():
                if r.source.parent == EXAMPLES:
                    self.assertFalse(any(s.get("changed") for s in r.spec["sentences"]), name)

    def test_the_other_fixture_has_notes_groups_relations_and_a_parallel_step(self):
        spec = STORIES["review-a-claim.story.json"].spec
        numbers = [s["n"] for s in spec["sentences"]]
        self.assertTrue(spec["groups"] and spec["relations"])
        self.assertTrue(any(s.get("note") for s in spec["sentences"]))
        self.assertTrue(any(a.get("note") for a in spec["actors"]))
        self.assertLess(len(set(numbers)), len(numbers))

    def test_egon_files_stay_literal_with_no_style_block_and_no_class(self):
        files = list(EXAMPLES.glob("*.egn.svg")) + list(FIXTURES.glob("*.egn.svg"))
        for name, r in each_story():
            files.append(TMP / "default" / f"{r.title}.egn.svg")
        self.assertTrue(files)
        for path in files:
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertNotIn("<style", text)
                self.assertNotIn("class=", text)


class TagsEveryElementWithItsSteps(unittest.TestCase):

    def actual(self, r, kind):
        return collections.Counter((steps, name) for kinds, steps, name, _ in tagged(r.board)
                                   if kinds[0] == kind)

    def test_each_tag_kind_holds_exactly_what_the_spec_names(self):
        for name, r in each_story():
            want = expected_tags(r.spec)
            for kind in TAG_KINDS:
                with self.subTest(story=name, kind=kind):
                    self.assertEqual(want[kind], self.actual(r, kind))

    def test_no_element_carries_two_kinds_or_sits_inside_another_tag(self):
        for name, r in each_story():
            groups = tagged(r.board)
            with self.subTest(story=name, check="one kind each"):
                self.assertTrue(all(len(kinds) == 1 for kinds, *_ in groups))
            with self.subTest(story=name, check="no nesting"):
                for _, _, _, el in groups:
                    inner = [g for g in el.iter() if g is not el and any(
                        g.get(f"data-{k}") is not None for k in TAG_KINDS)]
                    self.assertEqual([], inner)

    def test_every_drawn_arrow_icon_and_note_sits_in_exactly_one_group(self):
        for name, r in each_story():
            groups = tagged(r.board)
            by_kind = collections.defaultdict(list)
            for kinds, _, _, el in groups:
                by_kind[kinds[0]].append(el)
            heads = [p for p in r.board.iter() if local(p.tag) == "path" and p.get("marker-end")]
            icons = [g for g in r.board.iter() if local(g.tag) == "g" and g.get("transform")]
            bodies = [p for p in r.board.iter() if local(p.tag) == "path"
                      and f"{r.pid}-f-wash" in classes(p)]
            with self.subTest(story=name, check="arrows"):
                self.assertEqual(len(by_kind["s"]), len(heads))
                for el in by_kind["s"]:
                    self.assertEqual(1, len([p for p in el.iter() if p.get("marker-end")]))
            with self.subTest(story=name, check="icons"):
                self.assertEqual(len(by_kind["a"]) + len(by_kind["o"]), len(icons))
                for el in by_kind["a"] + by_kind["o"]:
                    self.assertEqual(1, len([g for g in el.iter() if g.get("transform")]))
            with self.subTest(story=name, check="notes"):
                self.assertEqual(len(by_kind["n"]), len(bodies))
                for el in by_kind["n"]:
                    self.assertEqual(1, len([p for p in el.iter() if f"{r.pid}-f-wash" in classes(p)]))

    def test_a_step_repeated_across_parallel_sentences_is_listed_once_in_order(self):
        for name, r in each_story():
            for kinds, steps, _, _ in tagged(r.board):
                if kinds[0] == "s" or not steps:
                    continue
                with self.subTest(story=name, kind=kinds[0], steps=steps):
                    listed = steps.split(",")
                    self.assertEqual(len(set(listed)), len(listed))
                    self.assertEqual(sorted(listed, key=int), listed)

    def test_the_parallel_example_gives_each_shared_actor_one_list(self):
        r = STORIES["parallel-dispatch.story.json"]
        found = {name: steps for kinds, steps, name, _ in tagged(r.board) if kinds[0] == "a"}
        self.assertEqual({"Warden": "1", "Fine service": "1,2,3,4", "Print partner": "3",
                          "Motorist": "3,4"}, found)

    def test_the_fixture_with_one_actor_in_two_sentences_of_one_step_lists_it_once(self):
        r = STORIES["review-a-claim.story.json"]
        found = {name: steps for kinds, steps, name, _ in tagged(r.board) if kinds[0] == "a"}
        self.assertEqual("2,3,4", found["Assessor"])
        self.assertEqual("1,3,4", found["Claimant"])

    def test_names_are_xml_escaped_and_the_root_carries_the_raw_title(self):
        r = STORIES["reroute-a-refund.story.json"]
        with self.subTest("escaped in the file"):
            for escaped in ('data-name="Case tool &amp; log"', 'data-name="Refund &lt;500 kr"',
                            'data-name="note on Refund &lt;500 kr"',
                            'data-name="Reroute a refund &amp; notify"'):
                self.assertIn(escaped, r.board_text)
        with self.subTest("read back as the raw text"):
            names = {name for _, _, name, _ in tagged(r.board)}
            self.assertIn("Case tool & log", names)
            self.assertIn("Refund <500 kr", names)
        for name, story in each_story():
            with self.subTest(story=name, check="root name"):
                root = story.board.find("{*}g")
                self.assertEqual(story.title, root.get("data-name"))
                self.assertEqual({"class", "data-name", "font-family"}, set(root.attrib))

    def test_an_actor_name_is_drawn_as_the_tagged_name(self):
        for name, r in each_story():
            for kinds, _, tag_name, el in tagged(r.board):
                if kinds[0] not in "ao" or len(tag_name) > 17:
                    continue
                with self.subTest(story=name, tag=tag_name):
                    shown = " ".join(t.text for t in children(el, "text"))
                    self.assertEqual(tag_name, shown)


class ThemesByClassNotByAttribute(unittest.TestCase):

    def test_the_prefix_is_the_crc32_of_the_title(self):
        for name, r in each_story():
            with self.subTest(story=name):
                self.assertEqual("sb" + format(zlib.crc32(r.title.encode("utf-8")), "08x"), r.pid)
                self.assertRegex(r.pid, r"^sb[0-9a-f]{8}$")

    def test_every_class_in_the_board_carries_the_prefix_and_no_colour_is_a_literal(self):
        for name, r in each_story():
            with self.subTest(story=name):
                for el in r.board.iter():
                    for token in classes(el):
                        self.assertTrue(token == r.pid or token.startswith(r.pid + "-"), token)
                    for key in ("fill", "stroke"):
                        if el.get(key) is not None:
                            self.assertEqual("none", el.get(key), f"{local(el.tag)} {key}")

    def test_each_colour_lands_on_the_role_the_egon_file_draws_it_in(self):
        for name, r in each_story():
            with self.subTest(story=name):
                egon, board = drawn(r.egn), drawn(r.board, r.pid)
                self.assertEqual(sorted(egon.elements()), sorted(board.elements()))

    def test_actors_and_work_objects_are_ink_or_accent_when_changed(self):
        for name, r in each_story():
            marked = changed_steps(r.spec)
            for kinds, steps, tag_name, el in tagged(r.board):
                if kinds[0] not in "ao":
                    continue
                role = "accent" if kinds[0] == "o" and steps in marked else "ink"
                with self.subTest(story=name, tag=tag_name):
                    (icon,) = children(el, "g")
                    self.assertEqual({f"{r.pid}-f-{role}"}, classes(icon))
                    for text in children(el, "text"):
                        self.assertEqual({f"{r.pid}-f-{role}", f"{r.pid}-s-surface"}, classes(text))

    def test_arrows_are_ink_or_accent_and_relations_are_ink_2(self):
        for name, r in each_story():
            marked = changed_steps(r.spec)
            for kinds, steps, tag_name, el in tagged(r.board):
                if kinds[0] != "s":
                    continue
                role = "ink-2" if steps == "" else ("accent" if steps in marked else "ink")
                tip = f"{r.pid}-tip-accent" if role == "accent" else f"{r.pid}-tip"
                with self.subTest(story=name, steps=steps, arrow=tag_name):
                    (line,) = children(el, "path")
                    self.assertEqual({f"{r.pid}-s-{role}"}, classes(line))
                    self.assertEqual("none", line.get("fill"))
                    self.assertEqual(f"url(#{tip})", line.get("marker-end"))
                    self.assertEqual(steps == "", line.get("stroke-dasharray") is not None)
                    for text in children(el, "text"):
                        if text.get("font-weight") == "600":
                            self.assertEqual({f"{r.pid}-f-surface"}, classes(text))
                            self.assertEqual(steps, text.text)
                        else:
                            self.assertEqual({f"{r.pid}-f-{role}", f"{r.pid}-s-surface"}, classes(text))
                    for badge in children(el, "circle"):
                        self.assertEqual({f"{r.pid}-f-{role}"}, classes(badge))

    def test_every_numbered_sentence_has_one_badge_with_the_digit_in_surface(self):
        for name, r in each_story():
            with self.subTest(story=name):
                badges = [el for _, steps, _, el in tagged(r.board) for _ in children(el, "circle")]
                self.assertEqual(len(r.spec["sentences"]), len(badges))

    def test_group_boundaries_and_note_leaders_are_ink_2_and_the_note_body_is_wash(self):
        for name, r in each_story():
            root = r.board.find("{*}g")
            boundaries = [e for e in children(root, "rect") if e.get("rx") == "14"]
            with self.subTest(story=name, check="group boundary"):
                self.assertEqual(len(r.spec.get("groups") or []), len(boundaries))
                for rect in boundaries:
                    self.assertEqual("none", rect.get("fill"))
                    self.assertEqual({f"{r.pid}-s-ink-2"}, classes(rect))
            for kinds, _, tag_name, el in tagged(r.board):
                if kinds[0] != "n":
                    continue
                with self.subTest(story=name, note=tag_name):
                    paths = children(el, "path")
                    leader, body = [p for p in paths if p.get("stroke-dasharray")], \
                                   [p for p in paths if not p.get("stroke-dasharray")]
                    self.assertEqual([{f"{r.pid}-s-ink-2"}], [classes(p) for p in leader])
                    self.assertEqual([{f"{r.pid}-f-wash", f"{r.pid}-s-ink-2"}], [classes(p) for p in body])
                    for text in children(el, "text"):
                        self.assertEqual({f"{r.pid}-f-ink"}, classes(text))

    def test_the_background_and_the_title_use_surface_and_ink(self):
        for name, r in each_story():
            root = r.board.find("{*}g")
            (first,) = children(root, "rect")[:1]
            with self.subTest(story=name):
                self.assertEqual({f"{r.pid}-f-surface"}, classes(first))
                self.assertEqual(r.board.get("viewBox").split(),
                                 [first.get(k) for k in ("x", "y", "width", "height")])
                title = children(root, "text")[0]
                self.assertEqual(r.title, title.text)
                self.assertEqual({f"{r.pid}-f-ink"}, classes(title))

    def test_each_role_maps_to_the_viewer_token_with_the_egon_literal_as_fallback(self):
        for name, r in each_story():
            style = r.board.find("{*}style").text
            rules = {sel: custom_props(body) for sel, body, media in css_blocks(style) if media is None}
            light = rules[f".{r.pid}"]
            with self.subTest(story=name, check="palette"):
                self.assertEqual({f"--{r.pid}-{role}": f"var(--{role},{literal})"
                                  for role, literal in LIGHT.items()}, light)
            with self.subTest(story=name, check="classes"):
                for kind, prop in (("f", "fill"), ("s", "stroke")):
                    for role in LIGHT:
                        self.assertIn(f".{r.pid}-{kind}-{role}{{{prop}:var(--{r.pid}-{role})}}", style)

    def test_the_ring_class_the_page_helper_appends_has_a_rule_in_the_board(self):
        for name, r in each_story():
            with self.subTest(story=name):
                self.assertIn(f".{r.pid}-ring{{fill:none;stroke:var(--{r.pid}-accent);stroke-width:3}}",
                              r.board.find("{*}style").text)


class ThemesInThreeStates(unittest.TestCase):

    def blocks(self, r):
        return css_blocks(r.board.find("{*}style").text)

    def test_light_is_the_bare_rule_and_dark_is_set_twice_with_the_same_palette(self):
        dark_media = '@media (prefers-color-scheme: dark)'
        for name, r in each_story():
            blocks = self.blocks(r)
            palettes = {(sel, media): custom_props(body) for sel, body, media in blocks
                        if body.startswith("--")}
            with self.subTest(story=name):
                self.assertEqual(
                    {(f".{r.pid}", None), (f':root:not([data-theme="light"]) .{r.pid}', dark_media),
                     (f':root[data-theme="dark"] .{r.pid}', None)}, set(palettes))
                light = palettes[(f".{r.pid}", None)]
                auto = palettes[(f':root:not([data-theme="light"]) .{r.pid}', dark_media)]
                forced = palettes[(f':root[data-theme="dark"] .{r.pid}', None)]
                self.assertEqual(auto, forced)
                self.assertEqual(set(light), set(auto))
                self.assertNotEqual(light, auto)
                for role in LIGHT:
                    self.assertRegex(auto[f"--{r.pid}-{role}"], rf"^var\(--{role},#[0-9a-f]{{6}}\)$")

    def test_no_colour_is_declared_only_inside_the_media_query(self):
        for name, r in each_story():
            blocks = self.blocks(r)
            inside = {c for _, body, media in blocks if media for c in HEX.findall(body)}
            outside = {c for _, body, media in blocks if not media for c in HEX.findall(body)}
            props_in = {p for _, body, media in blocks if media for p in custom_props(body)}
            props_out = {p for _, body, media in blocks if not media for p in custom_props(body)}
            with self.subTest(story=name):
                self.assertTrue(inside)
                self.assertLessEqual(inside, outside)
                self.assertLessEqual(props_in, props_out)

    def test_the_media_query_is_the_only_at_rule_and_holds_one_rule(self):
        for name, r in each_story():
            style = r.board.find("{*}style").text
            with self.subTest(story=name):
                self.assertEqual(1, style.count("@media"))
                self.assertEqual(1, len([b for b in self.blocks(r) if b[2]]))

    def test_no_presentation_attribute_holds_a_var_and_the_root_has_no_inline_background(self):
        for name, r in each_story():
            with self.subTest(story=name):
                for el in r.board.iter():
                    for key, value in el.attrib.items():
                        self.assertNotIn("var(", value, f"{local(el.tag)} {key}")
                self.assertIsNone(r.board.get("style"))
                self.assertNotIn("background-color", r.board_text)

    def test_the_egon_file_keeps_its_inline_background(self):
        for name, r in each_story():
            with self.subTest(story=name):
                self.assertEqual("background-color:#ffffff", r.egn.get("style"))


class PrefixesEveryIdPerBoard(unittest.TestCase):

    def test_every_marker_id_and_every_reference_starts_with_the_board_prefix(self):
        for name, r in each_story():
            with self.subTest(story=name):
                ids = re.findall(r'<marker id="([^"]+)"', r.board_text)
                refs = re.findall(r"url\(#([^)]+)\)", r.board_text)
                self.assertTrue(ids and refs)
                for found in ids + refs:
                    self.assertTrue(found.startswith(r.pid + "-"), found)
                self.assertLessEqual(set(refs), set(ids))

    def test_the_egon_file_keeps_the_bare_ids(self):
        for name, r in each_story():
            text = r.egn_default.decode("utf-8")
            marked = bool(changed_steps(r.spec))
            with self.subTest(story=name):
                self.assertEqual({"tip", "tip-accent"} if marked else {"tip"},
                                 set(re.findall(r'<marker id="([^"]+)"', text)))
                self.assertEqual({"tip", "tip-accent"} if marked else {"tip"},
                                 set(re.findall(r"url\(#([^)]+)\)", text)))

    def test_the_accent_arrowhead_exists_in_the_board_exactly_when_the_egon_file_has_it(self):
        for name, r in each_story():
            board_has = f'<marker id="{r.pid}-tip-accent"' in r.board_text
            with self.subTest(story=name):
                self.assertEqual(b'id="tip-accent"' in r.egn_default, board_has)

    def test_different_titles_give_different_prefixes(self):
        prefixes = [r.pid for _, r in each_story()]
        self.assertEqual(len(prefixes), len(set(prefixes)))

    def test_two_boards_in_one_page_leave_no_duplicate_id_or_class_rule(self):
        boards = [r for _, r in each_story()]
        page = "<html><body>" + "".join(r.board_text for r in boards) + "</body></html>"
        ids = re.findall(r'\bid="([^"]+)"', page)
        self.assertEqual(len(ids), len(set(ids)))
        rules = re.findall(r"^(\.sb[0-9a-f]{8}[^{]*)\{", page, re.M)
        self.assertEqual(len(rules), len(set(rules)))


class KeepsTheGeometryOfTheEgonFile(unittest.TestCase):

    def test_the_canvas_is_the_same(self):
        for name, r in each_story():
            with self.subTest(story=name):
                for key in ("width", "height", "viewBox"):
                    self.assertIsNotNone(r.egn.get(key))
                    self.assertEqual(r.egn.get(key), r.board.get(key), key)

    def test_every_path_has_the_same_d_in_the_same_count(self):
        for name, r in each_story():
            egon = collections.Counter(p.get("d") for p in r.egn.iter() if local(p.tag) == "path")
            board = collections.Counter(p.get("d") for p in r.board.iter() if local(p.tag) == "path")
            with self.subTest(story=name):
                self.assertTrue(egon)
                self.assertEqual(sum(egon.values()), sum(board.values()))
                self.assertEqual(egon, board)

    def test_every_text_and_badge_sits_where_the_egon_file_puts_it(self):
        for name, r in each_story():
            def spots(root):
                return collections.Counter(
                    (local(e.tag), e.get("x") or e.get("cx"), e.get("y") or e.get("cy"), e.text)
                    for e in root.iter() if local(e.tag) in {"text", "circle"})
            with self.subTest(story=name):
                self.assertEqual(spots(r.egn), spots(r.board))


class LeavesHandDrawnBoardsUntagged(unittest.TestCase):

    def test_no_checked_in_egon_file_carries_a_tag(self):
        files = list(EXAMPLES.glob("*.egn.svg")) + list(FIXTURES.glob("*.egn.svg"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(file=path.name):
                self.assertIsNone(re.search(r"\sdata-[saon]=", path.read_text(encoding="utf-8")))

    def test_a_drawn_board_has_no_story_source_so_no_board_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d) / "Order coffee.egn.svg"
            shutil.copy(EXAMPLES / "Order coffee.egn.svg", copy)
            for fmt in (None, "board.svg"):
                with self.subTest(format=fmt):
                    out = cli(copy, *(["--format", fmt] if fmt else []))
                    self.assertEqual(1, out.returncode)
                    self.assertTrue(out.stderr.startswith("error:"), out.stderr)
                    self.assertEqual("", out.stdout)
                    self.assertEqual(["Order coffee.egn.svg"], sorted(p.name for p in Path(d).iterdir()))


class PointsAtTheWarningGuideOnce(unittest.TestCase):

    def lines(self, *sources):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            out = cli(*sources, "--out-dir", d)
        self.assertEqual(0, out.returncode, out.stderr)
        return out.stderr.splitlines()

    def test_a_story_that_warns_ends_stderr_with_the_pointer_exactly_once(self):
        found = self.lines(EXAMPLES / "oversized.story.json")
        pointers = [line for line in found if line.startswith("warnings:")]
        self.assertGreater(len(found), 1)
        self.assertEqual(1, len(pointers))
        self.assertIn("references/warnings.md", pointers[0])
        self.assertEqual(pointers[0], found[-1])

    def test_several_stories_still_give_one_pointer_and_it_comes_last(self):
        found = self.lines(EXAMPLES / "oversized.story.json", EXAMPLES / "coffee-order.story.json")
        self.assertEqual(1, len([line for line in found if line.startswith("warnings:")]))
        self.assertTrue(found[-1].startswith("warnings:"))

    def test_a_clean_story_prints_no_warnings_line(self):
        self.assertEqual([], self.lines(EXAMPLES / "coffee-order.story.json"))


class PointsAtTheWarningGuideOnlyForTheRunThatWarned(unittest.TestCase):

    def stderr_of(self, source, out_dir):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = story2egn.main([str(source), "--out-dir", out_dir])
        self.assertEqual(0, code, err.getvalue())
        return err.getvalue()

    def test_a_clean_run_after_a_warning_run_in_one_process_prints_no_pointer(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            first = self.stderr_of(EXAMPLES / "oversized.story.json", d)
            second = self.stderr_of(EXAMPLES / "coffee-order.story.json", d)
        with self.subTest("the run that warned"):
            self.assertEqual(1, first.count("references/warnings.md"), first)
        with self.subTest("the clean run after it"):
            self.assertEqual("", second)


class TagsARelationWithNoStepNumber(unittest.TestCase):

    SPEC = {
        "title": "Hand over a parcel",
        "actors": [{"name": "Sender", "icon": "Person"}, {"name": "Courier", "icon": "Person"}],
        "sentences": [
            {"n": 1, "from": "Sender", "verb": "hands", "object": {"name": "Parcel", "icon": "Folder", "id": "parcel"},
             "prep": "to", "to": "Courier"},
            {"n": 2, "from": "Courier", "verb": "hands", "object": {"name": "Label", "icon": "Document", "id": "label"},
             "prep": "to", "to": "Sender"},
        ],
        "relations": [{"from": "label", "verb": "is stuck on", "to": "parcel"}],
    }

    def board(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            source = Path(d) / "parcel.story.json"
            source.write_text(json.dumps(self.SPEC), encoding="utf-8")
            out = cli(source, "--out-dir", d, "--format", "board.svg")
            self.assertEqual(0, out.returncode, out.stderr)
            return ET.parse(Path(d) / "Hand over a parcel.board.svg").getroot()

    def test_the_relation_has_an_empty_step_list_and_every_sentence_has_steps(self):
        arrows = [(el.get("data-s"), el.get("data-name")) for _, _, _, el in tagged(self.board())
                  if el.get("data-s") is not None]
        relation = [steps for steps, name in arrows if name == "is stuck on"]
        sentences = [steps for steps, name in arrows if name != "is stuck on"]
        with self.subTest("the relation"):
            self.assertEqual([""], relation)
        with self.subTest("the sentences"):
            self.assertTrue(sentences)
            self.assertTrue(all(sentences), sentences)


class WritesTheRequestedFormats(unittest.TestCase):

    SOURCE = FIXTURES / "review-a-claim.story.json"
    TITLE = "Review a claim"

    def run_into(self, *args, env=None):
        d = Path(tempfile.mkdtemp(dir=TMP))
        out = cli(self.SOURCE, "--out-dir", d, *args, env=env)
        self.assertEqual(0, out.returncode, out.stderr)
        return d, out

    def listing(self, d):
        return sorted(p.name for p in d.iterdir())

    def test_the_default_writes_the_egon_file_and_the_board(self):
        d, out = self.run_into()
        self.assertEqual([f"{self.TITLE}.board.svg", f"{self.TITLE}.egn.svg"], self.listing(d))
        self.assertEqual(sorted(str(d / n) for n in self.listing(d)), sorted(out.stdout.split("\n")[:-1]))

    def test_the_board_alone_writes_only_the_board(self):
        d, _ = self.run_into("--format", "board.svg")
        self.assertEqual([f"{self.TITLE}.board.svg"], self.listing(d))

    def test_the_egon_format_alone_writes_no_board(self):
        d, _ = self.run_into("--format", "egn.svg")
        self.assertEqual([f"{self.TITLE}.egn.svg"], self.listing(d))

    def test_the_old_formats_still_write_no_board(self):
        d, _ = self.run_into("--format", "svg,egn")
        self.assertEqual([f"{self.TITLE}.egn", f"{self.TITLE}.svg"], self.listing(d))

    def test_the_board_written_alone_is_the_board_written_beside_the_egon_file(self):
        alone, _ = self.run_into("--format", "board.svg")
        both, _ = self.run_into()
        self.assertEqual((alone / f"{self.TITLE}.board.svg").read_bytes(),
                         (both / f"{self.TITLE}.board.svg").read_bytes())

    def test_the_date_goes_on_both_stems(self):
        before = datetime.date.today().isoformat()
        d, _ = self.run_into("--date")
        after = datetime.date.today().isoformat()
        found = self.listing(d)
        self.assertEqual(2, len(found))
        for stamp in {before, after}:
            if all(f"{self.TITLE}_{stamp}." in n for n in found):
                break
        else:
            self.fail(found)
        self.assertEqual({"board.svg", "egn.svg"}, {n.split(".", 1)[1] for n in found})

    def test_an_unsafe_title_gives_both_files_the_same_readable_stem(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            spec = json.loads(self.SOURCE.read_text(encoding="utf-8"))
            spec["title"] = "Pay: fines/fees?"
            source = Path(d) / "pay.story.json"
            source.write_text(json.dumps(spec), encoding="utf-8")
            out = cli(source, "--out-dir", Path(d) / "out")
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertEqual(["Pay- fines-fees.board.svg", "Pay- fines-fees.egn.svg"],
                             sorted(p.name for p in (Path(d) / "out").iterdir()))
            board = ET.parse(Path(d) / "out" / "Pay- fines-fees.board.svg").getroot()
            self.assertEqual("Pay: fines/fees?", board.find("{*}g").get("data-name"))

    def test_an_unknown_format_is_refused_before_anything_is_written(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            out = cli(self.SOURCE, "--out-dir", d, "--format", "board.svg,bogus")
            self.assertEqual(2, out.returncode)
            self.assertIn("bogus", out.stderr)
            self.assertEqual([], list(Path(d).iterdir()))


class NeverFeedsTheBoardToAPngConverter(unittest.TestCase):
    """A fake converter first on PATH records what it was handed."""

    SOURCE = FIXTURES / "review-a-claim.story.json"
    TITLE = "Review a claim"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=TMP))
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "rsvg-convert"
        # rsvg-convert -o OUT IN, so $2 is the png and $3 the svg. Builtins only
        fake.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$@" > "$RECORD/argv"\n'
            'while IFS= read -r line || [ -n "$line" ]; do printf "%s\\n" "$line"; done < "$3" > "$RECORD/input.svg"\n'
            ': > "$2"\n', encoding="utf-8")
        fake.chmod(0o755)
        self.record = self.dir / "record"
        self.record.mkdir()
        self.env = {"PATH": str(bin_dir), "RECORD": str(self.record)}

    def convert(self, formats):
        out_dir = self.dir / "out"
        out = cli(self.SOURCE, "--out-dir", out_dir, "--format", formats, env=self.env)
        self.assertEqual(0, out.returncode, out.stderr)
        argv = (self.record / "argv").read_text(encoding="utf-8").split("\n")[:-1]
        return out_dir, argv, (self.record / "input.svg").read_text(encoding="utf-8")

    def plain_svg(self):
        d = self.dir / "plain"
        self.assertEqual(0, cli(self.SOURCE, "--out-dir", d, "--format", "svg").returncode)
        return (d / f"{self.TITLE}.svg").read_text(encoding="utf-8")

    def test_the_board_alone_converts_a_plain_svg_it_deletes_afterwards(self):
        out_dir, argv, handed = self.convert("board.svg,png")
        with self.subTest("what the converter read"):
            self.assertEqual(["-o", str(out_dir / f"{self.TITLE}.png")], argv[:2])
            self.assertTrue(argv[2].endswith(".__tmp.svg"), argv[2])
            self.assertEqual(self.plain_svg(), handed.rstrip("\n"))
            self.assertNotIn("data-name", handed)
            self.assertNotIn("<style", handed)
        with self.subTest("what is left behind"):
            self.assertEqual([f"{self.TITLE}.board.svg", f"{self.TITLE}.png"],
                             sorted(p.name for p in out_dir.iterdir()))

    def test_with_the_egon_file_beside_it_the_converter_reads_the_egon_file(self):
        out_dir, argv, handed = self.convert("board.svg,egn.svg,png")
        with self.subTest("what the converter read"):
            self.assertEqual(str(out_dir / f"{self.TITLE}.egn.svg"), argv[2])
            self.assertNotIn("data-name", handed)
            self.assertNotIn("<style", handed)
        with self.subTest("the board is still written"):
            self.assertTrue((out_dir / f"{self.TITLE}.board.svg").exists())

    def test_the_converter_never_sees_a_board_in_any_order_of_formats(self):
        for formats in ("png,board.svg", "egn.svg,board.svg,png", "svg,board.svg,png"):
            with self.subTest(formats=formats):
                shutil.rmtree(self.dir / "out", ignore_errors=True)
                _, argv, handed = self.convert(formats)
                self.assertFalse(argv[2].endswith(".board.svg"), argv[2])
                self.assertNotIn("data-name", handed)


if __name__ == "__main__":
    unittest.main()
