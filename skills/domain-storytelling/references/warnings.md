# When the generator complains

It routes arrows around shapes and, where possible, around other activity arrows. It writes
to stderr when a shape overlaps another, a label has nowhere clear to sit, an arrow crosses
a shape it does not connect, a verb or number has another arrow running through it or sits
nearer another arrow than its own, a work object sits nearer an actor whose sentence it is not in,
a manual pin makes arrows cross, a note overlaps an arrow, or a note's leader line crosses
something it could have avoided. It also writes a `name:` line for two things, a name too
long for the two lines under its icon, because the rest of it is replaced by an ellipsis and
leaves the picture, and a work object carrying an actor's name, because two shapes then
answer to it. It writes a `reading:` line when the picture is too wide to
read where it gets embedded, naming the width, the size the names come out at in a column,
and which part of the story set that width, because a markdown image is force-fit to
whatever column it lands in. Act on the part it names. Dropping annotations and splitting
the story pull on different stories, and a split does nothing at all to a wall of notes. It writes a `branch:` line
when the wording and the sentences together read as two alternative paths numbered into one
sequence, naming the two sentences it means. That one is not a layout fault and the numbered fixes below do not reach
it. Draw one story per branch instead, copying the shared opening sentences into each. A `gap:`
line names an actor that starts working with nothing handed to it, so add the sentence that
hands it anything, or split the story where a wait or an event separates the two. A `verb:`
line names a verb holding two activities, which become two sentences, or a verb that reaches
somebody without a `to`, which needs the recipient actor. That second half guesses from the
verb's first letters, so "offers a discount" can trip it, and a verb that reaches nobody can
stay as it is. An `icon:` line names a work object
drawn with an actor's icon, which needs a work-object icon. Those
lines name the elements involved, so act on them rather than ignoring them:

1. Most of the time the story is too big. Split it.
2. Rename long work objects, a shorter name buys a lot of room and is the only fix for a
   `name:` line about a cut name. For one about a shared name, rename either shape, or keep
   both deliberately because the domain really does use the one word.
3. Remove manual `at` positions and compare the automatic layout.
4. As a last resort pin a shape with `"at": [col, row]` and render again.

After every render, inspect the whole picture at ordinary reading size. The generator
already seats the cast to keep consecutive sentences near each other and to avoid crossing
arrows where a seating exists that does not, so a crossing you can still see is one no
seating could remove. Completion requires both a warning-free render and a clean visual
trace: each numbered sentence is easy to follow, every work object sits nearest the actors
of its own sentence, activity arrows do not cross when a compact alternative exists, and
the drawing does not spend most of its
canvas on empty space. A pin earns its place only when this whole
picture improves. A pin that merely replaces one warning with a sprawling layout is a
failed layout, and so is one that seats the actor who speaks first on the right, since
the layout puts that actor on the left so sentence one runs into the picture.

A crossed leader is only reported when the note could have been placed to avoid it. A work
object inside the ring of actors cannot always be reached without crossing a spoke, because
the layout puts the actors round a hub and the arrows radiate through the gaps.

A reader attributes a verb or a number to the arrow it touches, so one that another arrow
runs through names two sentences at once. Placement centres a verb on its arrow first and
sets it beside the arrow when every centred seat reaches a sibling, which is the long verb
on a short arrow leaving a busy actor. A warning left after that means no seat cleared it.
Shorten the verb, usually by moving its condition into a note on the sentence, so "ends the
occupation" carries the arrow and "when the last bought ticket ends" goes in the note.

A reader attributes a work object to whoever it sits nearest rather than to whoever the
arrow comes from. Two actors facing each other across the ring have their midpoint in the
middle of the ring, where the protagonist stands, so the object ends up nearest the
protagonist and the picture says the protagonist did that work. Placement slides such an
object outwards until its own actors are nearest, and when that fails it moves the object
along the chord towards the nearer of the two, which leans it towards an actor it belongs
to rather than a third. Outwards alone cannot rescue two actors sitting exactly opposite,
because the one in the middle stays on the short side of every triangle the slide makes.
Both together leave only the crowded ring, where the fix is to split the story.
`Grant a building permit` in `examples/` is the worked case, at 20 sentences where this
file says split at fifteen.

Placement will not exile a note to avoid a crossing, and this is the one case where a
picture still ships with a dashed line over a solid one. The nearest clear position is often
far enough away to widen the canvas and run the leader across the whole picture, so near and
crossing beats far and clean.
Remove the note or split the story if the crossing matters more than the distance, because
no amount of searching will find a third option.
