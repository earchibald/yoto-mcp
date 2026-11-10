"""Graph and op builders for the card patterns we have run by hand on the Hilo cards.

These are pure functions: they take card documents and media, and return a story graph (with the
walks it must pass) or plan ops. The session supplies the spoken prompts and turns the result into
a plan, so nothing here writes to Yoto.
"""

from __future__ import annotations

import re
from typing import Any

from .cards import chapters, title_of

BOOK_PATTERN = r"^Book (\d+)\b"
PROMPT_TEXT = "For book {n}, click left dial. For other books, click right dial."
ANNOUNCE_TITLE = "{series} Book {n}: {subtitle}"
ANNOUNCE_TITLE_BARE = "{series} Book {n}"
INTRO_TEXT_HEAD = "{series} books {first} through {last}."
INTRO_TEXT_BOOK = "Book {n}, track {track}."


def choice_id(n: int) -> str:
    return f"choose-book{n}"


def announce_id(n: int) -> str:
    return f"announce-book{n}"


def segment_id(n: int, i: int) -> str:
    return f"book{n}-{i:02d}"


def series_name(card: dict) -> str:
    title = title_of(card)
    m = re.match(r"^(.*?)\s+Books?\b", title)
    return (m.group(1) if m else title).strip() or title


def clean_title(title: str) -> str:
    return re.sub(r"\.(mp3|m4a|wav|aac)$", "", title or "", flags=re.I)


def parse_books(card: dict, pattern: str = BOOK_PATTERN) -> dict[int, list[dict]]:
    """Group a linear card's tracks by book number, in order. Chapters that don't match are left out.

    Each entry: {title, audio: {ref, format, duration, fileSize, channels}, icon, chapter}.
    """
    rx = re.compile(pattern)
    books: dict[int, list[dict]] = {}
    for c in chapters(card):
        m = rx.match(c.get("title") or "")
        if not m:
            continue
        for t in c.get("tracks") or []:
            audio = {"ref": t.get("trackUrl")}
            for k in ("format", "duration", "fileSize", "channels"):
                if t.get(k) is not None:
                    audio[k] = t[k]
            icon = (t.get("display") or {}).get("icon16x16") or (c.get("display") or {}).get("icon16x16")
            books.setdefault(int(m.group(1)), []).append(
                {
                    "title": clean_title(t.get("title") or c.get("title")),
                    "audio": audio,
                    "icon": icon,
                    "chapter": c.get("key"),
                }
            )
    return dict(sorted(books.items()))


def announce_title(series: str, n: int, subtitles: dict[int, str]) -> str:
    sub = subtitles.get(n)
    return (
        ANNOUNCE_TITLE.format(series=series, n=n, subtitle=sub)
        if sub
        else ANNOUNCE_TITLE_BARE.format(series=series, n=n)
    )


# ------------------------------------------------------------------ book selector


