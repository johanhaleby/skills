---
name: domain-storytelling
description: Turn a described business process, workflow, or domain into one or more Domain Stories (domainstorytelling.org) rendered as pictures, and as Egon.io files that open at egon.io. Use when asked to draw, model, visualise, or map a process as a domain story, to run a Domain Storytelling workshop, or to make sense of who does what with what in a domain. Also use before designing software when the business process behind it has never been written down.
---

# Domain Storytelling

A domain story is a picture of a business process told as sentences: **an actor does
something with a work object, sometimes involving another actor**. The numbers on the
arrows carry the order. Nothing else does.

This skill models the story, then renders it. Read `references/notation.md` and
`references/spec.md` in one call before modelling anything non-trivial. The spec file lists every icon name.

## Steps

1. **Model the story.** Draft is the default: model the typical case from what the user described, choose icons, render, and say which assumptions you made. Ask only when a choice would change the model rather than decorate it.
2. **Read the sentences back once** and ask which of them is optional, or happens differently some of the time. Those become annotations. This is the easiest beat to skip, and skipping it costs the most, because the notation has no branch. A step that only sometimes happens either carries an annotation or silently joins the happy path.
3. **Run a workshop instead** when the user asks for one (`/domain-storytelling workshop`, "run a workshop", "interview me about this") or when a plan hinges on a process nobody has written down. Read `references/workshop.md` now. Never interview and draw at the same time.
4. **Write the spec and render it** as Rendering below says.
5. **Act on the warnings.** When the generator writes anything to stderr, read `references/warnings.md` now. The generator ends with a line naming that file, so a skipped read shows in the output.
6. **Inspect the picture** by reading the preview PNG the render wrote, and name every file written. A render is done when it has no warnings and the picture traces cleanly.

## Rules that are not negotiable

These come from the method, and breaking them produces something that looks like a domain
story but is not one.

- **Every actor appears exactly once.** One shape per actor, however many sentences it is in.
- **Two people in different roles are two actors, even when the domain calls them the same
  thing.** The rule above is about one actor drawn once, not about one *word* drawn once.
  Before merging two mentions of the same noun, ask whether one person could do both steps.
  When they could not, they are separate actors, and they get separate shapes and names that
  say which role each one plays here. The sequence numbers are what make this wrong rather
  than merely vague, because they assert that the actor doing sentence 5 is the one who did
  sentence 2. A single Driver who joins a classification's queue, frees a parking spot, and
  then claims the freed spot says a driver gave up a parking spot and was offered it back,
  when the departing holder and the queuer are two roles that share a job title. An annotation naming somebody else who could
  do the step, "an administrator can free the parking spot instead", is the tell that the
  step's actor was never settled.
- **Every activity draws its own work object.** The same order in three sentences is three
  shapes. This is not duplication, it is how the notation shows the thing moving.
- **No conditionals, branches, or loops.** There is no gateway, no diamond, no dashed
  "if". Model an important alternative as a *separate story*. Keep a small variation as an
  annotation.
- **A sentence starts with an actor** and the sequence number goes on that first arrow. The
  arrow on to a second actor carries a preposition, not a number.
- **Sentences that happen at the same time share one number**, and any number of them can.
  Repeat the `n` value across them in the spec. This is the only way the notation says "at
  the same time", since it has no fork and no join, so reach for it whenever neither step
  waits for the other. Keep the numbers separate when one genuinely follows the other,
  because a shared number claims more than "these two are related".
- **Use the domain's own words.** The verb on the arrow is the word the domain expert said.
  If you find yourself writing "processes" or "handles", you have not understood the step
  yet, so ask. In a story drawn from code, the domain's word is the name of the action a
  person triggers, the button, endpoint or command, so a `cancel-reservation` endpoint makes
  "cancels the reservation". Each verb names one action, and two different actions in one
  story get two different verbs. Follow the endpoint to the screen that calls it before
  naming the actor, and name the actor after that screen's users, because a backoffice user
  and an expert service in the admin editor are different roles who can do different things.
  When a neighbouring action has a similar effect, cancelling a parking spot's reservation
  against removing the queuer from the queue, the note names the screen and button, and the
  neighbour it is not.
- **A work object is one noun the domain already says.** "Invoice", "Parking spot claim".
  An `and` in the name is the tell that it is two, and the fix is to pick the one the
  activity really moves or to write two sentences sharing a number. Length is the symptom
  rather than the fault, so shortening a compound name to fit only hides it.
- **Say the scope out loud.** Granularity, point in time, domain purity. A story without
  them is ambiguous about what it even claims. It travels in the spec and in the Egon
  description rather than on the picture, because those three terms are the method talking
  to the modeller and a reader of the image cannot decode them.

## Showing where a change lands

When the point of the story is what a change does to a process, mark the sentences it touches with
`"changed": true` and say so in the description. One picture with the delta marked usually beats two pictures
the reader has to compare by eye. Draw an AS-IS and a TO-BE story instead when the *shape* of the process
changed, meaning steps moved between actors or disappeared, rather than when the steps stayed put and only
their content changed.

When the change decides who gets something, an offer, a priority or a turn, draw the contrast: the person who
loses out and the person who gains, as two actors in the same role named by their position ("First queuer",
"Second queuer"). End on the sentence that names which of them got it. With one actor in that role, a rule that
passes someone over draws exactly like a rule that does nothing.

A story drawn from code rather than from a person is a reading of that code. Say so next to the picture, so a
reader knows it has not been checked with anyone who does the work. Before drawing a changed sentence, read the
guard that decides when the new behaviour applies: the state it requires and the event that runs it. Draw that
event as the sentence before the change, and put a state the whole story holds in, such as "the departing driver
is still parked there", in the description. A note like "the next time the queue is asked" names no actor, and
is the tell that the trigger was never found.

## How many stories

- One story per important alternative. The happy path first.
- AS-IS and TO-BE are always separate stories, never one picture.
- A coarse overview plus one fine-grained story for the step that matters, when the process
  is large.
- Split at roughly fifteen sentences whatever else is true. A wide, busy picture is the
  method telling you the story is too big.

Name a set so it reads as a set: `Refund - AS-IS`, `Refund - TO-BE`.

## Rendering

Write a spec as JSON, then run the generator from the directory holding this `SKILL.md`:

```bash
python3 scripts/story2egn.py path/to/story.json --preview <scratch dir>
```

Standard library only. It writes two files next to the spec, or into `--out-dir`, and `--preview` adds `<Title>.png` in a directory of its own, so one call renders the story and hands back the picture to read. Point `--preview` at a scratch or temp directory, away from the files that ship. The PNG comes from rsvg-convert, cairosvg or inkscape when one is installed and from macOS Quick Look otherwise. `--help` lists the other formats and `--date`.

- `<Title>.egn.svg` is the picture and the editable model in one file. It opens in any SVG viewer and imports at https://egon.io. **Egon reads the story title back out of the filename**, so the title in the spec decides the filename.
- `<Title>.board.svg` is the same picture for a page. Every actor, work object, activity and note sits in a group tagged with the steps it belongs to, and it carries its own dark mode. `scripts/story-board.js` can dim, ring or crop to one step. A board hand-drawn at egon.io has no tags, so it stays a plain image.

Always tell the user the paths that were written. Show the pictures inline if the host can display images, otherwise the paths are the deliverable. Never say a story was produced without naming its file.

## Reading a story back

`.egn`, `.egn.svg`, and legacy `.dst` files are read by the sibling `egn-to-text` skill.
Use it rather than parsing them here.
