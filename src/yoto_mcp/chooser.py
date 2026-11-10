"""The icon chooser: pick a 16×16 icon per chapter (or track) from generated, Yoto and own icons.

The same page runs two ways:

* **Browser** (Claude Code): the server serves it on a loopback port with a one-time token in the
  URL and opens it. The page posts picks to ``/select`` as they happen; Done ends the wait.
* **Inline** (an MCP Apps host such as Claude Desktop): the host renders ``ui://yoto/icon-chooser``.
  The page gets the chooser state from the tool result and saves picks by calling
  ``yoto_icon_select`` through the app bridge.

Either way the picks come back as ``set_icon`` ops in a plan.
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import cards, icons, refs

PAGE = Path(__file__).with_name("chooser.html")
MAX_PER_SOURCE = 8
STOP_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "of",
    "to",
    "in",
    "on",
    "for",
    "book",
    "chapter",
    "track",
    "part",
    "mp3",
}


@dataclass
class Chooser:
    chooser_id: str
    token: str
    account: str
    card: str
    card_id: str
    rows: list[dict]
    selections: dict[str, dict] = field(default_factory=dict)  # row id → {chapter_key, track_key?, icon}
    requests: list[dict] = field(default_factory=list)
    done: bool = False
    created_at: float = field(default_factory=time.time)

    def state(self) -> dict:
        return {
            "chooser_id": self.chooser_id,
            "card": self.card,
            "account": self.account,
            "rows": self.rows,
            "selections": list(self.selections.values()),
            "requests": self.requests,
            "done": self.done,
        }

    def record(self, selections: list[dict] | None, requests: list[dict] | None, done: bool) -> None:
        valid = {r["id"]: r for r in self.rows}
        for sel in selections or []:
            row = valid.get(sel.get("row") or _row_id(sel.get("chapter_key"), sel.get("track_key")))
            if not row:
                continue
            if sel.get("icon"):
                self.selections[row["id"]] = {
                    k: v
                    for k, v in (
                        ("chapter_key", row["chapter_key"]),
                        ("track_key", row.get("track_key")),
                        ("icon", sel["icon"]),
                    )
                    if v
                }
            else:
                self.selections.pop(row["id"], None)
        for req in requests or []:
            if req.get("note") and req.get("chapter_key"):
                self.requests.append({"chapter_key": req["chapter_key"], "note": str(req["note"])[:300]})
        self.done = self.done or done


def _row_id(chapter_key: str | None, track_key: str | None) -> str:
    return f"{chapter_key}/{track_key}" if track_key else str(chapter_key)


def _words(text: str) -> list[str]:
    import re

    return [w for w in re.findall(r"[a-z]+", (text or "").casefold()) if len(w) > 2 and w not in STOP_WORDS]


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _icon_url_map(playable: dict) -> dict[str, str]:
    """yoto:# ref → signed URL, from a playable read, so the page can show current icons."""
    out: dict[str, str] = {}
    for c in cards.chapters(playable):
        for holder in [c.get("display") or {}] + [t.get("display") or {} for t in c.get("tracks") or []]:
            url = holder.get("icon16x16")
            if isinstance(url, str) and url.startswith("https://"):
                out[refs.to_ref(url) or url] = url
    return out


def build_rows(
    doc: dict,
    playable: dict | None,
    public: list[dict],
    mine: list[dict],
    generated: dict[str, list[str]] | None,
    per_track: bool,
) -> list[dict]:
    urls = _icon_url_map(playable or {})
    pool = [("yoto", i) for i in public] + [("mine", i) for i in mine]
    generated = generated or {}
    rows = []
    for n, c in enumerate(cards.chapters(doc), start=1):
        targets: list[tuple[str | None, str]] = [(None, c.get("title") or c["key"])]
        if per_track and len(c.get("tracks") or []) > 1:
            targets = [(t.get("key"), t.get("title") or "") for t in c["tracks"]]
        for track_key, title in targets:
            holder = c.get("display") or {}
            if track_key:
                t = next(t for t in c["tracks"] if t.get("key") == track_key)
                holder = t.get("display") or holder
            current = holder.get("icon16x16")
            cands: list[dict] = []
            for sha in dict.fromkeys(generated.get(c["key"], []) + generated.get("*", [])):
                hit = icons.cached(sha)
                if hit:
                    cands.append(
                        {
                            "icon": sha,
                            "src": _data_uri(hit[0]),
                            "source": "generated",
                            "title": hit[1].get("name") or sha[:8],
                        }
                    )
            words = set(_words(title))
            scored = []
            for source, item in pool:
                hay = set(_words(" ".join([item.get("title") or "", *(item.get("publicTags") or [])])))
                score = len(words & hay)
                if score and item.get("mediaId") and item.get("url"):
                    scored.append((-score, source, item))
            scored.sort(key=lambda x: x[0])
            per_source: dict[str, int] = {}
            for _s, source, item in scored:
                if per_source.get(source, 0) >= MAX_PER_SOURCE:
                    continue
                per_source[source] = per_source.get(source, 0) + 1
                cands.append(
                    {
                        "icon": refs.REF_PREFIX + item["mediaId"],
                        "src": item["url"],
                        "source": source,
                        "title": item.get("title") or "",
                    }
                )
            rows.append(
                {
                    "id": _row_id(c["key"], track_key),
                    "chapter_key": c["key"],
                    "track_key": track_key,
                    "label": c.get("overlayLabel") or str(n),
                    "title": title,
                    "current": {"icon": current, "src": urls.get(current or "")} if current else None,
                    "candidates": cands,
                }
            )
    return rows


