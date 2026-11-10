---
description: Draft icons for every chapter of a Yoto card and open the icon chooser
argument-hint: "<card title> [--draw]"
---

Choose icons for the Yoto card: $ARGUMENTS

Follow the card-art skill.

1. `yoto_card_get(card, view="outline")` to see the chapters.
2. If `--draw` was given: draw one icon per chapter, or one per book, with `yoto_icon_render` in a
   single batch. Look at the contact sheet and fix it, at most three rounds.
3. `yoto_icon_chooser(card, generated={chapter_key: [sha256, …]})`. Tell the user a picker is
   opening, and that pressing Done sends their picks back.
4. If the result has `requests`, draw those, then reopen the chooser with the new candidates.
5. Show the plan diff (which chapters get new icons). Apply after the user agrees.
