"""Interactive cards as story graphs: validate, compile to a card, simulate, decompile.

Claude writes a small graph instead of raw Yoto events. A node becomes one chapter, in graph
order (chapter order is dial order). Each node names where to go next:

    {"id": "choose-book4", "type": "choice", "title": "Book 4?",
     "tracks": [{"title": "Book 4?", "audio": "yoto:#…" | {"ref": …, "duration": …} | "<asset name>",
                 "icon": "yoto:#…"}],
     "left": "announce-book4", "right": "choose-book5"}

* ``next``: where the node goes when its last track ends (a node id, or ``"stop"``).
* ``left`` / ``right``: where a press of that button goes, on any of the node's tracks.
  ``prev`` and ``skip`` are accepted as aliases for segments.
* ``type``: ``choice`` (plays, then waits for a button), ``segment`` (plays on to ``next``)
  or ``end`` (plays, then stops).

The compiler emits ``onEnd`` / ``onLhb`` / ``onRhb`` events with globally unique chapter keys,
sets the interactive config, and fills in every asset. The simulator walks the result the way
the player does: it follows ``onEnd`` and presses each button, so a goto to nowhere, a dead
button or an endless loop is caught before anything is written.
"""

from __future__ import annotations

import copy
import re
from collections import deque
from typing import Any

from . import refs
from .cards import chapters, title_of

NODE_TYPES = ("choice", "segment", "end")
HANDLES = ("next", "left", "right")
ALIASES = {"prev": "left", "skip": "right", "ending": "end"}
STOP = "stop"
EVENT_FOR = {"next": "onEnd", "left": "onLhb", "right": "onRhb"}
PRESS_FOR = {"onEnd": "end", "onLhb": "left", "onRhb": "right"}
DEFAULT_CONFIG = {
    "autoadvance": "none",
    "disableTrackNav": False,
    "disableChapterNav": False,
    # Forget the play position after a second, so the card starts over each time. 1 is what
    # works on a real player; 0 is undocumented.
    "resumeTimeout": 1,
}
_SLUG = re.compile(r"[^a-z0-9]+")

GRAPH_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Yoto story graph",
    "type": "object",
    "required": ["nodes"],
    "properties": {
        "start": {"type": "string", "description": "Start node id (default: the first node)."},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "tracks"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"enum": list(NODE_TYPES), "default": "segment"},
                    "title": {"type": "string"},
                    "icon": {"type": "string", "description": "yoto:# ref used for tracks without one"},
                    "tracks": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["audio"],
                            "properties": {
                                "key": {"type": "string"},
                                "title": {"type": "string"},
                                "audio": {
                                    "description": "a yoto:# ref, media from yoto_media_upload_wait / "
                                    "yoto_tts, or the name of an entry in `assets`",
                                },
                                "icon": {"type": "string"},
                            },
                        },
                    },
                    "next": {"type": "string", "description": "node id or 'stop'"},
                    "left": {"type": "string"},
                    "right": {"type": "string"},
                },
            },
        },
        "assets": {"type": "object", "description": "name → media, referenced by tracks' `audio`"},
        "expect": {
            "type": "array",
            "description": "Declared walks: {presses: ['left', 'end', …], plays: [node ids]}; "
            "compile fails if the simulator disagrees.",
        },
    },
}


class GraphError(ValueError):
    pass


# ------------------------------------------------------------------ normalizing


def normalize(graph: dict) -> dict:
    """A copy with aliases resolved, types defaulted and `start` set."""
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise GraphError("A story graph is an object with a `nodes` list.")
    g = copy.deepcopy(graph)
    for node in g["nodes"]:
        if not isinstance(node, dict):
            raise GraphError("Every node must be an object.")
        for alias, real in ALIASES.items():
            if alias in node and alias != "ending":
                node.setdefault(real, node.pop(alias))
        node["type"] = ALIASES.get(node.get("type") or "segment", node.get("type") or "segment")
        node.setdefault("title", node.get("id"))
        node["tracks"] = node.get("tracks") or []
    if g["nodes"]:
        g.setdefault("start", g["nodes"][0].get("id"))
    return g


