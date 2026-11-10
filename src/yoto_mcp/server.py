"""The yoto-mcp stdio server: card, media and icon tools over a local ``Session``."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.types import ToolAnnotations

from . import cards, chooser, icons, reference, story, vault, workflows
from .session import Session

INSTRUCTIONS = """\
Tools for the user's Yoto MYO playlists and interactive cards.

- Name cards by their title, the way the user sees them in the Yoto app. Never show card ids
  to the user; they can't see them anywhere.
- Every change is plan-then-apply: call yoto_card_plan, show the user the diff and warnings,
  and only call yoto_card_apply after they agree. Apply takes a backup and verifies the write.
- Cards owned by another family account are read-only from this account. Copy their media with
  yoto_media_import before reusing it on this account's cards.
- To draw an icon, write a 16×16 character grid with a palette (yoto_icon_render) or start from
  yoto_icon_glyph; look at the preview it returns before offering the icon.
- For interactive cards, write a story graph (yoto://schema/story-graph) and use
  yoto_story_compile; it simulates the card and returns a plan. The templates
  (yoto_template_book_selector, yoto_template_title_tracks) do the Hilo patterns in one call.
- yoto_icon_select is for the icon chooser page only; don't call it yourself.
"""

mcp = FastMCP("yoto", instructions=INSTRUCTIONS)
_session: Session | None = None

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)
UPLOAD = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def session() -> Session:
    global _session
    if _session is None:
        _session = Session()
    return _session


# ------------------------------------------------------------------ accounts


@mcp.tool(annotations=READ)
def yoto_whoami() -> dict[str, Any]:
    """List the linked Yoto accounts (by the short names the user gave them) and the default one."""
    s = session()
    messages = s.finish_logins()
    accts = vault.accounts()
    return {
        "accounts": [{"name": a.name, "default": a.default} for a in accts],
        "pending_logins": list(s.pending_logins),
        "messages": messages,
        "client_configured": bool(vault.client_id()),
        "scopes": "user:content:manage offline_access",
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def yoto_account_link(name: str, open_browser: bool = True) -> dict[str, Any]:
    """Start a browser login that links a Yoto account under a short name such as "Family".

    Returns the login URL (opened in the browser by default). Call yoto_whoami after the user
    finishes logging in to confirm the account is linked.
    """
    url = session().start_login(name, open_browser=open_browser)
    return {
        "login_url": url,
        "name": name,
        "next": "Ask the user to finish logging in in the browser, then call yoto_whoami.",
    }


# ------------------------------------------------------------------ reading cards


@mcp.tool(annotations=READ)
def yoto_cards_list(
    account: str | None = None, search: str | None = None, fresh: bool = False
) -> dict[str, Any]:
    """List cards in My Playlists and the family library, by title.

    `owned: false` cards belong to another family account and are read-only from this one.
    """
    acct = vault.find_account(account)
    entries = session().index(account, fresh=fresh)
    if search:
        want = search.casefold()
        entries = [e for e in entries if want in e.title.casefold()]
    rows = sorted((e.row() for e in entries), key=lambda r: (not r["owned"], r["title"].casefold()))
    return {"account": acct.name, "count": len(rows), "cards": rows}


@mcp.tool(annotations=READ)
def yoto_card_get(card: str, view: str = "outline", account: str | None = None) -> dict[str, Any]:
    """Fetch one card by title. `view` is `summary`, `outline` (chapter list) or `full` (whole document).

    Media in every view is given as yoto:# refs, which are safe to reuse in plans on the same account.
    """
    acct, _api, _entry, doc, owned = session().read(card, account)
    out: dict[str, Any] = {"summary": cards.summary(doc, owned, acct.name)}
    if view in ("outline", "full"):
        out["outline"] = cards.outline(doc)
    if view == "full":
        out["document"] = {k: v for k, v in doc.items() if k not in ("userId",)}
    return out


@mcp.tool(annotations=READ)
def yoto_card_playable(
    card: str, chapters: list[str] | None = None, account: str | None = None
) -> dict[str, Any]:
    """Short-lived signed URLs for listening to or spot-checking a card's tracks. Don't store them."""
    s = session()
    acct, api = s.account(account)
    entry = s.find(card, account)
    doc = api.get_playable(entry.card_id)
    rows = []
    for c in cards.chapters(doc):
        if chapters and c.get("key") not in chapters and c.get("title") not in chapters:
            continue
        for t in c.get("tracks") or []:
            rows.append(
                {
                    "chapter": c.get("title"),
                    "key": c.get("key"),
                    "track": t.get("title"),
                    "duration": t.get("duration"),
                    "url": t.get("trackUrl"),
                }
            )
    return {"card": entry.title, "account": acct.name, "tracks": rows}


# ------------------------------------------------------------------ plan and apply

OPS_HELP = """\
Ops (address chapters by key, or by exact chapter title):
- insert_chapter {key, title, audio: <media from yoto_media_upload_wait>, icon?, before|after: chapter, or position: start|end|N}
  or {key, title, tracks: [{key?, title, audio, icon?}], ...}
- remove_chapter {chapter}
- move_chapter {chapter, before|after|position}
- retitle {title} (the card) or {chapter, track?, title}
- set_track_audio {chapter, track?, audio}
- set_icon {chapter, track?, icon: "yoto:#…"} (no track: the chapter and all its tracks)
- set_events {chapter, track?, events: {onEnd|onLhb|onRhb: {cmd: goto|stop, params?} | null}}
- set_config {config: {resumeTimeout, autoadvance, disableTrackNav, …: value | null}, playbackType?}
- set_cover {url}
- set_metadata {metadata: {description, author, …}}
- replace_refs {map: {"yoto:#old": "yoto:#new" | media}} (after yoto_media_import)
- renumber_labels {} (runs automatically after structural ops)
"""


PLAN_DOC = (
    """Plan an edit to a card without writing anything. Returns a plan_id, a readable diff and warnings.

`card` is the card's title, or "new" (with `title`) to plan a new card. Show the user the diff
before applying. Plans expire after 30 minutes.

"""
    + OPS_HELP
)


@mcp.tool(annotations=READ, description=PLAN_DOC)
def yoto_card_plan(
    card: str, ops: list[dict], account: str | None = None, title: str | None = None, renumber: bool = True
) -> dict[str, Any]:
    return session().make_plan(card, ops, account, title=title, renumber=renumber).public()


@mcp.tool(annotations=WRITE)
def yoto_card_apply(plan_id: str, verify_playback: bool = True) -> dict[str, Any]:
    """Apply a plan the user has seen: checks the card hasn't changed, backs it up, writes, re-reads, verifies.

    Applying the same plan twice does nothing the second time.
    """
    return session().apply(plan_id, verify_playback=verify_playback)


@mcp.tool(annotations=READ)
def yoto_card_backups(card: str, account: str | None = None) -> dict[str, Any]:
    """List the local backups of a card, newest first (taken before every apply; the last 20 are kept)."""
    return {"card": card, "backups": session().backups(card, account)}


@mcp.tool(annotations=READ)
def yoto_card_restore(card: str, backup: str | None = None, account: str | None = None) -> dict[str, Any]:
    """Plan restoring a card from a backup (the newest by default). Apply the returned plan to restore."""
    return session().restore_plan(card, backup, account).public()


# ------------------------------------------------------------------ media


@mcp.tool(annotations=UPLOAD)
def yoto_media_upload_begin(sha256: str, filename: str, account: str | None = None) -> dict[str, Any]:
    """Start an audio upload. `exists: true` means Yoto already has these bytes (no PUT needed).

    Otherwise PUT the file to upload_url (bin/yoto-upload does this), then call yoto_media_upload_wait.
    """
    _acct, api = session().account(account)
    ticket = api.audio_upload_begin(sha256, filename)
    return {"exists": ticket.exists, "upload_id": ticket.upload_id, "upload_url": ticket.upload_url}


@mcp.tool(annotations=UPLOAD)
def yoto_media_upload_wait(
    upload_id: str, account: str | None = None, timeout: float = 180
) -> dict[str, Any]:
    """Wait for Yoto to transcode an upload. Returns the yoto:# ref plus duration, fileSize, channels, format."""
    s = session()
    acct, api = s.account(account)
    media = api.audio_upload_wait(upload_id, timeout=timeout)
    s.note_owned(acct.sub, media.ref)
    return {**media.as_dict(), "account": acct.name}


@mcp.tool(annotations=UPLOAD)
def yoto_media_upload_file(path: str, account: str | None = None) -> dict[str, Any]:
    """Upload a local audio file in one step and wait for transcoding. The bytes never pass through Claude."""
    return session().upload_file(path, account)


@mcp.tool(annotations=UPLOAD)
def yoto_media_import(
    card: str, from_account: str, to_account: str | None = None, chapters: list[str] | None = None
) -> dict[str, Any]:
    """Copy a card's audio and icons from one linked account into another (media refs are per account).

    Returns a map from old refs to new media; use it in a `replace_refs` op on the destination's copy.
    """
    return session().media_import(card, from_account, to_account, chapters)


# ------------------------------------------------------------------ icons


def _render_result(rendered: list[icons.Rendered]) -> list:
    sheet = icons.contact_sheet([(r.name, r.png) for r in rendered])
    info = {
        "icons": [r.info() for r in rendered],
        "preview": "Each icon at 8×: on black (as the player shows it), then on a checkerboard.",
    }
    return [json.dumps(info, indent=1), Image(data=sheet, format="png")]


@mcp.tool(annotations=LOCAL, structured_output=False)
def yoto_icon_render(specs: list[dict]) -> list:
    """Render 16×16 icons from character grids and return a preview sheet to look at.

    Each spec: {"name": str, "rows": [16 strings of 16 chars], "palette": {char: "#RRGGBB" | "#RRGGBBAA" | "transparent"}}.
    "." and " " are transparent unless the palette says otherwise. Style: strokes at least 2 px, 4 colours
    or fewer, a 1 px margin, bright colours that read on black, one idea per icon. Fix lint warnings and
    re-render (at most three rounds) before offering an icon.
    """
    return _render_result([icons.render(spec) for spec in specs])


@mcp.tool(annotations=LOCAL, structured_output=False)
def yoto_icon_glyph(
    text: str, colour: str = "#ffffff", outline: str | None = None, name: str | None = None
) -> list:
    """A centred icon of 1–2 characters (digits, A–Z, ? ! + -) or one symbol (★ ♥ ♪ ☾ ☀).

    Returns the spec (editable, for yoto_icon_render) and its preview.
    """
    spec = icons.glyph(text, colour, outline, name)
    rendered = icons.render(spec)
    out = _render_result([rendered])
    data = json.loads(out[0])
    data["spec"] = spec
    return [json.dumps(data, indent=1), out[1]]


@mcp.tool(annotations=LOCAL, structured_output=False)
def yoto_icon_from_image(
    path: str, max_colours: int = 6, alpha_threshold: int = 128, name: str | None = None
) -> list:
    """Downscale and quantize a local image into a 16×16 spec, as a starting point to clean up."""
    spec = icons.from_image(str(Path(path).expanduser()), max_colours, alpha_threshold, name)
    rendered = icons.render(spec)
    out = _render_result([rendered])
    data = json.loads(out[0])
    data["spec"] = spec
    return [json.dumps(data, indent=1), out[1]]


def _upload_icon(icon: str, account: str | None = None, auto_convert: bool = False) -> dict[str, Any]:
    s = session()
    acct, api = s.account(account)
    hit = icons.cached(icon) if len(icon) == 64 and all(c in "0123456789abcdef" for c in icon) else None
    if hit:
        png, meta = hit
        filename = f"{(meta.get('name') or icon[:8]).replace(' ', '-')}.png"
    else:
        f = Path(icon).expanduser()
        if not f.is_file():
            raise FileNotFoundError(f"{icon!r} is neither a rendered icon's sha256 nor a file.")
        png, filename = f.read_bytes(), f.name
    if not auto_convert and filename.lower().endswith(".png") and (problems := icons.check_png(png)):
        raise ValueError(
            "Not a 16×16 RGBA PNG (" + "; ".join(problems) + "). Re-render it, or pass "
            "auto_convert=true to let Yoto resize it."
        )
    up = api.upload_icon(png, filename, auto_convert=auto_convert)
    s.note_owned(acct.sub, up["ref"])
    return {"ref": up["ref"], "new": up["new"], "account": acct.name}


@mcp.tool(annotations=UPLOAD)
def yoto_icon_upload(icon: str, account: str | None = None, auto_convert: bool = False) -> dict[str, Any]:
    """Upload an icon: the sha256 of a rendered icon, or a local PNG/GIF path. Returns its yoto:# ref.

    Without auto_convert the file must be exactly 16×16 RGBA PNG, so Yoto stores the pixels as drawn.
    Use the ref in a set_icon op.
    """
    return _upload_icon(icon, account, auto_convert)


@mcp.tool(annotations=READ)
def yoto_icons_search(query: str, account: str | None = None, limit: int = 24) -> dict[str, Any]:
    """Search this account's uploaded icons and Yoto's public icons by title and tags."""
    return {"query": query, "icons": session().search_icons(query, account, limit)}


@mcp.tool(annotations=UPLOAD)
def yoto_cover_upload(path: str, account: str | None = None) -> dict[str, Any]:
    """Upload cover art from a local image (Yoto resizes it). Use the url in a set_cover op."""
    _acct, api = session().account(account)
    f = Path(path).expanduser()
    ext = f.suffix.lower().lstrip(".")
    ctype = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext)
    if not ctype:
        raise ValueError("Cover art must be a JPEG, PNG, GIF or WebP file.")
    return api.upload_cover(f.read_bytes(), ctype)


