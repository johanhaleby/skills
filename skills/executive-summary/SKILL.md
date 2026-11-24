---
name: executive-summary
description: Write a short executive summary of what a piece of work does and why, so the person who asked for it can explain it to others. Use when asked for an executive summary or a background summary of a unit, a PR or a plan step, and when that person quotes a line from one and asks about it.
---

# Executive summary

An executive summary tells someone who wasn't there what a piece of work does, why it was built that way, and what is left. The person who asks for it, called the requester below, reads it to understand the work and then explains it to colleagues. Every line has to make sense to a person who never saw the code, the plan or the conversation behind it, because the requester may pass it on as it is.

## How it should read

Plain, one person to another, the way you'd explain it to a colleague over coffee. With the `bro` skill installed, write the way it re-explains things. With the `johan-writing` skill installed, write through it and run its gate with `--internal` before sending, since the summary names who decided what.

Without `bro`, these are its main principles, and they apply in full:

- Simpler, not necessarily shorter. The goal is a text nobody can misread. Take the space an idea needs, and cut preamble, hedging and consultant-speak.
- Facts stay exact. Every path, command, name, number, id and decision appears as it is, and only the explanation around it gets simpler.
- Short paragraphs and short sentences. A table becomes plain sentences unless it really compares things, and a list is for things that really are separate.
- Write in the language the requester asked in.

Read the code, the PRs and the plan before writing. Every claim in the summary is one you checked: a field name, a mode, who decided and when, a test count, a commit. Leave out what you can't check, or mark it unverified. A plausible inference reads just like a fact.

## Sections

1. **Background.** The problem in a few sentences: what goes wrong without this work, and for whom.
2. **Terms.** One bullet per word the rest of the summary leans on, domain words such as backorder or chargeback as well as code words. Use the plain word in the text and give the code's name once, in backticks (`maxOpenOrders`), or the phrase the code uses ("the code calls this stale").
3. **What changed.** Per PR, what it does to the system in practice. Say which mode or kind of run each behaviour applies to, such as a dry run against a real run, or with a feature flag off against on. Name the field or setting a step changes and what changing it does.
4. **Design decisions.** One subsection per decision, its heading stating the decision. Each one says what it decides, who decided it, when, and on whose recommendation, why, as a short list, and what it means in practice, as a short list. When it departs from what the plan or the ticket said, quote that text and say why it changed. A decision id, such as an ADR number or a ticket key, gets a one-line explanation the first time it appears.
5. **Tests and reviews.** The tests that back the claims above, with counts, and how many deliberate breaks a test caught. Tie each review and CI result to the commit it ran at, and say what has to run again when a later commit is pushed.
6. **Known errors.** Anything wrong in the plan or the docs, the right version, and what the error affects.
7. **Remaining steps.** Numbered, each naming who acts (the requester, the agent, a colleague) and at which commit.

Drop a section with nothing in it.

## Lines that raise questions

Each kind of line below tends to make a reader stop and ask about a draft. Look for every line of that kind, not just the example, and write the answer into the summary up front.

- **A vague verb.** Report, handle, process, sync, tidy, take care of. Say what happens, where it shows up, who sees it and what follows. Example: "report any record where the flag disagrees" left open where and as what.
- **A word only the code or the plan uses.** Every section can hide one, so give each a Terms entry. Example: "hydrate the order" became "fill in the order's missing fields from the database".
- **A verb without its object.** Join, cancel, release, claim. The first time a section uses such a verb, or the noun made from it ("the join"), name what it acts on, even when Terms defines it, since the reader meets the line before the glossary. Example: "a customer joins while the last seat is released" left the reader asking what the customer joins, and became "a customer joins the event's waiting list".
- **A technical term with more than one meaning.** Snapshot, window, session, lock. Tell what happens in the order it happens, and keep the term only under its established name, in Terms, with the field it comes from. Example: "a payment saved in that gap is in the refund's snapshot" became "the refund reads the clock, then the payments table, so a payment saved in between shows up with a time later than the refund's".
- **A verb that reads two ways.** Lose, drop, fail, miss, clear. "Lost" can mean beaten by someone else or gone for good, and in a race the difference is the whole point. Say who did what first, what the system refused, and what is still there afterwards. Example: "the import lost the row" read as a deleted row, and became "another import saved the row first, so the database refused this import's save and it ran again".
- **A rule without its effect.** For every rule, decision or mode, say what the user, the system and the team see when it applies. Example: "the retry policy applies" needed what the customer sees and what reaches the on-call engineer.
- **A category without examples.** Blocker, warning, edge case, invalid data. Give two or three real ones, quoting actual messages or values where they exist.
- **A safety claim.** Can't happen, always, never, safe, covered. Say what it rests on and what happens when that doesn't hold.
- **A missing actor.** A passive verb or a team name doing the work ("Support cleans up afterwards"). Name who does what, where and when.
- **An unnamed page, endpoint or job.** Any screen, endpoint, job or actor the summary mentions needs its name, where it lives or how it is reached, what a person does there and what they see. Example: "the admin page retries a capacity change that loses a race" became the event editor in the admin site (`/admin/events/{id}/edit`), where an organizer changes an event's seat count and clicks save, then sees "Could not save the seat count. Try again." and saves again.
- **Today's system, assumed.** Describing a change means describing what exists today, so check that too, defaults and where a value comes from included. Example: a limit the draft called new already existed, set in config instead of code.
- **A finding in the work itself.** Writing the summary can show that the code does something twice or not at all, or that the plan cites the wrong decision. Tell the requester about it as a finding, apart from the summary.

When the requester quotes a line and asks about it, answer, then rewrite that line in the summary so the next reader doesn't have to ask. When asked to simplify, make it shorter and keep every fact, number, name and id.

When the work changes, such as a new commit or a removed check, update the summary in the same turn: counts, commits and remaining steps.

## Delivering it

Write it as a markdown file in the session's scratchpad or working folder and send it with the host's file tool, such as `SendUserFile`, or give its full path. Done when every claim is checked, the `johan-writing` gate shows no hard hits where it is installed, and the file is sent.