def slugify(node_id: str) -> str:
    return _SLUG.sub("-", str(node_id).lower()).strip("-") or "node"


def chapter_keys(nodes: list[dict]) -> dict[str, str]:
    """Node id → globally unique chapter key (a probe of the used set, so ids like
    ``a``, ``a!`` and ``a-2`` still get three different keys)."""
    used: set[str] = set()
    out: dict[str, str] = {}
    for node in nodes:
        base = slugify(node["id"])
        key, n = base, 2
        while key in used:
            key, n = f"{base}-{n}", n + 1
        used.add(key)
        out[node["id"]] = key
    return out


def track_keys(node: dict, ckey: str) -> list[str]:
    tracks = node["tracks"]
    keys: list[str] = []
    for i, t in enumerate(tracks, start=1):
        k = t.get("key") or (ckey if len(tracks) == 1 else f"{ckey}-{i:02d}")
        while k in keys:
            k += "-x"
        keys.append(k)
    return keys


# ------------------------------------------------------------------ validation


def _edges(node: dict) -> dict[str, str]:
    return {h: node[h] for h in HANDLES if node.get(h)}


def validate(graph: dict) -> dict:
    """Errors (block compiling) and warnings for a graph. Works on raw or normalized graphs."""
    try:
        g = normalize(graph)
    except GraphError as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": []}
    errors: list[str] = []
    warns: list[str] = []
    nodes = g["nodes"]
    if not nodes:
        errors.append("The graph has no nodes.")
        return {"valid": False, "errors": errors, "warnings": warns}
    ids = [n.get("id") for n in nodes]
    if any(not i for i in ids):
        errors.append("Every node needs an id.")
    for dup in sorted({i for i in ids if i and ids.count(i) > 1}):
        errors.append(f"Node id {dup!r} is used more than once.")
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    if g.get("start") not in by_id:
        errors.append(f"Start node {g.get('start')!r} doesn't exist.")
    assets = g.get("assets") or {}

    for n in nodes:
        nid = n.get("id")
        if n["type"] not in NODE_TYPES:
            errors.append(f"{nid}: unknown type {n['type']!r} (use choice, segment or end).")
        if not n["tracks"]:
            errors.append(f"{nid}: has no tracks.")
        for i, t in enumerate(n["tracks"], start=1):
            audio = t.get("audio") if isinstance(t, dict) else None
            if audio is None:
                errors.append(f"{nid}: track {i} has no audio.")
            elif isinstance(audio, str) and not (refs.is_ref(audio) or audio.startswith("https://")):
                if audio not in assets:
                    errors.append(f"{nid}: track {i} audio {audio!r} is neither a yoto:# ref nor an asset.")
        for handle, target in _edges(n).items():
            if handle == "next" and target == STOP:
                continue
            if target not in by_id:
                errors.append(f"{nid}: {handle} goes to missing node {target!r}.")
        if n["type"] == "choice":
            for h in ("left", "right"):
                if not n.get(h):
                    errors.append(f"{nid}: a choice needs both left and right (missing {h}).")
            if n.get("next") and n["next"] != STOP:
                warns.append(f"{nid}: a choice with `next` won't wait for a button.")
        elif n["type"] == "segment":
            if not n.get("next"):
                errors.append(f"{nid}: a segment needs `next` (a node id, or 'stop').")
        elif n["type"] == "end" and n.get("next") not in (None, STOP):
            errors.append(f"{nid}: an end node can't have `next`.")

    if errors:
        return {"valid": False, "errors": errors, "warnings": warns}

    # Reachability over every edge the player can take.
    seen: set[str] = set()
    queue = deque([g["start"]])
    while queue:
        cur = queue.popleft()
        if cur in seen:
            continue
        seen.add(cur)
        for target in _edges(by_id[cur]).values():
            if target != STOP:
                queue.append(target)
    for n in nodes:
        if n["id"] not in seen:
            errors.append(f"{n['id']}: can't be reached from the start node {g['start']!r}.")

    # A loop made only of `next` edges plays forever with nobody choosing.
    for n in nodes:
        path, cur = [], n["id"]
        while cur not in path:
            path.append(cur)
            nxt = by_id[cur].get("next")
            if by_id[cur]["type"] != "segment" or not nxt or nxt == STOP:
                break
            cur = nxt
        else:
            loop = path[path.index(cur) :]
            if n["id"] == min(loop):
                errors.append("Endless loop with no choice in it: " + " → ".join(loop + [cur]) + ".")

    if not any(n["type"] in ("choice", "end") or n.get("next") == STOP for n in nodes):
        warns.append("No node ever stops or waits; the card only ends when the child turns it off.")
    return {"valid": not errors, "errors": errors, "warnings": warns}


