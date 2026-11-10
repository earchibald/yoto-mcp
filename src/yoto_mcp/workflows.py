"""Multi-step jobs built on a ``Session``: spoken prompts, story compiling, and the card templates.

Each job ends in a plan (never a write), so the user sees the diff before anything changes.
"""

from __future__ import annotations

from typing import Any

from . import cards, plan, story, templates, tts
from .session import Session

LOW_MATCH = 0.75


def owner_of(s: Session, card_id: str) -> str | None:
    """The short name of the linked account that owns a card, if any does."""
    from . import vault

    for a in vault.accounts():
        try:
            entries = s.index(a.name)
        except Exception:  # noqa: BLE001 - an account with a broken login just doesn't answer
            continue
        if any(e.card_id == card_id and e.owned for e in entries):
            return a.name
    return None


def _require_owner(s: Session, entry: cards.Entry, owned: bool, account: str | None) -> None:
    if owned:
        return
    who = owner_of(s, entry.card_id)
    hint = f" Pass account={who!r}." if who else ""
    raise PermissionError(
        f"{entry.title!r} belongs to another account, and its audio can only be reused by its owner.{hint}"
    )


# ------------------------------------------------------------------ prompts


def prompt_media(
    s: Session,
    text: str,
    account: str | None = None,
    voice: str | None = None,
    model: str | None = None,
    pronunciations: dict[str, str] | None = None,
    check: bool = True,
) -> dict:
    """Speak ``text``, upload it, and return the media (plus the TTS check)."""
    clip = tts.speak(text, voice=voice, model=model, pronunciations=pronunciations, check=check)
    media = s.upload_file(str(clip.path), account)
    media.pop("file", None)
    out = {**media, "text": text, "tts": clip.info()}
    if clip.match is not None and clip.match < LOW_MATCH:
        out["warning"] = (
            f"The transcript ({clip.transcript!r}) doesn't match the text well ({clip.match:.2f}); "
            "listen to it, or adjust the pronunciation map and regenerate."
        )
    return out


def _tts_report(prompts: list[dict]) -> dict:
    return {
        "clips": len(prompts),
        "warnings": [p["warning"] for p in prompts if p.get("warning")],
        "transcripts": [
            {"text": p["text"], "heard": p["tts"].get("transcript"), "match": p["tts"].get("match")}
            for p in prompts
            if p["tts"].get("transcript") is not None
        ],
    }


# ------------------------------------------------------------------ compiling


