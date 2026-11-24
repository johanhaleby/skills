# Skills


| Skill | What it does |
| --- | --- |
| [`domain-storytelling`](skills/domain-storytelling/SKILL.md) | Turns a described business process into one or more [Domain Stories](https://domainstorytelling.org), written as Egon.io files that open at [egon.io](https://egon.io) and as pictures. |
| [`egn-to-text`](skills/egn-to-text/SKILL.md) | Reads an Egon.io file (`.egn`, `.egn.svg`, and the older `.dst` and `.dst.svg`) and prints the story as text, so an agent can read a diagram it was handed. |
| [`executive-summary`](skills/executive-summary/SKILL.md) | Writes a short summary of what a piece of work does and why, such as a pull request or a step in a plan, for a reader who never saw the code, so the person who asked for it can explain the work to others. |

`domain-storytelling` uses `egn-to-text` to read stories that already exist, so install both.

This is what `domain-storytelling` draws for [`coffee-order.story.json`](skills/domain-storytelling/examples/coffee-order.story.json), a five-sentence story about ordering a coffee. The matching [`.egn`](skills/domain-storytelling/examples/Order%20coffee.egn) file opens at [egon.io](https://egon.io) for further editing.

![Order coffee drawn as a domain story. The customer orders a flat white from the barista, who rings up the order, the customer taps a card on the point of sale, which prints a receipt, and the barista brews the coffee](skills/domain-storytelling/examples/Order%20coffee.egn.svg)

## Installing

In Claude Code, add this repository as a plugin marketplace and install the plugins you want:

```sh
/plugin marketplace add johanhaleby/skills
/plugin install domain-storytelling@johanhaleby-skills
/plugin install executive-summary@johanhaleby-skills
```

For any other agent, copy or symlink the folders under `skills/` into the agent's skills directory, for example `~/.claude/skills`, `~/.agents/skills` or `~/.codex/skills`. Keep `domain-storytelling` and `egn-to-text` next to each other, because `domain-storytelling` finds `egn-to-text` as a sibling folder.

The `domain-storytelling` and `egn-to-text` scripts need Python 3 and nothing outside the standard library. `executive-summary` has no scripts.

## Running the tests

The tests need Python 3 and Node. CI runs them with Python 3.12 and Node 24.

```sh
python3 skills/domain-storytelling/scripts/test_story2egn.py
python3 skills/domain-storytelling/scripts/test_story_board.py
node skills/domain-storytelling/scripts/test-story-board.cjs
python3 skills/egn-to-text/scripts/test_egn2text.py
```

## License

MIT, see [`LICENSE`](LICENSE).