def book_selector_graph(
    books: dict[int, list[dict]],
    prompts: dict[int, dict],
    announces: dict[int, dict],
    titles: dict[int, str],
    prompt_icons: dict[int, str] | None = None,
) -> dict:
    """The Hilo selector: right cycles the prompts, left picks a book, which plays to the end.

        choose-book4 --left--> announce-book4 -> book4-01 -> … -> book5-01 -> … -> stop
             |right
        choose-book5 …   choose-book8 --right--> choose-book4

    While a book plays, left and right step to the previous and next track. Previous from a book's
    first track returns to its prompt; next from the very last track returns to the first prompt.
    """
    order = list(books)
    if not order:
        raise ValueError("No books found in the source card.")
    icons = prompt_icons or {}
    nodes: list[dict[str, Any]] = []
    for idx, b in enumerate(order):
        nodes.append(
            {
                "id": choice_id(b),
                "type": "choice",
                "title": f"Book {b}?",
                "tracks": [
                    {
                        "title": f"Book {b}?",
                        "audio": prompts[b],
                        "icon": icons.get(b) or books[b][0].get("icon"),
                    }
                ],
                "left": announce_id(b),
                "right": choice_id(order[(idx + 1) % len(order)]),
            }
        )
    for idx, b in enumerate(order):
        tracks = books[b]
        first_icon = icons.get(b) or tracks[0].get("icon")
        nodes.append(
            {
                "id": announce_id(b),
                "type": "segment",
                "title": titles[b],
                "tracks": [{"title": titles[b], "audio": announces[b], "icon": first_icon}],
                "next": segment_id(b, 1),
                "left": choice_id(b),
                "right": segment_id(b, 1),
            }
        )
        for i, t in enumerate(tracks, start=1):
            last_of_book, last_book = i == len(tracks), idx == len(order) - 1
            if not last_of_book:
                nxt = segment_id(b, i + 1)
            elif not last_book:
                nxt = segment_id(order[idx + 1], 1)
            else:
                nxt = None
            node = {
                "id": segment_id(b, i),
                "type": "segment",
                "title": t["title"],
                "tracks": [{"title": t["title"], "audio": t["audio"], "icon": t.get("icon")}],
                "next": nxt or "stop",
                "left": segment_id(b, i - 1) if i > 1 else choice_id(b),
                "right": nxt or choice_id(order[0]),
            }
            nodes.append(node)

    expect = []
    for idx, b in enumerate(order):
        plays = [choice_id(x) for x in order[: idx + 1]] + [announce_id(b)]
        plays += [segment_id(bb, i) for bb in order[idx:] for i in range(1, len(books[bb]) + 1)]
        expect.append(
            {
                "presses": ["right"] * idx + ["left"],
                "plays": plays,
                "why": f"left on prompt {b} announces book {b}, then plays to the end",
            }
        )
    expect.append(
        {
            "presses": ["right"] * len(order),
            "plays": [choice_id(x) for x in order] + [choice_id(order[0])],
            "why": "right cycles every prompt and comes back to the first",
        }
    )
    return {"start": choice_id(order[0]), "nodes": nodes, "expect": expect}


# ------------------------------------------------------------------ title tracks


def title_track_ops(
    card: dict,
    titles: dict[int, dict],
    names: dict[int, str],
    pattern: str = BOOK_PATTERN,
) -> list[dict]:
    """insert_chapter ops for a title track before each book's first chapter (skips books that have one)."""
    rx = re.compile(pattern)
    existing = {c.get("key") for c in chapters(card)}
    ops, seen = [], set()
    for c in chapters(card):
        m = rx.match(c.get("title") or "")
        if not m or (b := int(m.group(1))) in seen:
            continue
        seen.add(b)
        if b not in titles or announce_id(b) in existing:
            continue
        icon = ((c.get("tracks") or [{}])[0].get("display") or {}).get("icon16x16") or (
            c.get("display") or {}
        ).get("icon16x16")
        op = {
            "op": "insert_chapter",
            "key": announce_id(b),
            "title": names[b],
            "audio": titles[b],
            "before": c["key"],
        }
        if icon:
            op["icon"] = icon
        ops.append(op)
    return ops


def book_starts(card: dict, books: list[int], pattern: str = BOOK_PATTERN) -> dict[int, int]:
    """1-based chapter number where each book starts (its title track, else its first chapter)."""
    rx = re.compile(pattern)
    chs = chapters(card)
    out: dict[int, int] = {}
    for b in books:
        for i, c in enumerate(chs, start=1):
            m = rx.match(c.get("title") or "")
            if c.get("key") == announce_id(b) or (m and int(m.group(1)) == b):
                out[b] = i
                break
    return out


def intro_text(series: str, starts: dict[int, int]) -> str:
    books = list(starts)
    parts = [INTRO_TEXT_HEAD.format(series=series, first=books[0], last=books[-1])]
    parts += [INTRO_TEXT_BOOK.format(n=b, track=t) for b, t in starts.items()]
    return " ".join(parts)


def find_intro(card: dict, intro: str = "intro") -> dict | None:
    hits = [c for c in chapters(card) if (c.get("title") or "").casefold() == intro.casefold()]
    return hits[0] if len(hits) == 1 else None