# ------------------------------------------------------------------ compiling


def _media(audio: Any, assets: dict) -> dict:
    if isinstance(audio, str) and audio in assets:
        audio = assets[audio]
    if isinstance(audio, str):
        audio = {"ref": audio}
    if not isinstance(audio, dict) or not (audio.get("ref") or audio.get("trackUrl")):
        raise GraphError(f"Unusable audio {audio!r}: give a yoto:# ref or uploaded media.")
    return audio


def compile_graph(
    graph: dict,
    title: str,
    *,
    config: dict | None = None,
    metadata: dict | None = None,
    cover: str | None = None,
) -> dict:
    """The card document for ``graph`` (raises GraphError when it isn't valid)."""
    report = validate(graph)
    if not report["valid"]:
        raise GraphError("The story graph isn't valid:\n- " + "\n- ".join(report["errors"]))
    g = normalize(graph)
    assets = g.get("assets") or {}
    nodes = g["nodes"]
    # The start node plays first, so it must be the first chapter.
    start = next(n for n in nodes if n["id"] == g["start"])
    nodes = [start] + [n for n in nodes if n is not start]
    ckeys = chapter_keys(nodes)
    tkeys = {n["id"]: track_keys(n, ckeys[n["id"]]) for n in nodes}

    def goto(target: str) -> dict:
        return {"cmd": "goto", "params": {"chapterKey": ckeys[target], "trackKey": tkeys[target][0]}}

    out_chapters = []
    all_hosted = True
    for n in nodes:
        ckey = ckeys[n["id"]]
        tracks = []
        for i, (t, tkey) in enumerate(zip(n["tracks"], tkeys[n["id"]], strict=True)):
            media = _media(t["audio"], assets)
            ref = media.get("ref") or media.get("trackUrl")
            all_hosted &= refs.is_ref(ref)
            track: dict[str, Any] = {
                "key": tkey,
                "title": t.get("title") or n["title"],
                "trackUrl": ref,
                "type": "audio" if refs.is_ref(ref) else "stream",
                "format": media.get("format") or "mp3",
            }
            for src, dst in (("duration", "duration"), ("fileSize", "fileSize"), ("channels", "channels")):
                if media.get(src) is not None:
                    track[dst] = media[src]
            icon = t.get("icon") or n.get("icon")
            if icon:
                track["display"] = {"icon16x16": icon}
            events: dict[str, Any] = {}
            last = i == len(n["tracks"]) - 1
            if not last:
                events["onEnd"] = {
                    "cmd": "goto",
                    "params": {"chapterKey": ckey, "trackKey": tkeys[n["id"]][i + 1]},
                }
            elif n["type"] == "segment" and n.get("next") and n["next"] != STOP:
                events["onEnd"] = goto(n["next"])
            else:
                events["onEnd"] = {"cmd": "stop"}
            for h in ("left", "right"):
                if n.get(h):
                    events[EVENT_FOR[h]] = goto(n[h])
            track["events"] = events
            tracks.append(track)
        chapter: dict[str, Any] = {"key": ckey, "title": n["title"], "tracks": tracks}
        icon = n.get("icon") or (tracks[0].get("display") or {}).get("icon16x16")
        if icon:
            chapter["display"] = {"icon16x16": icon}
        ds = [t.get("duration") for t in tracks]
        if all(d is not None for d in ds):
            chapter["duration"] = sum(ds)
        out_chapters.append(chapter)

    for num, c in enumerate(out_chapters, start=1):
        c["overlayLabel"] = str(num)
        for t in c["tracks"]:
            t["overlayLabel"] = str(num)
    cfg = dict(DEFAULT_CONFIG)
    if all_hosted:
        cfg["onlineOnly"] = False
    cfg.update(config or {})
    meta: dict[str, Any] = {"media": {}}
    meta.update(copy.deepcopy(metadata or {}))
    if cover:
        meta.setdefault("cover", {})["imageL"] = cover
    card = {
        "title": title,
        "content": {"playbackType": "interactive", "config": cfg, "chapters": out_chapters},
        "metadata": meta,
    }
    durations = [c.get("duration") for c in out_chapters]
    if all(d is not None for d in durations):
        meta["media"]["duration"] = sum(durations)
    sizes = [t.get("fileSize") for c in out_chapters for t in c["tracks"]]
    if all(s is not None for s in sizes):
        meta["media"]["fileSize"] = sum(sizes)
    return card


