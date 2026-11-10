#!/usr/bin/env python3
"""PreToolUse guard for yoto_card_apply.

Blocks the write unless the plan exists, has no errors and isn't stale (plans live in
~/.yoto/plans/ and only yoto_card_plan and the tools built on it make them). Otherwise asks the
user to confirm, with a one-line summary of what the plan changes. Standard library only; it runs outside the server.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PLAN_TTL = 30 * 60


def decide(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def summary(p: dict) -> str:
    d = p.get("diff") or {}
    parts = []
    for key, verb in (("added", "add"), ("removed", "remove"), ("moved", "move"), ("changed", "change")):
        if d.get(key):
            parts.append(f"{verb} {len(d[key])} chapter{'s' if len(d[key]) != 1 else ''}")
    for k in (d.get("card") or {}):
        parts.append(f"change the card's {k}")
    what = ", ".join(parts) or "no changes"
    title = p.get("base_title") or "a new card"
    verb = "Create" if not p.get("card_id") else "Write"
    return f"{verb} {title!r} on {p.get('account')!r}: {what}."


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    plan_id = str((event.get("tool_input") or {}).get("plan_id") or "")
    if not plan_id or "/" in plan_id or plan_id.startswith("."):
        decide("deny", "yoto_card_apply needs a plan_id from yoto_card_plan.")
    home = Path(os.environ.get("YOTO_HOME") or Path.home() / ".yoto")
    f = home / "plans" / f"{plan_id}.json"
    if not f.exists():
        decide("deny", f"No plan {plan_id!r}. Make one with yoto_card_plan and show the user its diff first.")
    p = json.loads(f.read_text())
    if p.get("applied"):
        decide("allow", "This plan was already applied; the server won't write again.")
    if p.get("errors"):
        decide("deny", "The plan has errors: " + "; ".join(p["errors"]))
    if time.time() - float(p.get("created_at") or 0) > PLAN_TTL:
        decide("deny", "The plan is older than 30 minutes. Make a fresh plan and show the user the diff.")
    decide("ask", summary(p) + " Apply it? (A backup is taken first.)")


if __name__ == "__main__":
    main()
