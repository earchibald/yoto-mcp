---
description: Build an interactive card (choose-a-book, branching story, quiz) from a source card
argument-hint: "<source card title> [book-selector|title-tracks|branching|quiz]"
---

Build an interactive Yoto card. Arguments: $ARGUMENTS (a source card title, then a pattern;
book-selector is the default).

Follow the interactive-cards and voice-prompts skills.

1. `yoto_card_get(source, view="outline")`. Confirm with the user which chapters belong to which
   book or part. Ask for subtitles (book number → subtitle) if the pattern speaks them.
2. book-selector: `yoto_template_book_selector(source_card, subtitles, account=<owner>)`.
   title-tracks: `yoto_template_title_tracks(card, subtitles)`.
   branching or quiz: agree on the flow with the user, make prompts with `yoto_tts`, write the
   graph with `expect` walks, then `yoto_story_compile`.
3. Show the user, in plain words:
   - the TTS transcripts, and any low-match warnings
   - the simulator walks (what plays after each choice)
   - the plan diff
4. Ask the card-auditor agent to review the plan. Mention any no-go.
5. Apply only after the user says yes. Report `verified` and the backup. Suggest trying it on the
   player: start-over, dial skip, and each button.
