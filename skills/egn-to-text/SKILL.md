---
name: egn-to-text
description: Read and understand Egon.io domain story files (.egn, .egn.svg, and legacy .dst / .dst.svg) from domainstorytelling.org. Use this skill whenever one of those files is uploaded, referenced, or found in a repository — including oblique requests such as "look at this diagram", "what does this model show", or "review this flow". Run the bundled converter before interpreting the file so inlined icon SVG, layout geometry, and legacy string-wrapped JSON do not obscure the story.
---

# Egon.io domain stories

`.egn` is JSON, not a binary format. It parses directly — no image or screenshot is needed for questions about the story's text and sequence.

## Do this first

Do not read the raw file first. Resolve the directory containing this `SKILL.md`,
then run the converter from that directory:

```bash
cd /path/to/egn-to-text
python3 scripts/egn2text.py path/to/story.egn
```

Multiple stories at once: pass several paths. Structured output for programmatic
use: add `--json`. Standard library only, no dependencies. If one input fails,
the converter continues with the others but exits non-zero after printing any
successful results.

The reduction varies with the story and icon set. The text form preserves named participants, activities, sequence numbers, annotations, and explicit groups, but it does not preserve spatial layout or informal visual clustering.

## Reading the output

The converter emits these sections:

- **Participants** — actors and work objects, deduplicated by name. Egon lets the
  same actor appear as several shapes in one story; `[appears as N shapes]` flags
  that so you don't mistake it for N distinct entities.
- **Story** — the numbered activities. This is the semantic core. Read it in order;
  it should parse as consecutive sentences.
- **Unnumbered relations** — activities without a sequence number. These are
  structural context (containment, ownership, derivation), *not* steps in the flow.
  Do not present them as part of the sequence.
- **Annotations** — free text attached to elements. Empty ones are dropped.
- **Groups** — explicit groups and their members, when present.

## What the text form loses

Spatial layout. Domain stories sometimes convey meaning through proximity and
whitespace that no text rendering captures. Explicit `domainStory:group` elements
*are* extracted, but informal visual clustering is not. If the user's question
turns on layout, say so rather than guessing.

## Format notes

The file normally has two top-level keys: `domain` (the icon set — inlined SVG,
usually irrelevant to text questions) and `dst` (a flat list of elements). Legacy
`.dst` exports may store either value as a JSON-encoded string; the converter
handles that form. Activities have `source`, `target`, and sometimes `number`;
numbers may be strings or numbers. Missing references are rendered as unknown
rather than aborting the whole story. Schemaless trailing objects commonly carry
`{"info": ...}` and `{"version": ...}`.

Version matters when files are in git. Egon v3 dropped unused attributes from the
format, so a v2-exported `.egn` diffed against a v3 export of the same story shows
large structural changes that reflect nothing about content. If a diff looks
alarming, check whether the version changed before reading anything into it.

`.egn.svg` and `.dst.svg` files carry the story embedded inside the SVG; the
converter extracts it, so prefer those when a file must be both viewable and
parseable. If the user's question turns on layout, inspect the SVG visually as a
separate step and say which conclusions depend on that layout.
