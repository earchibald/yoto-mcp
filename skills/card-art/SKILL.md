---
name: card-art
description: Drawing 16x16 Yoto display icons as character grids, previewing and linting them, choosing icons with the chooser, and preparing cover art. Use when the user wants icons or a cover for a Yoto card, chapter or track.
---

# Icons and cover art

You can't paint pixels by describing an image, so don't try. Write each icon as a **16×16
character grid plus a palette**. The server renders it deterministically, lints it, and returns an
8× preview that you must look at before offering the icon.

## Draw, look, fix (at most three rounds)

1. State the intent in a few words ("a crescent moon, yellow on black").
2. `yoto_icon_render([{name, rows: [16 strings × 16 chars], palette: {char: "#RRGGBB"}}])`.
   `.` and space are transparent. Or start from `yoto_icon_glyph("4", "#ffdd33")` for digits,
   letters and ★ ♥ ♪ ☾ ☀, or from `yoto_icon_from_image(path)` and clean it up.
3. Look at the contact sheet (black, as the player shows it, then a checkerboard). Fix lint
   warnings and anything that reads badly, then re-render.

Errors name the row and column. Fix them in one step.

## Style rules

- Strokes at least 2 px thick. Single pixels vanish on the player.
- 4 colours or fewer, bright enough to read on black.
- Keep a 1 px margin. Art shouldn't touch the edge.
- One idea per icon. Numerals for book prompts work well.
- Draw a whole book's set in one `yoto_icon_render` call so they match side by side.

## Using icons

- `yoto_icon_upload(sha256)` uploads a rendered icon exactly as drawn (16×16 RGBA, no resampling)
  and returns `yoto:#…`. Use it in a `set_icon` op: `{op: "set_icon", chapter, icon}` sets the
  chapter and all its tracks.
- `yoto_icons_search("moon")` finds Yoto's public icons and the account's own.
- **Chooser**: `yoto_icon_chooser(card, generated={chapter_key: [sha256…], "*": [...]})` opens a
  picker (browser from Claude Code, inline in Claude Desktop). The user's picks come back as a
  plan of `set_icon` ops, and generated icons are uploaded first. If the result has `requests`
  ("a friendlier moon"), draw those and reopen the chooser with them. Never call
  `yoto_icon_select`; it's for the page.

## Cover art

`yoto_cover_upload(path)` takes a JPEG, PNG, GIF or WebP the user supplies. Yoto resizes it. Use
the returned URL in a `set_cover` op. A selector card looks better with its own cover than with
the source card's.
