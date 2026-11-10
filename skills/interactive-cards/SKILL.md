---
name: interactive-cards
description: How to design and build Yoto interactive cards (choose-a-book selectors, branching stories, quizzes, linear cards with title tracks) as story graphs with the yoto MCP compiler and simulator. Use when the user wants buttons to choose, branch or skip on a Yoto card.
---

# Interactive Yoto cards

Write a **story graph**, never raw events. `yoto_story_compile` turns it into a card, walks it
the way the player does, and returns a plan only if the walk is clean.

## The graph

```json
{
  "start": "q1",
  "nodes": [
    {"id": "q1", "type": "choice", "title": "Which way?",
     "tracks": [{"title": "Which way?", "audio": "<media from yoto_tts>"}],
     "left": "cave", "right": "woods"},
    {"id": "cave", "type": "segment", "title": "The cave", "tracks": [{"audio": "yoto:#…"}], "next": "end1"},
    {"id": "woods", "type": "end", "title": "The woods", "tracks": [{"audio": "yoto:#…"}]},
    {"id": "end1", "type": "end", "title": "The end", "tracks": [{"audio": "yoto:#…"}]}
  ],
  "expect": [{"presses": ["left"], "plays": ["q1", "cave", "end1"]}]
}
```

- `choice` plays its prompt, then waits. It needs both `left` and `right`.
- `segment` plays, then goes to `next` (a node id, or `"stop"`). It may also have `left`/`right`
  (or the aliases `prev`/`skip`) for manual stepping.
- `end` plays, then stops.
- Several tracks in one node play in order.
- **Node order is chapter order, and chapter order is dial order.** The start node always comes
  first. Put an announcement right before what it introduces.
- `expect` declares walks. `presses` uses `left`, `right` and `end` (let the track finish). After
  the presses, the card plays on until it stops or waits. Compile fails if the walk differs.
  Declare one walk per path you promised the user.

## Workflow

1. Sketch the flow in words for the user, and agree on it.
2. Make prompt audio with `yoto_tts`. Reuse existing tracks' `yoto:#` refs from `yoto_card_get`,
   on the same account.
3. `yoto_story_validate`, fix the errors, then `yoto_story_compile`.
4. Show the simulator walks, then the plan diff. Apply after the user agrees.
5. Ask the user to try it on the player: start-over, dial skip, and each button on each node type.

`yoto_story_simulate(presses, card=…)` replays presses against a live card. `yoto_story_from_card`
turns an existing card back into a graph you can edit and recompile.

## Ready-made patterns

- **Choose a book** (`yoto_template_book_selector`): from a linear card whose chapters are titled
  `Book N …`. Right cycles the prompts, left announces the book and plays it through to the end.
  Left and right step tracks during a book. Rebuilding updates the same card in place.
- **Title tracks** (`yoto_template_title_tracks`): inserts "<Series> Book N: <subtitle>" before
  each book of a linear card and regenerates the spoken intro.
- **Branching story**: a choice at each fork, segments between, end nodes.
- **Quiz**: a choice per question. The right answer goes on; the wrong one plays a "try again"
  segment whose `next` returns to the question.

## Device facts

Read `yoto://docs/interactive` for which facts are observed and which are assumed. The essentials:
- A `goto` to a missing key fails silently on the player. That's why the compiler exists.
- `resumeTimeout: 1` makes the card start over each time. That's the default, and it's what works.
- After a prompt ends with `stop`, its buttons still work.
