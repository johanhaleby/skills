# Story spec

One JSON file per story. The generator owns ids, coordinates, waypoints and the Egon
envelope. The spec only carries the story.

```json
{
  "title": "Order coffee",
  "scope": { "granularity": "coarse-grained", "time": "AS-IS", "purity": "digitalized" },
  "description": "The typical case: one customer, paying by card, drinking in.",
  "actors": [
    { "name": "Customer", "icon": "Person" },
    { "name": "Barista", "icon": "Person" },
    { "name": "Point of sale", "icon": "System", "note": "Replaced next quarter." }
  ],
  "sentences": [
    { "n": 1, "from": "Customer", "verb": "orders",
      "object": { "name": "Flat white", "icon": "Dining" },
      "prep": "from", "to": "Barista" },
    { "n": 2, "from": "Barista", "verb": "rings up",
      "object": { "name": "Order", "icon": "Document", "id": "order" },
      "prep": "in", "to": "Point of sale",
      "note": "Only for drinking in." }
  ],
  "relations": [
    { "from": "order", "verb": "is billed to", "to": "Customer" }
  ],
  "groups": [
    { "name": "Counter", "members": ["Barista", "Point of sale"] }
  ]
}
```

## Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `title` | yes | Names the story **and the file**. Egon reads the title back out of the filename. |
| `scope` | no | `granularity`, `time`, `purity`. Written into the Egon description, where a modeller reading the model will look for it. The picture does not print it, because the three terms are the method talking to the modeller. |
| `description` | no | One or two lines under the title. Say what the story assumes. |
| `actors` | yes | `name`, `icon`, optional `note`, optional `at`. Declared once each. An icon an actor holds is that story's actor icon, and no work object may reuse it. |
| `sentences` | yes | See below. |
| `relations` | no | Unnumbered structural arrows: `from`, `to`, optional `verb`. |
| `groups` | no | `name` and `members`: a labelled boundary for a subdomain or team. |

### Sentences

| Field | Required | Meaning |
| --- | --- | --- |
| `n` | no | Sequence number. Defaults to position. Repeat it across sentences that genuinely happen in parallel, meaning both of them happen and neither waits. A repeated number never means one or the other. The notation has no conditional, so two alternatives are two stories. |
| `from` | yes | The actor doing it. An actor name, never a work object. |
| `verb` | yes | The domain's own word, on the numbered arrow. |
| `object` | usually | `name`, `icon`, optional `id`, optional `at`. The icon has to be one no actor in this story holds, so reach for one of the work-object icons in `notation.md`. |
| `prep` | no | Label on the second arrow: `to`, `in`, `from`, `via`. Reads as part of the sentence. |
| `to` | no | The actor receiving it. Omit for a sentence that ends at the work object. |
| `note` | no | An annotation, attached to that sentence's work object. Drawn in full, so the shape grows to fit instead of the text being cut. |
| `changed` | no | `true` marks this sentence as the part a change touches. See below. |

## Marking what a change touches

`"changed": true` on a sentence draws its arrows, sequence number, work object and labels in one accent
colour, and adds a legend line under the description. Use it to show where in a process a pull request, a
merged change, or a proposal lands, so a reader can see the delta without comparing two pictures line by line.

The accent is stored as Egon's own `pickedColor`, so a marked story keeps its colours when it is imported at
egon.io rather than losing them on the way in.

Two things it is not. It is not a general colour control, so there is one accent and no palette. And it is not
a branch, a marked sentence is still an ordinary sentence and an alternative path is still a separate story.

A caveat in the caption cannot undo what the sequence numbers say, which is why the generator writes a
`branch:` line when two sentences read as alternatives numbered into one sequence.

## The board file

Next to `egn.svg` the generator writes `<Title>.board.svg`, the same picture from the same render pass, with every actor, work object, activity and note in a group a page can find, so no step ever has to match a drawing back to its model. The Egon file is unchanged, and a board hand-drawn at egon.io has no spec to tag from, so it stays a plain image. The root group's `data-name` is the story title, which is the board's identity, and each tagged group adds its own `data-name`. Colours are classes from a style block prefixed per board. They read the host page's CSS custom properties (`--ink`, `--ink-2`, `--surface`, `--accent`, `--wash`), with the Egon literals as fallbacks. The block switches to dark colours when the system prefers dark and the page has no `data-theme="light"`, or when the page sets `data-theme="dark"`. No presentation attribute holds `var()`. Marker ids and classes share the prefix, so two boards inlined in one page cannot reach into each other. `scripts/story-board.js` adds dim, ring, pulse and viewBox crop to a page that includes it, and nothing is applied until the page asks.