# ------------------------------------------------------------------ spoken prompts


@mcp.tool(annotations=UPLOAD)
def yoto_tts(
    text: str,
    account: str | None = None,
    voice: str | None = None,
    model: str | None = None,
    pronunciations: dict[str, str] | None = None,
    check: bool = True,
) -> dict[str, Any]:
    """Speak a short prompt on this machine, check it with speech-to-text, and upload it.

    `text` is what the card displays; the pronunciation map rewrites it for speaking
    ("Hilo" → "High-low" by default). Returns media (ref, duration, …) to use as a track's audio,
    plus the transcript and a match score. Listen again if the match is low.
    """
    return workflows.prompt_media(session(), text, account, voice, model, pronunciations, check)


# ------------------------------------------------------------------ interactive cards

GRAPH_HELP = """\
A graph: {"start"?: id, "nodes": [{"id", "type": "choice"|"segment"|"end", "title",
"tracks": [{"title", "audio": "yoto:#…" | media | asset name, "icon"?}], "next"?: id|"stop",
"left"?: id, "right"?: id}], "assets"?: {name: media}, "expect"?: [{"presses": ["right", "left"],
"plays": [node ids]}]}. A choice waits for a button; a segment plays on to `next`; an end stops.
Node order is chapter (dial) order, with the start node first. Full schema: yoto://schema/story-graph.
"""


