# The notation, and how to model with it

Source: https://domainstorytelling.org/quick-start-guide, and the worked examples in
`WPS/egon.io-examples`.

## The sentence

> **actor** — *activity* → **work object** — *preposition* → **actor**

"Moviegoer **buys** a *ticket* **from** the cashier." Subject, predicate, object. Read a
domain story aloud and it should come out as ordinary sentences; if it does not, the model
is wrong, not the reader.

- **Actors** are people, groups, or software systems. They start sentences. Nothing else does.
- **Work objects** are what actors work on: documents, messages, money, physical things.
- **Activities** are arrows labelled with the domain's verb.
- **Sequence numbers** sit on the first arrow of each sentence and carry the order.
- **Annotations** hold what does not fit: variations, definitions, assumptions.
- **Groups** ring a part of the picture: a team, a location, a subdomain, a bounded context.

## The two rules that surprise people

**An actor appears once.** However many sentences a system takes part in, it is one shape.
That is why a busy system ends up with arrows radiating from it — and seeing that is often
the point of drawing at all.

**A work object is drawn again for every activity.** Three sentences that touch the order
draw three orders. It looks like duplication and is not: it is how the notation shows a
thing moving through a process rather than sitting in a database.

## What the notation refuses to draw

No conditionals. No branches. No loops. No gateways or decision diamonds.

This is a deliberate constraint, not a missing feature. When a process forks, you either
pick the important alternative and give it **its own story**, or you note the small
variation as an **annotation** on the typical case. A domain story that grows an "if" has
stopped being a domain story.

## Scope, stated every time

| Axis | Values | Question it answers |
| --- | --- | --- |
| Granularity | coarse-grained ↔ fine-grained | How much detail? |
| Point in time | AS-IS / TO-BE | The process today, or the one you want? |
| Domain purity | pure / digitalized | Does software appear at all? |

A pure story has no systems in it — only people and the work. It is the right choice when
the software is what you are about to design, because putting today's systems in the
picture quietly decides the answer.

## How to run the modelling

1. **Typical case first.** One concrete, ordinary run through the process. Not the general
   rule, not the edge case: one story that actually happened.
2. **Say each sentence back.** "So the warden photographs the vehicle, and that goes into
   the fine service?" Corrections come fast and cheaply at this point.
3. **Only then, what else can happen.** Collect the rules once the typical case stands.
4. **Watch the verbs.** "Processes", "handles", "manages" mean the step is not understood
   yet. The domain expert has a better word — ask for it.
5. **Watch for a missing actor.** If a work object appears from nowhere, somebody made it.

## Layout

The generator lays the picture out the way the book's own examples do: the actor taking
part in the most sentences goes in the middle, the other actors sit around it, and each
sentence's work object sits on the line between the two actors it involves.

Position carries no sequence. The numbers do, and no arrangement of shapes ever asserts an
order. Do not try to read a domain story left to right.

The generator still has to choose where each actor sits, and it chooses for the reader's
eye rather than for meaning. The actor who speaks first takes the opening seat on the left,
so sentence one runs into the picture instead of out of it, and the rest are seated so that
consecutive sentences end up near each other instead of on opposite sides. When the
protagonist is the one who speaks first it already holds the middle, so sentence one runs
outwards and the ring turns the other way. None of that adds meaning. The numbers remain
the only order.

Two sentences that genuinely happen at the same time carry the same number, and any number
of sentences can share one. That is the notation's only way to say "at the same time",
because there is no fork and no join to draw. Use it when neither step waits for the other,
and keep separate numbers when one really does follow the other, since a shared number
claims something stronger than "these are related". In the spec, repeat the `n` value
across those sentences.

## Choosing icons

An actor and a work object are drawn the same way, an icon with a name under it, so the
icon is the only thing telling a reader which kind a shape is. Once an actor in a story
holds an icon, no work object in that story may wear it, and the generator refuses the
spec rather than drawing one glyph for two kinds.

The split below is a habit rather than a law. `Business` is the actor `Bank` in one
bundled example and the work object `Account` in another, and both are right. What is
fixed is the rule above, within one story.

### Usually an actor

| The thing | Icon |
| --- | --- |
| A person, any role | `Person` |
| A team, department, committee | `Group` |
| Any software, service, or application | `System` |
| A regulator, court, authority | `Courthouse` |
| A company or office | `Business` |
| A shop or depot | `Store` |

### Usually a work object

| The thing | Icon |
| --- | --- |
| A document, form, contract, application | `Document` |
| A case, file, dossier | `Folder` |
| A phone call | `Call`, a message `Email`, a chat `Conversation` |
| Money, a price, an invoice | `Euro` or `Dollar` |
| A card payment | `Credit-Card` |
| A receipt or ticket | `Receipt` |
| A queue, broker, or anything hosted | `Cloud` |
| A rating, score, or assessment | `Assessment` |
| An approval or rejection | `Thumb-up`, `Thumb-down`, `Thumb-up-down` |
| A signature or registration | `How-To-Reg` |
| A vehicle | `Car`, `Truck`, `Bus`, `Train`, `Bike` |
| A place or site | `Place` |
| A duration, deadline, or timer | `Time` or `Hourglass` |
| A list or report | `View-List` or `Assessment` |
| A problem, error, or dispute | `Problem` |
| A credential or key | `Key`, security `Security` |

A person who is worked on rather than working, the patient a paramedic moves or the
passenger a gate agent boards, is a work object, and `Person` is usually taken by an actor
by then. `Walking`, `Blind` and `How-To-Reg` all read as human and leave the reader able to
see at a glance which human acts and which is moved.

When nothing in the set fits, take the nearest icon and say which substitution you made.
Egon supports uploading custom icons and the cinema example uses one of its own for the
snacks, so a concept with no matching icon is a normal gap rather than a modelling error.

`scripts/icons.json` has all ninety-one names. Anything outside Egon's small default set
travels inside the generated file, so Egon may ask whether to take the icon set on import.
That prompt is expected; it is not an error.

## Reading an existing story

Use the `egn-to-text` skill for `.egn`, `.egn.svg`, and legacy `.dst` files.