# ------------------------------------------------------------------ simulating


class _Player:
    def __init__(self, card: dict):
        self.chapters = chapters(card)
        self.by_key = {c.get("key"): c for c in self.chapters}
        self.problems: list[str] = []

    def first(self) -> tuple[str, str] | None:
        if not self.chapters or not self.chapters[0].get("tracks"):
            return None
        c = self.chapters[0]
        return c["key"], c["tracks"][0]["key"]

    def track(self, pos: tuple[str, str]) -> dict | None:
        c = self.by_key.get(pos[0])
        if not c:
            return None
        return next((t for t in c.get("tracks") or [] if t.get("key") == pos[1]), None)

    def step(self, pos: tuple[str, str], press: str) -> tuple[str, tuple[str, str] | None]:
        """What the player does on ``press`` at ``pos``: ('goto', pos) | ('stop', None) | ('none', None)."""
        event = {"end": "onEnd", "left": "onLhb", "right": "onRhb"}[press]
        t = self.track(pos)
        cmd = (t or {}).get("events", {}).get(event)
        if cmd is None:
            if press == "end":
                return self._native_next(pos)
            return "none", None
        if cmd.get("cmd") == "stop":
            return "stop", None
        if cmd.get("cmd") == "repeat":
            return "goto", pos
        if cmd.get("cmd") == "goto":
            p = cmd.get("params") or {}
            ckey, tkey = str(p.get("chapterKey")), p.get("trackKey")
            c = self.by_key.get(ckey)
            if c is None:
                self.problems.append(f"{pos[0]}.{event} goes to missing chapter {ckey!r}.")
                return "broken", None
            target = (ckey, str(tkey) if tkey is not None else c["tracks"][0]["key"])
            if not self.track(target):
                self.problems.append(f"{pos[0]}.{event} goes to missing track {target[1]!r}.")
                return "broken", None
            return "goto", target
        self.problems.append(f"{pos[0]}.{event} has unknown cmd {cmd.get('cmd')!r}.")
        return "broken", None

    def _native_next(self, pos: tuple[str, str]) -> tuple[str, tuple[str, str] | None]:
        # With no onEnd, an interactive card (autoadvance none) stops; a linear one plays on.
        c = self.by_key[pos[0]]
        keys = [t["key"] for t in c["tracks"]]
        i = keys.index(pos[1])
        if i + 1 < len(keys):
            return "goto", (pos[0], keys[i + 1])
        ci = self.chapters.index(c)
        if ci + 1 < len(self.chapters):
            nxt = self.chapters[ci + 1]
            return "goto", (nxt["key"], nxt["tracks"][0]["key"])
        return "stop", None