@mcp.tool(
    annotations=LOCAL,
    description="Check a story graph for unreachable nodes, dead ends, missing "
    "targets and endless loops. Writes nothing.\n\n" + GRAPH_HELP,
)
def yoto_story_validate(graph: dict[str, Any]) -> dict[str, Any]:
    return story.validate(graph)


@mcp.tool(
    annotations=READ,
    description="Compile a story graph into an interactive card, walk it like the "
    "player would, and (if the walk is clean) return a plan writing it to `card` (a title, or "
    '"new"). Show the user the simulator result and the diff, then yoto_card_apply.\n\n' + GRAPH_HELP,
)
def yoto_story_compile(
    graph: dict[str, Any],
    title: str,
    card: str = "new",
    account: str | None = None,
    config: dict[str, Any] | None = None,
    cover: str | None = None,
) -> dict[str, Any]:
    return workflows.compile_plan(session(), graph, title, card, account, config, cover)


@mcp.tool(annotations=READ)
def yoto_story_simulate(
    presses: list[str],
    card: str | None = None,
    graph: dict[str, Any] | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Replay button presses against a card (by title) or a draft graph and list what plays.

    `presses` is a list of "left", "right" or "end" (let the track finish). After the presses run
    out the card plays on until it stops or waits for a button. Also returns the full walk report.
    """
    return workflows.simulate(session(), card, graph, presses, account)


@mcp.tool(annotations=READ)
def yoto_story_from_card(card: str, account: str | None = None) -> dict[str, Any]:
    """Turn a card's events back into a story graph (one node per chapter) for editing and recompiling."""
    _acct, _api, entry, doc, owned = session().read(card, account)
    return {"card": entry.title, "owned": owned, "graph": story.from_card(doc), "mermaid": story.mermaid(doc)}


@mcp.tool(annotations=UPLOAD)
def yoto_template_book_selector(
    source_card: str,
    subtitles: dict[str, str] | None = None,
    account: str | None = None,
    title: str | None = None,
    target: str | None = None,
    voice: str | None = None,
    pattern: str = r"^Book (\d+)\b",
    prompt_text: str = "For book {n}, click left dial. For other books, click right dial.",
    prompt_icons: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a "choose a book" interactive card from a linear multi-book card, as a plan.

    Right cycles the books' prompts, left picks one: it announces "<Series> Book N: <subtitle>"
    and plays that book and the ones after it. Left/right step tracks while a book plays.
    Generates and uploads the spoken prompts (only the upload writes anything). `subtitles` maps
    book number to subtitle. `target` is the card to rebuild (default: the existing
    "<source> (Choose a Book)" if there is one, else a new card). Use the source card's owner account.
    """
    return workflows.book_selector(
        session(), source_card, subtitles, account, title, target, voice, pattern, prompt_text, prompt_icons
    )


@mcp.tool(annotations=UPLOAD)
def yoto_template_title_tracks(
    card: str,
    subtitles: dict[str, str] | None = None,
    account: str | None = None,
    pattern: str = r"^Book (\d+)\b",
    intro: str | None = "intro",
    voice: str | None = None,
) -> dict[str, Any]:
    """Plan inserting a spoken "<Series> Book N: <subtitle>" title track before each book of a linear card.

    Also regenerates the `intro` chapter's audio with recalculated track numbers ("Book 4, track 2.").
    Books that already have a title track are left alone. Returns a plan; apply it after the user agrees.
    """
    return workflows.title_tracks(session(), card, subtitles, account, pattern, intro, voice)


# ------------------------------------------------------------------ icon chooser

CHOOSER_URI = "ui://yoto/icon-chooser"
_choosers = chooser.ChooserServer()


def _client_has_apps(ctx: Context | None) -> bool:
    try:
        params = ctx.session.client_params if ctx else None
    except Exception:  # noqa: BLE001
        return False
    if params is None:
        return False
    caps = params.capabilities
    blobs = [getattr(caps, "experimental", None) or {}, getattr(caps, "model_extra", None) or {}]
    extensions = (getattr(caps, "model_extra", None) or {}).get("extensions") or {}
    blobs.append(extensions)
    return any("io.modelcontextprotocol/ui" in b or "ui" in b for b in blobs)


def _open_chooser(
    card: str, generated: dict[str, list[str]] | None, per_track: bool, account: str | None
) -> chooser.Chooser:
    s = session()
    acct, api, entry, doc, owned = s.read(card, account)
    if not owned:
        raise PermissionError(f"{entry.title!r} belongs to another account; open the chooser from its owner.")
    try:
        playable = api.get_playable(entry.card_id)
    except Exception:  # noqa: BLE001 - current icons just won't show
        playable = {}
    public = s._public_icons(api)
    mine = api.list_icons("me")
    s.note_owned(acct.sub, *[f"yoto:#{i['mediaId']}" for i in public + mine if i.get("mediaId")])
    rows = chooser.build_rows(doc, playable, public, mine, generated, per_track)
    cid, token = _choosers.new_id()
    ch = chooser.Chooser(cid, token, acct.name, entry.title, entry.card_id, rows)
    _choosers.add(ch, public, mine)
    return ch


def _chooser_result(ch: chooser.Chooser) -> dict[str, Any]:
    out: dict[str, Any] = {
        "chooser_id": ch.chooser_id,
        "card": ch.card,
        "done": ch.done,
        "selections": list(ch.selections.values()),
        "requests": ch.requests,
    }
    if ch.selections:
        ops, uploaded = chooser.selections_to_ops(
            list(ch.selections.values()), lambda sha: _upload_icon(sha, ch.account)["ref"]
        )
        p = session().make_plan(ch.card, ops, ch.account)
        out["plan"] = p.public()
        out["uploaded_icons"] = uploaded
    if ch.requests:
        out["next"] = (
            "The user asked for new candidates. Draw them with yoto_icon_render, then reopen the "
            "chooser with generated={chapter_key: [sha256, …]}."
        )
    elif ch.selections:
        out["next"] = "Show the user the plan's diff, then yoto_card_apply."
    return out


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    meta={"ui": {"resourceUri": CHOOSER_URI}, "ui/resourceUri": CHOOSER_URI},
)
async def yoto_icon_chooser(
    card: str,
    generated: dict[str, list[str]] | None = None,
    per_track: bool = False,
    account: str | None = None,
    mode: str = "auto",
    wait_minutes: float = 15,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Open an icon picker over a card: one row per chapter, with Yoto's matching icons, the account's own
    icons and any icons you drew (`generated`: {chapter_key or "*": [sha256 from yoto_icon_render]}).

    In Claude Code it opens in the browser and returns when the user presses Done, with their picks as a
    plan of set_icon ops (generated icons are uploaded first). In an MCP Apps host it renders inline and
    returns at once; call yoto_icon_chooser_result after the user says they're done.
    """
    import asyncio
    import webbrowser

    ch = _open_chooser(card, generated, per_track, account)
    inline = mode == "inline" or (mode == "auto" and _client_has_apps(ctx))
    if inline:
        return {
            **ch.state(),
            "mode": "inline",
            "next": "When the user says they're done, call yoto_icon_chooser_result.",
        }
    url = _choosers.url(ch)
    webbrowser.open(url)
    deadline = time.monotonic() + wait_minutes * 60
    while not ch.done and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
    out = _chooser_result(ch)
    out["mode"] = "browser"
    out["url"] = url
    if not ch.done:
        out["note"] = (
            "The user hasn't pressed Done yet. The page stays open; call yoto_icon_chooser_result "
            "later to collect the picks."
        )
    return out


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def yoto_icon_select(
    chooser_id: str,
    selections: list[dict[str, Any]] | None = None,
    requests: list[dict[str, Any]] | None = None,
    done: bool = False,
) -> dict[str, Any]:
    """Save picks from the icon chooser page. Called by the page only, not by Claude."""
    ch = _choosers.choosers.get(chooser_id)
    if ch is None:
        raise LookupError("That chooser is closed. Open a new one with yoto_icon_chooser.")
    ch.record(selections, requests, done)
    return {"saved": len(ch.selections), "done": ch.done}


@mcp.tool(annotations=READ)
def yoto_icon_chooser_result(chooser_id: str) -> dict[str, Any]:
    """The picks made so far in an icon chooser, as a plan of set_icon ops, plus any requests for new icons."""
    ch = _choosers.choosers.get(chooser_id)
    if ch is None:
        raise LookupError("No chooser with that id in this session.")
    return _chooser_result(ch)


# ------------------------------------------------------------------ resources


@mcp.resource(
    CHOOSER_URI,
    name="icon-chooser",
    mime_type="text/html;profile=mcp-app",
    meta={"ui": {"csp": {"resourceDomains": ["https://*.yotoplay.com"]}, "prefersBorder": True}},
)
def chooser_page() -> str:
    return chooser.PAGE.read_text()


@mcp.resource("yoto://schema/story-graph", name="story-graph-schema", mime_type="application/json")
def story_graph_schema() -> str:
    return json.dumps(story.GRAPH_SCHEMA, indent=1)


@mcp.resource("yoto://docs/interactive", name="interactive-reference", mime_type="text/markdown")
def interactive_docs() -> str:
    return reference.INTERACTIVE


@mcp.resource("yoto://card/{card_id}", name="card", mime_type="application/json")
def card_resource(card_id: str) -> str:
    s = session()
    _acct, api = s.account(None)
    doc = api.get_card(card_id).card
    return json.dumps({k: v for k, v in doc.items() if k != "userId"}, indent=1)


@mcp.resource("yoto://card/{card_id}/outline", name="card-outline", mime_type="text/markdown")
def card_outline_resource(card_id: str) -> str:
    _acct, api = session().account(None)
    return cards.outline_markdown(api.get_card(card_id).card)


@mcp.resource("yoto://card/{card_id}/graph", name="card-graph", mime_type="text/vnd.mermaid")
def card_graph_resource(card_id: str) -> str:
    _acct, api = session().account(None)
    return story.mermaid(api.get_card(card_id).card)


@mcp.resource("yoto://icons/{sha256}", name="icon", mime_type="application/json")
def icon_resource(sha256: str) -> str:
    hit = icons.cached(sha256)
    if not hit:
        raise LookupError("No rendered icon with that sha256.")
    png, meta = hit
    import base64

    return json.dumps({**meta, "png_base64": base64.b64encode(png).decode()})


# ------------------------------------------------------------------ prompts


@mcp.prompt(name="book-selector", description="Build a choose-a-book interactive card from a linear card.")
def book_selector_prompt(source_card: str, subtitles: str = "", voice: str = "") -> str:
    return (
        f"Build a choose-a-book interactive card from the Yoto card {source_card!r}.\n"
        f"Subtitles by book number: {subtitles or '(ask me, or leave them out)'}. Voice: {voice or 'the default'}.\n"
        "1. yoto_card_get the source (outline) and confirm which chapters are books.\n"
        "2. Run yoto_template_book_selector with the owner account.\n"
        "3. Show me the TTS transcripts with low matches, the simulator walks and the plan diff.\n"
        "4. Apply only after I say yes, then confirm the read-back verified."
    )


@mcp.prompt(name="add-title-tracks", description="Add spoken title tracks before each book of a linear card.")
def add_title_tracks_prompt(card: str, titles: str = "", pattern: str = r"^Book (\d+)\b") -> str:
    return (
        f"Add a spoken title track before each book of the Yoto card {card!r}.\n"
        f"Book subtitles: {titles or '(ask me)'}. Chapters for book N match {pattern!r}.\n"
        "Run yoto_template_title_tracks, show me the diff and the new intro text, and apply after I agree."
    )


@mcp.prompt(
    name="audit-card", description="Check a card for foreign refs, label gaps, dead ends and stale intros."
)
def audit_card_prompt(card: str) -> str:
    return (
        f"Audit the Yoto card {card!r} without changing it.\n"
        "- yoto_card_get (full): look for audio or icons owned by another account, labels that don't run "
        "1..N, chapters without icons, and an intro whose spoken track numbers no longer match.\n"
        "- If it is interactive: yoto_story_simulate with no presses and with each button, and report dead "
        "buttons, unreachable chapters and loops.\n"
        "Report findings as a short list, worst first, each with the fix (as plan ops) I could apply."
    )


def run() -> None:
    mcp.run()
