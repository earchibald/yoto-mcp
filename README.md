# yoto-mcp

Lets Claude read and safely edit your [Yoto](https://yotoplay.com) MYO playlists and interactive
cards. It's a local MCP server plus a Claude Code plugin (`yoto`) that adds skills, slash commands,
a plan reviewer and a guard on writes.

- **Cards by title.** Cards are named the way the Yoto app shows them, and card ids never appear.
- **Every write is plan, then apply.** A plan shows a diff, warnings and lint. Apply checks that
  the card hasn't changed since, backs it up, writes, reads it back, and checks that tracks play.
- **Interactive cards from a story graph.** A compiler and a player simulator catch the gotos to
  nowhere, dead buttons and endless loops that fail silently on a real player.
- **Spoken prompts on your machine.** Local TTS with a pronunciation map, plus a speech-to-text check.
- **Icons.** Pixel-exact 16×16 icons drawn from character grids, and an icon chooser page.

It runs on your machine over stdio. Your Yoto tokens and the app's client secret stay in the
macOS keychain, and no tool result ever contains them.

## Install

**1. Store the Yoto app's credentials and link your account(s)** (once, in a terminal):

```bash
uvx --from git+https://github.com/earchibald/yoto-mcp yoto-mcp setup        # client id + secret → keychain
uvx --from git+https://github.com/earchibald/yoto-mcp yoto-mcp login Eugene  # browser login, linked as "Eugene"
uvx --from git+https://github.com/earchibald/yoto-mcp yoto-mcp login Family  # optional second family account
```

The first account you link becomes the default; change it with `yoto-mcp default <name>`.
Login listens on the app's registered callback, `http://localhost/oauth/callback` by default (use
`setup --redirect-uri …` to change it).

**2. Add the plugin to Claude Code:**

```
/plugin marketplace add earchibald/yoto-mcp
/plugin install yoto@yoto-mcp
```

The plugin starts the server with `uvx`, so you need [uv](https://docs.astral.sh/uv/).
Without the plugin, any MCP client can run the server directly:
`claude mcp add yoto -- uvx --from git+https://github.com/earchibald/yoto-mcp yoto-mcp serve`.

**Optional, for spoken prompts:** [ollama](https://ollama.com) with a speech model
(`ollama pull phonon`), `ffmpeg`, and `mlx_whisper` or `whisper` for the transcript check.

## Use it

| Command | Does |
| --- | --- |
| `/yoto:login [name]` | Link an account, or list the linked ones |
| `/yoto:card <title>` | Outline a card and audit it: foreign media, label gaps, dead ends, stale intros |
| `/yoto:interactive <source> [pattern]` | Build a choose-a-book, title-track, branching or quiz card |
| `/yoto:icons <title> [--draw]` | Draw candidate icons and open the chooser |
| `/yoto:restore <title> [backup]` | Put a card back from a backup |

You can also just ask: "add a spoken title before each book on Hilo Books 4-8".

## Tools

| Area | Tools |
| --- | --- |
| Accounts | `yoto_whoami`, `yoto_account_link` |
| Reading | `yoto_cards_list`, `yoto_card_get`, `yoto_card_playable` |
| Editing | `yoto_card_plan` → `yoto_card_apply`, `yoto_card_backups`, `yoto_card_restore` |
| Media | `yoto_media_upload_file`, `yoto_media_upload_begin` / `_wait`, `yoto_media_import`, `yoto_tts` |
| Interactive | `yoto_story_validate`, `yoto_story_compile`, `yoto_story_simulate`, `yoto_story_from_card` |
| Templates | `yoto_template_book_selector`, `yoto_template_title_tracks` |
| Icons and art | `yoto_icon_render`, `yoto_icon_glyph`, `yoto_icon_from_image`, `yoto_icon_upload`, `yoto_icons_search`, `yoto_icon_chooser`, `yoto_icon_chooser_result`, `yoto_cover_upload` |

There are also resources (`yoto://schema/story-graph`, `yoto://docs/interactive`, card JSON,
outline and Mermaid graph) and three prompts (`book-selector`, `add-title-tracks`, `audit-card`).

### Safety

`yoto_card_apply` refuses a plan if any of these holds:

- the card changed after the plan was made
- the card's title changed
- the account doesn't own the card
- the plan is older than 30 minutes
- the plan has lint errors, such as another account's media refs

It saves a backup to `~/.yoto/backups/` (the last 20 per card), writes, re-reads, compares, and
makes ranged GETs on the first, last and changed tracks. Each apply is logged to `~/.yoto/changes.log`.
The plugin's PreToolUse hook also blocks an apply without a valid plan, and asks you to confirm
every write with a one-line summary.

### Interactive cards

Claude writes a small graph; the compiler does the rest:

```json
{"nodes": [
  {"id": "q", "type": "choice", "tracks": [{"audio": "yoto:#…"}], "left": "cave", "right": "woods"},
  {"id": "cave", "type": "end", "tracks": [{"audio": "yoto:#…"}]},
  {"id": "woods", "type": "end", "tracks": [{"audio": "yoto:#…"}]}],
 "expect": [{"presses": ["left"], "plays": ["q", "cave"]}]}
```

Node order is chapter (dial) order. The compiler gives every chapter a unique key and emits
`onEnd`/`onLhb`/`onRhb` events. It sets the interactive config: no autoadvance, dial skipping on,
and `resumeTimeout: 1` so the card starts over. Then the simulator walks every path and checks the
declared `expect` walks before any plan is made.

### Icon chooser

From Claude Code, the chooser opens in your browser on a loopback port with a one-time token. In
an MCP Apps host such as Claude Desktop, it renders inline (`ui://yoto/icon-chooser`). Picks come
back as a plan of `set_icon` ops.

## Live checks

These are open questions from the design. Each one writes to the default account and cleans up after itself.

```bash
uvx --from git+https://github.com/earchibald/yoto-mcp yoto-mcp check icons    # autoConvert=false icon round-trip, pixel-exact?
uvx --from git+https://github.com/earchibald/yoto-mcp yoto-mcp check resume   # is resumeTimeout: 0 accepted?
```

## Develop

```bash
uv run --extra dev pytest
claude --plugin-dir . plugin details yoto     # load the plugin from this checkout
```

Tests run against `tests/fake_yoto.py`, which mimics what the real API did during the Hilo work:

- `/content` is owner-only, and `/card` returns signed URLs
- foreign refs fail with 400
- known uploads get a null upload URL
- transcoding is async
- refresh tokens rotate

`mcp` is pinned below 2, because 2.x renamed FastMCP.