def search_pool(public: list[dict], mine: list[dict], query: str, limit: int = 48) -> list[dict]:
    words = [w for w in query.casefold().split() if w]
    out = []
    for source, items in (("mine", mine), ("yoto", public)):
        for item in items:
            hay = " ".join([item.get("title") or "", *(item.get("publicTags") or [])]).casefold()
            if words and all(w in hay for w in words) and item.get("mediaId") and item.get("url"):
                out.append(
                    {
                        "icon": refs.REF_PREFIX + item["mediaId"],
                        "src": item["url"],
                        "source": source,
                        "title": item.get("title") or "",
                    }
                )
    return out[:limit]


# ------------------------------------------------------------------ loopback server


class ChooserServer:
    """One loopback HTTP server per process, serving every open chooser."""

    def __init__(self) -> None:
        self.choosers: dict[str, Chooser] = {}
        self.pools: dict[str, tuple[list[dict], list[dict]]] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        self._start()
        assert self._httpd is not None
        return self._httpd.server_address[1]

    def url(self, ch: Chooser) -> str:
        return f"http://127.0.0.1:{self.port}/?c={ch.chooser_id}&t={ch.token}"

    def add(self, ch: Chooser, public: list[dict], mine: list[dict]) -> None:
        self.choosers[ch.chooser_id] = ch
        self.pools[ch.chooser_id] = (public, mine)

    def new_id(self) -> tuple[str, str]:
        return secrets.token_urlsafe(6), secrets.token_urlsafe(18)

    def _start(self) -> None:
        with self._lock:
            if self._httpd:
                return
            server = self

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_a):  # keep stdio clean: stdout is the MCP channel
                    pass

                def _chooser(self) -> Chooser | None:
                    q = parse_qs(urlparse(self.path).query)
                    ch = server.choosers.get((q.get("c") or [""])[0])
                    if ch and secrets.compare_digest((q.get("t") or [""])[0], ch.token):
                        return ch
                    return None

                def _send(self, status: int, body: bytes, ctype: str) -> None:
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self):  # noqa: N802
                    ch = self._chooser()
                    if not ch:
                        return self._send(403, b"This chooser link has expired.", "text/plain")
                    route = urlparse(self.path).path
                    if route == "/":
                        return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                    if route == "/state":
                        return self._send(200, json.dumps(ch.state()).encode(), "application/json")
                    if route == "/search":
                        q = parse_qs(urlparse(self.path).query)
                        public, mine = server.pools.get(ch.chooser_id, ([], []))
                        hits = search_pool(public, mine, (q.get("q") or [""])[0])
                        return self._send(200, json.dumps({"icons": hits}).encode(), "application/json")
                    return self._send(404, b"not found", "text/plain")

                def do_POST(self):  # noqa: N802
                    ch = self._chooser()
                    if not ch:
                        return self._send(403, b"This chooser link has expired.", "text/plain")
                    if urlparse(self.path).path != "/select":
                        return self._send(404, b"not found", "text/plain")
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
                    except json.JSONDecodeError:
                        return self._send(400, b"bad json", "text/plain")
                    ch.record(body.get("selections"), body.get("requests"), bool(body.get("done")))
                    return self._send(
                        200,
                        json.dumps({"saved": len(ch.selections), "done": ch.done}).encode(),
                        "application/json",
                    )

            self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=self._httpd.serve_forever, daemon=True, name="yoto-chooser").start()


def selections_to_ops(selections: list[dict], upload: Any) -> tuple[list[dict], dict[str, str]]:
    """set_icon ops for the picks; ``upload(sha256) -> ref`` uploads generated icons first."""
    uploaded: dict[str, str] = {}
    ops = []
    for sel in selections:
        icon = sel["icon"]
        if not refs.is_ref(icon):
            if icon not in uploaded:
                uploaded[icon] = upload(icon)
            icon = uploaded[icon]
        op: dict[str, Any] = {"op": "set_icon", "chapter": sel["chapter_key"], "icon": icon}
        if sel.get("track_key"):
            op["track"] = sel["track_key"]
        ops.append(op)
    return ops, uploaded