| Group | Attribute | Step list |
| --- | --- | --- |
| An arrow with its verb and number | `data-s` | The sentence's number, empty for a relation. |
| An actor | `data-a` | Every sentence that names it as `from` or `to`. |
| A work object | `data-o` | Its one sentence. |
| A note | `data-n` | Its sentence, empty for an actor's own note. |

## Addressing

Actors are addressed by name, because there is exactly one of each.

Work objects are not. The notation draws a separate shape for every activity, so two
sentences both naming `Order` are two different shapes and the bare name means nothing.
Give a work object an `id` when a `relation` or a `group` needs to point at it, and use
that id. A declared `id` is checked against the other ids and against the actor names, so
those cannot collide.

A work object's `name` is not an address and is never compared, so an actor and a work
object may carry the same name. The generator writes a `name:` line when they do and
leaves the call to you, because a domain that uses one noun for a role and a thing is
stating a fact about itself.

## Layout overrides

`"at": [col, row]` pins a shape, and nothing moves it afterwards. An overlap the pin causes
is reported rather than quietly relaxed away, so a pin that makes the drawing worse says so.
Only reach for it when the generator has warned about the drawing, splitting the story did
not help, and the automatic layout has been inspected.
`col` steps horizontally, `row` vertically, both from the middle of the picture. Negative
goes left and up. Keep the pin only when the complete drawing becomes easier to trace.

## What is rejected

The generator fails loudly rather than drawing something wrong:

- an unknown icon name
- `from` or `to` naming an actor that was never declared
- the same actor declared twice, since the notation allows one shape per actor
- an actor declared but never used in a sentence
- a `relation` or `group` member that is neither an actor name nor a work object `id`
- a sentence with neither an `object` nor a `to`
- a work object drawn with an icon an actor in the same story already uses, since one
  glyph would then stand for two kinds and nothing in the picture says which
- a field the generator does not read, at any level of the file. An invented
  top-level `notes` list draws nothing, so it is refused rather than dropped.

## Icons

From Egon's own library, and the name is case-sensitive. The common ones and what they
are for are in `notation.md`, and this is all of them:

`Alarm`, `Assessment`, `Attach`, `Bike`, `Blind`, `Boat`, `Briefcase`, `Bus`, `Business`, `Calendar`, `Call`, `Car`, `Cellphone`, `Circle-Arrows`, `Cloud`, `Commute`, `Conversation`, `Copyright`, `Couch`, `Courthouse`, `Credit-Card`, `Dashboard`, `Desktop`, `Dining`, `DNS`, `Document`, `Dollar`, `Education`, `Email`, `Euro`, `Favorite`, `Flag`, `Flight-landing`, `Flight-takeoff`, `Folder`, `Gas-station`, `Gavel`, `Grid`, `Group`, `Headset`, `Hotel`, `Hourglass`, `How-To-Reg`, `Info`, `Key`, `Keyboard`, `Label`, `Meeting-room`, `Microphone`, `Motorcycle`, `Mouse`, `Pencil`, `Person`, `Pet`, `Picture-as-PDF`, `Pie-chart`, `Place`, `Plane`, `Printer`, `Problem`, `Receipt`, `Router`, `Ruler`, `Scanner`, `Search`, `Security`, `Settings`, `Shopping`, `Shuttle`, `Signal`, `Speaker-phone`, `Store`, `Sum`, `System`, `Tablet`, `Taxi`, `Theater`, `Thumb-down`, `Thumb-up`, `Thumb-up-down`, `Time`, `Traffic`, `Train`, `Truck`, `Update`, `View-List`, `Walking`, `Watch`, `Water`, `World`, `Wrench`.

The choice is per kind and per story. Once an actor holds an icon, every work object in
that story has to pick another one.
