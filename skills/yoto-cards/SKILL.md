---
name: yoto-cards
description: Rules for reading and editing Yoto MYO cards with the yoto MCP tools — cards by title, plan-then-apply, account ownership of media, backups and restore. Use whenever a task touches a Yoto card, playlist, track, chapter, icon or cover.
---

# Working with Yoto cards

A Yoto card is one JSON document that is replaced whole on every write, and it may be a child's
bedtime card. These rules keep edits safe.

## Name things the way the user sees them

- Cards are named by **title**. The Yoto app never shows a card id, so never show one to the user.
  Every tool takes `card` as a title: an exact match wins, then a unique partial match. If a title
  is ambiguous or missing, the tool changes nothing and lists the closest titles. Ask the user
  which one.
- Accounts are named by the short name given at login ("Eugene", "Family"). `yoto_whoami` lists them.

## Every change is plan, then apply

1. `yoto_card_get` (outline) to see the card.
2. `yoto_card_plan` with small ops keyed by chapter key. It writes nothing and returns a diff,
   warnings and errors.
3. Show the user the diff and warnings in plain words. Mention removed chapters and stale intros.
4. Only after they agree: `yoto_card_apply(plan_id)`. A hook asks the user to confirm too.
5. Report the result: `verified`, the playback checks (206 means it plays) and the backup.

Never hand-build a full card document. Use ops, a template, or `yoto_story_compile`.
Plans expire after 30 minutes. If apply says the card changed since planning, plan again.

## Ownership

- `yoto_cards_list` marks family-library cards from another account as `owned: false`. They're
  read-only from this account.
- Media refs (`yoto:#…` audio, icons, covers) belong to the account that uploaded them. A card that
  uses another account's ref fails with "Media file not found". To copy a card to another account,
  `yoto_media_import` it first, then use its map in a `replace_refs` op.
- Templates must run as the source card's owner. The error message names the right account.

## Undo

Every apply saves a backup first (the last 20 per card). `yoto_card_backups` lists them, and
`yoto_card_restore` makes a plan that puts one back. Apply it like any other plan.

## Media

- Local audio: `yoto_media_upload_file(path)`. Yoto dedupes by content, so re-uploading is cheap.
- Spoken prompts: `yoto_tts` (see the voice-prompts skill).
- Icons: see the card-art skill. Covers: `yoto_cover_upload(path)`, then a `set_cover` op.

## After an edit

Re-read the card (`yoto_card_get`) if the user asks what changed. Chapter labels are renumbered
1..N automatically after structural ops. If a chapter named "intro" speaks track numbers, adding
or moving chapters makes them stale. The plan warns, and `yoto_template_title_tracks` regenerates it.
