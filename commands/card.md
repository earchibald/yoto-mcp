---
description: Show a Yoto card's outline and audit it for problems
argument-hint: "<card title>"
---

Show and audit the Yoto card titled "$ARGUMENTS". Change nothing.

1. `yoto_card_get(card, view="outline")`. Show the chapters as a short table: label, title,
   duration (m:ss), icon yes/no, and events for interactive cards.
2. Audit it and report findings worst first, each with a fix the user could apply as plan ops:
   - audio or icons owned by another account (the get result and lint say so)
   - labels that don't run 1..N
   - chapters without icons
   - an "intro" chapter whose spoken track numbers may be stale
   - for interactive cards: `yoto_story_simulate(presses=[], card=…)`. Report its walk problems,
     dead buttons, unreachable chapters and loops.
3. Offer the next step, without doing it.