def compile_plan(
    s: Session,
    graph: dict,
    title: str,
    card: str = "new",
    account: str | None = None,
    config: dict | None = None,
    cover: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Compile a story graph and simulate it; if the walk is clean, plan writing it to ``card``."""
    report = story.validate(graph)
    if not report["valid"]:
        return {"compiled": False, "errors": report["errors"], "warnings": report["warnings"]}
    if card.strip().casefold() != "new":
        _acct, _api, entry, base, owned = s.read(card, account)
        base_meta = base.get("metadata") or {}
        if cover is None:
            cover = (base_meta.get("cover") or {}).get("imageL")
        metadata = {
            **{k: base_meta[k] for k in ("description", "author") if k in base_meta},
            **(metadata or {}),
        }
        card = entry.title
    doc = story.compile_graph(graph, title, config=config, metadata=metadata, cover=cover)
    sim = story.walk(doc, graph.get("expect"), keys_for(graph))
    out: dict[str, Any] = {
        "compiled": True,
        "graph_warnings": report["warnings"],
        "simulator": sim,
        "chapters": len(cards.chapters(doc)),
    }
    if not sim["ok"]:
        out["note"] = "The simulator found problems, so no plan was made. Fix the graph and compile again."
        return out
    p = s.make_plan(card, [{"op": "replace_document", "card": doc}], account, title=title, renumber=False)
    out.update(p.public())
    return out


def keys_for(graph: dict) -> dict[str, str]:
    g = story.normalize(graph)
    nodes = g["nodes"]
    start = next((n for n in nodes if n["id"] == g.get("start")), nodes[0] if nodes else None)
    ordered = ([start] if start else []) + [n for n in nodes if n is not start]
    return story.chapter_keys(ordered)


def simulate(
    s: Session, card: str | None, graph: dict | None, presses: list[str], account: str | None
) -> dict:
    if graph is not None:
        doc = story.compile_graph(graph, graph.get("title") or "draft")
        key_of = keys_for(graph)
    elif card:
        _a, _api, _e, doc, _o = s.read(card, account)
        key_of = {}
    else:
        raise ValueError("Give a card title or a graph.")
    node_of = {v: k for k, v in key_of.items()}
    run = story.replay(doc, presses)
    for p in run["plays"]:
        if p["chapter"] in node_of:
            p["node"] = node_of[p["chapter"]]
    return {"presses": presses, **run, "walk": story.walk(doc)}


# ------------------------------------------------------------------ templates


def _int_keys(d: dict | None) -> dict[int, Any]:
    return {int(k): v for k, v in (d or {}).items()}


def book_selector(
    s: Session,
    source: str,
    subtitles: dict | None = None,
    account: str | None = None,
    title: str | None = None,
    target: str | None = None,
    voice: str | None = None,
    pattern: str = templates.BOOK_PATTERN,
    prompt_text: str = templates.PROMPT_TEXT,
    prompt_icons: dict | None = None,
    series: str | None = None,
) -> dict:
    acct, _api, entry, src, owned = s.read(source, account)
    _require_owner(s, entry, owned, account)
    books = templates.parse_books(src, pattern)
    if not books:
        raise ValueError(
            f"No chapters of {entry.title!r} match {pattern!r}; pass a pattern whose group 1 is the book number."
        )
    subs = _int_keys(subtitles)
    series = series or templates.series_name(src)
    names = {b: templates.announce_title(series, b, subs) for b in books}
    prompts: dict[int, dict] = {}
    announces: dict[int, dict] = {}
    spoken: list[dict] = []
    for b in books:
        prompts[b] = prompt_media(s, prompt_text.format(n=b), acct.name, voice)
        announces[b] = prompt_media(s, names[b] + ".", acct.name, voice)
        spoken += [prompts[b], announces[b]]
    graph = templates.book_selector_graph(books, prompts, announces, names, _int_keys(prompt_icons))
    title = title or f"{entry.title} (Choose a Book)"
    if target is None:
        target = "new"
        try:
            existing = s.find(title, acct.name, prefer_owned=True)
            if existing.owned and cards._norm(existing.title) == cards._norm(title):
                target = existing.title
        except LookupError:
            pass
    cover = ((src.get("metadata") or {}).get("cover") or {}).get("imageL")
    out = compile_plan(
        s,
        graph,
        title,
        target,
        acct.name,
        cover=cover,
        metadata={
            "description": f"Choose a book from {entry.title}: right for the next book, left to play it."
        },
    )
    out["books"] = {str(b): len(t) for b, t in books.items()}
    out["tts"] = _tts_report(spoken)
    out["graph"] = graph
    return out


def title_tracks(
    s: Session,
    card: str,
    subtitles: dict | None = None,
    account: str | None = None,
    pattern: str = templates.BOOK_PATTERN,
    intro: str | None = "intro",
    voice: str | None = None,
    series: str | None = None,
) -> dict:
    acct, _api, entry, doc, owned = s.read(card, account)
    _require_owner(s, entry, owned, account)
    books = templates.parse_books(doc, pattern)
    if not books:
        raise ValueError(f"No chapters of {entry.title!r} match {pattern!r}.")
    subs = _int_keys(subtitles)
    series = series or templates.series_name(doc)
    names = {b: templates.announce_title(series, b, subs) for b in books}
    have = {c.get("key") for c in cards.chapters(doc)}
    spoken: list[dict] = []
    titles: dict[int, dict] = {}
    for b in books:
        if templates.announce_id(b) not in have:
            titles[b] = prompt_media(s, names[b] + ".", acct.name, voice)
            spoken.append(titles[b])
    ops = templates.title_track_ops(doc, titles, names, pattern)
    after = plan.build(doc, ops)
    starts = templates.book_starts(after, list(books), pattern)
    intro_text = None
    if intro:
        ch = templates.find_intro(after, intro)
        if ch is not None:
            intro_text = templates.intro_text(series, starts)
            media = prompt_media(s, intro_text, acct.name, voice)
            spoken.append(media)
            ops.append({"op": "set_track_audio", "chapter": ch["key"], "audio": media})
    p = s.make_plan(entry.title, ops, acct.name)
    out = p.public()
    out["book_starts"] = {str(b): n for b, n in starts.items()}
    out["intro_text"] = intro_text
    out["tts"] = _tts_report(spoken)
    return out