def replay(card: dict, presses: list[str], max_steps: int = 2000) -> dict:
    """Play the card from the start, pressing buttons in order. ``"end"`` lets the track finish.

    After the presses run out, the card keeps playing (as if nobody touched it) until it stops
    or waits; the trace lists every track played.
    """
    player = _Player(card)
    pos = player.first()
    if pos is None:
        return {"plays": [], "ended": "empty card", "problems": ["The card has no tracks."]}
    trace = [{"chapter": pos[0], "track": pos[1], "title": (player.track(pos) or {}).get("title")}]
    todo = list(presses)
    steps = 0
    ended = None
    while steps < max_steps:
        steps += 1
        press = todo.pop(0) if todo else "end"
        action, nxt = player.step(pos, press)
        if action == "none":
            continue  # nothing bound to that button: the track plays on
        if action == "stop" and todo and _waits(player.track(pos) or {}):
            continue  # the prompt finished; the player waits for the next press
        if action in ("stop", "broken"):
            if todo:
                ended = f"stopped at {pos[0]} (presses left over: {todo})"
            else:
                ended = f"{'stopped' if action == 'stop' else 'broken'} at {pos[0]}"
            break
        assert nxt is not None
        pos = nxt
        trace.append({"chapter": pos[0], "track": pos[1], "title": (player.track(pos) or {}).get("title")})
        t = player.track(pos) or {}
        if not todo and _waits(t):
            ended = f"stopped at {pos[0]}; its buttons still work"
            break
    else:
        ended = f"still playing after {max_steps} tracks (an endless loop)"
        player.problems.append(ended)
    return {"plays": trace, "ended": ended, "problems": player.problems}


def _waits(track: dict) -> bool:
    ev = track.get("events") or {}
    return (ev.get("onEnd") or {}).get("cmd") == "stop" and bool(ev.get("onLhb") or ev.get("onRhb"))


def walk(card: dict, expect: list[dict] | None = None, key_of: dict[str, str] | None = None) -> dict:
    """Check every chapter the way check_payload did for the Hilo card.

    * every goto lands on a real chapter and track,
    * every chapter can be reached from the start,
    * from each chapter, letting it play ends in a stop, a wait for a button, or a loop that a
      button can leave,
    * declared walks (``expect``: ``{presses, plays}``, ``plays`` in node ids or chapter keys)
      match what the player does.
    """
    player = _Player(card)
    problems: list[str] = []
    notes: list[str] = []
    chs = player.chapters
    if not chs:
        return {"ok": False, "problems": ["The card has no chapters."], "notes": [], "expect": []}
    for c in chs:
        for t in c.get("tracks") or []:
            for press in ("end", "left", "right"):
                player.step((c["key"], t["key"]), press)
    problems += sorted(set(player.problems))
    player.problems = []

    # Reachability: every transition the player can make.
    start = player.first()
    seen: set[str] = set()
    queue = deque([start] if start else [])
    visited: set[tuple[str, str]] = set()
    while queue:
        pos = queue.popleft()
        if pos in visited:
            continue
        visited.add(pos)
        seen.add(pos[0])
        for press in ("end", "left", "right"):
            action, nxt = player.step(pos, press)
            if action == "goto" and nxt:
                queue.append(nxt)
    unreachable = [c["key"] for c in chs if c["key"] not in seen]
    if unreachable:
        problems.append(f"Unreachable chapters: {', '.join(unreachable)}.")

    # Letting each chapter play on: does it settle?
    for c in chs:
        pos: tuple[str, str] = (c["key"], c["tracks"][0]["key"])
        path: list[tuple[str, str]] = []
        while pos not in path and len(path) < 5000:
            path.append(pos)
            action, nxt = player.step(pos, "end")
            if action != "goto" or nxt is None:
                break
            pos = nxt
        else:
            loop = path[path.index(pos) :] if pos in path else path
            escapable = any(
                player.step(p, b)[0] == "goto" and player.step(p, b)[1] not in loop
                for p in loop
                for b in ("left", "right")
            )
            if not escapable:
                problems.append(f"{c['key']}: plays forever in a loop no button leaves.")
            elif c["key"] == min(p[0] for p in loop):
                notes.append(f"Loop through {', '.join(sorted({p[0] for p in loop}))} (a button leaves it).")
    for c in chs:
        last = c["tracks"][-1]
        ev = last.get("events") or {}
        if (ev.get("onEnd") or {}).get("cmd") == "stop" and not (ev.get("onLhb") or ev.get("onRhb")):
            notes.append(f"{c['key']} ends the card.")

    checked = []
    key_of = key_of or {}
    for exp in expect or []:
        got = replay(card, list(exp.get("presses") or []))
        want = [key_of.get(x, x) for x in exp.get("plays") or []]
        plays = [p["chapter"] for p in got["plays"]]
        ok = plays == want if want else True
        if exp.get("ends") and exp["ends"] not in (got["ended"] or ""):
            ok = False
        checked.append({"presses": exp.get("presses"), "ok": ok, "plays": plays, "ended": got["ended"]})
        if not ok:
            problems.append(f"Walk {exp.get('presses')} played {plays} ({got['ended']}), expected {want}.")
    return {"ok": not problems, "problems": problems, "notes": notes, "expect": checked}


