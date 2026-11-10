#!/usr/bin/env python3
"""Run with: python3 -m unittest test_egn2text -v"""
import json
import tempfile
import unittest
from pathlib import Path

import egn2text

STORY = [
    {"type": "domainStory:actorPerson", "name": "Driver", "id": "shape_0001", "x": 0, "y": 0},
    {"type": "domainStory:workObjectDocument", "name": "Ticket", "id": "shape_0002", "x": 200, "y": 0},
    {"type": "domainStory:activity", "name": "buys", "id": "connection_0001", "number": 1,
     "waypoints": [{"original": {"x": 38, "y": 38}, "x": 38, "y": 38},
                   {"original": {"x": 238, "y": 38}, "x": 238, "y": 38}],
     "source": "shape_0001", "target": "shape_0002"},
    {"info": "a description"},
    {"version": "3.0.0"},
]
PAYLOAD = {"domain": {"name": "default", "actors": {"Person": "<svg/>"},
                      "workObjects": {"Document": "<svg/>"}},
           "dst": STORY}


def parse(text, suffix):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"story{suffix}"
        p.write_text(text, encoding="utf-8")
        return egn2text.extract(p)


class ReadsEveryFormEgonWrites(unittest.TestCase):
    def assert_story_intact(self, result):
        self.assertEqual([s["text"] for s in result["steps"]],
                         ["Driver (actorPerson) buys Ticket (workObjectDocument)"])
        self.assertEqual(result["description"], "a description")

    def test_plain_egn(self):
        self.assert_story_intact(parse(json.dumps(PAYLOAD), ".egn"))

    def test_egn_svg_uses_an_uppercase_comment(self):
        # Exactly what svg.service.ts appendDST writes.
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" version="1.2"><g/></svg>'
               "\n<!-- <DST>\n" + json.dumps(PAYLOAD, indent=2) + "\n </DST> -->")
        self.assert_story_intact(parse(svg, ".egn.svg"))

    def test_lowercase_tag_form_still_reads(self):
        svg = "<svg><g/></svg><dst>" + json.dumps(PAYLOAD) + "</dst>"
        self.assert_story_intact(parse(svg, ".dst.svg"))

    def test_legacy_string_wrapped_values(self):
        legacy = {"domain": json.dumps(PAYLOAD["domain"]), "dst": json.dumps(STORY)}
        self.assert_story_intact(parse(json.dumps(legacy), ".dst"))

    def test_an_svg_with_no_story_is_an_error(self):
        with self.assertRaises(ValueError):
            parse('<svg><path d="M0 0"/></svg>', ".egn.svg")


if __name__ == "__main__":
    unittest.main()
