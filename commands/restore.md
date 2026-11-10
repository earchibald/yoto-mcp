---
description: Restore a Yoto card from one of its backups
argument-hint: "<card title> [backup]"
---

Restore the Yoto card from: $ARGUMENTS

1. `yoto_card_backups(card)`. Show the backups newest first: when each was saved and its chapter count.
2. Pick the backup the user named (default: the newest), then `yoto_card_restore(card, backup)`.
3. Show the plan's diff: what the restore puts back and what it removes. Apply only after the user agrees.