# ------------------------------------------------------------------ decompiling


def from_card(card: dict) -> dict:
    """An interactive card back to a graph (one node per chapter), for editing and recompiling."""
    chs = chapters(card)
    by_key = {c.get("key"): c for c in chs}
    reached_by_end: set[str] = set()
    for c in chs:
        ev = (c.get("tracks") or [{}])[-1].get("events") or {}
        p = (ev.get("onEnd") or {}).get("params") or {}
        if (ev.get("onEnd") or {}).get("cmd") == "goto" and p.get("chapterKey") != c.get("key"):
            reached_by_end.add(str(p.get("chapterKey")))
    nodes = []
    for i, c in enumerate(chs):
        tracks = c.get("tracks") or []
        last_ev = (tracks[-1].get("events") if tracks else None) or {}
        node: dict[str, Any] = {"id": c["key"], "title": c.get("title") or c["key"]}
        node_tracks = []
        for t in tracks:
            media = {k: t[k] for k in ("duration", "fileSize", "channels", "format") if t.get(k) is not None}
            media["ref"] = refs.to_ref(t.get("trackUrl"))
            nt: dict[str, Any] = {"key": t.get("key"), "title": t.get("title"), "audio": media}
            icon = (t.get("display") or {}).get("icon16x16")
            if icon:
                nt["icon"] = refs.to_ref(icon)
            node_tracks.append(nt)
        node["tracks"] = node_tracks
        if (c.get("display") or {}).get("icon16x16"):
            node["icon"] = refs.to_ref(c["display"]["icon16x16"])
        on_end = last_ev.get("onEnd")
        for event, handle in (("onLhb", "left"), ("onRhb", "right")):
            cmd = last_ev.get(event)
            if cmd and cmd.get("cmd") == "goto":
                node[handle] = (cmd.get("params") or {}).get("chapterKey")
        if on_end and on_end.get("cmd") == "goto":
            node["type"] = "segment"
            node["next"] = (on_end.get("params") or {}).get("chapterKey")
        elif on_end is None and card.get("content", {}).get("playbackType") != "interactive":
            node["type"] = "segment"
            node["next"] = chs[i + 1]["key"] if i + 1 < len(chs) else STOP
        elif node.get("left") and node.get("right") and c["key"] not in reached_by_end:
            node["type"] = "choice"
        elif node.get("left") or node.get("right"):
            node["type"] = "segment"
            node["next"] = STOP
        else:
            node["type"] = "end"
        nodes.append(node)
    del by_key
    cfg = (card.get("content") or {}).get("config") or {}
    return {
        "title": title_of(card),
        "start": chs[0]["key"] if chs else None,
        "nodes": nodes,
        "config": {k: cfg[k] for k in ("resumeTimeout", "disableTrackNav", "disableChapterNav") if k in cfg},
    }


def mermaid(card: dict) -> str:
    """A Mermaid flowchart of a card's events."""
    lines = ["flowchart TD"]
    for c in chapters(card):
        label = (c.get("title") or c["key"]).replace('"', "'")
        lines.append(f'  {_mid(c["key"])}["{label}"]')
    for c in chapters(card):
        ev = (c.get("tracks") or [{}])[-1].get("events") or {}
        for event, cmd in ev.items():
            if (cmd or {}).get("cmd") == "goto":
                target = (cmd.get("params") or {}).get("chapterKey")
                if target and target != c["key"]:
                    lines.append(f"  {_mid(c['key'])} -->|{PRESS_FOR.get(event, event)}| {_mid(target)}")
            elif (cmd or {}).get("cmd") == "stop" and event == "onEnd":
                lines.append(f"  {_mid(c['key'])} -.-> stop((stop))")
    return "\n".join(lines)


def _mid(key: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", key)
