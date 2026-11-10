from __future__ import annotations

import copy

import pytest

from yoto_mcp import story, templates


def media(name: str) -> dict:
    return {"ref": f"yoto:#{name}", "duration": 10, "fileSize": 100, "channels": "mono", "format": "mp3"}


def selector(hilo: dict) -> dict:
    books = templates.parse_books(hilo)
    prompts = {b: media(f"prompt{b}") for b in books}
    announces = {b: media(f"announce{b}") for b in books}
    names = {b: templates.announce_title("Hilo", b, {4: "Waking the Monsters"}) for b in books}
    return templates.book_selector_graph(books, prompts, announces, names)


def test_parse_books_groups_hilo_tracks(hilo):
    books = templates.parse_books(hilo)
    assert list(books) == [4, 5]
    assert [t["title"] for t in books[4]] == [
        "Book 4 Chapter 1- Whoa",
        "Book 4 Chapter 2- Here to Help",
        "Book 4 Chapter 3- A Lot to Do",
    ]
    assert all(t["audio"]["ref"].startswith("yoto:#") for b in books.values() for t in b)
    assert templates.series_name(hilo) == "Hilo"


def test_book_selector_compiles_and_walks_like_the_hilo_card(hilo):
    graph = selector(hilo)
    assert story.validate(graph)["valid"]
    card = story.compile_graph(graph, "Hilo Books 4-8 (Choose a Book)")
    chs = {c["key"]: c for c in card["content"]["chapters"]}
    first = card["content"]["chapters"][0]
    assert first["key"] == "choose-book4"
    ev = first["tracks"][0]["events"]
    assert ev["onEnd"] == {"cmd": "stop"}
    assert ev["onLhb"]["params"] == {"chapterKey": "announce-book4", "trackKey": "announce-book4"}
    assert ev["onRhb"]["params"]["chapterKey"] == "choose-book5"
    assert chs["choose-book5"]["tracks"][0]["events"]["onRhb"]["params"]["chapterKey"] == "choose-book4"
    # Finishing book 4 flows into book 5; the last track stops; right from it goes back to the start.
    assert chs["book4-03"]["tracks"][0]["events"]["onEnd"]["params"]["chapterKey"] == "book5-01"
    last = chs["book5-03"]["tracks"][0]["events"]
    assert last["onEnd"] == {"cmd": "stop"}
    assert last["onRhb"]["params"]["chapterKey"] == "choose-book4"
    assert chs["book4-01"]["tracks"][0]["events"]["onLhb"]["params"]["chapterKey"] == "choose-book4"
    cfg = card["content"]["config"]
    assert card["content"]["playbackType"] == "interactive"
    assert cfg["resumeTimeout"] == 1 and cfg["onlineOnly"] is False and cfg["autoadvance"] == "none"
    assert [c["overlayLabel"] for c in card["content"]["chapters"]] == [
        str(n) for n in range(1, len(chs) + 1)
    ]
    assert chs["announce-book4"]["title"] == "Hilo Book 4: Waking the Monsters"
    assert chs["announce-book5"]["title"] == "Hilo Book 5"

    report = story.walk(card, graph["expect"])
    assert report["ok"], report["problems"]
    assert all(e["ok"] for e in report["expect"])


def test_replay_waits_after_a_prompt_then_takes_the_press(hilo):
    card = story.compile_graph(selector(hilo), "t")
    run = story.replay(card, ["end", "right", "end", "left"])
    assert [p["chapter"] for p in run["plays"]][:4] == [
        "choose-book4",
        "choose-book5",
        "announce-book5",
        "book5-01",
    ]
    assert run["ended"].startswith("stopped")


def test_walk_catches_a_wrong_declared_trace(hilo):
    graph = selector(hilo)
    graph["expect"] = [{"presses": ["left"], "plays": ["choose-book4", "book4-01"]}]
    card = story.compile_graph(graph, "t")
    report = story.walk(card, graph["expect"])
    assert not report["ok"]
    assert "expected" in report["problems"][0]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda g: g["nodes"][0].update(left="nowhere"), "missing node 'nowhere'"),
        (lambda g: g["nodes"][0].pop("right"), "needs both left and right"),
        (
            lambda g: g["nodes"].append({"id": "orphan", "type": "end", "tracks": [{"audio": "yoto:#x"}]}),
            "can't be reached",
        ),
        (lambda g: g["nodes"][-1].pop("next"), "needs `next`"),
        (lambda g: g["nodes"][0].update(start=None) or g.update(start="nope"), "Start node 'nope'"),
        (lambda g: g["nodes"][1]["tracks"][0].update(audio="not-an-asset"), "neither a yoto:# ref"),
    ],
)
def test_validate_catches_silent_device_failures(hilo, mutate, message):
    graph = selector(hilo)
    mutate(graph)
    report = story.validate(graph)
    assert not report["valid"]
    assert any(message in e for e in report["errors"]), report["errors"]
    with pytest.raises(story.GraphError):
        story.compile_graph(graph, "t")


def test_endless_next_loop_is_an_error():
    graph = {
        "nodes": [
            {"id": "a", "tracks": [{"audio": "yoto:#a"}], "next": "b"},
            {"id": "b", "tracks": [{"audio": "yoto:#b"}], "next": "a"},
        ]
    }
    errors = story.validate(graph)["errors"]
    assert any("Endless loop" in e for e in errors)


def test_chapter_keys_stay_unique():
    nodes = [{"id": "a"}, {"id": "a!"}, {"id": "a-2"}, {"id": "A"}]
    keys = story.chapter_keys(nodes)
    assert len(set(keys.values())) == 4


def test_multi_track_nodes_chain_their_tracks():
    graph = {
        "nodes": [
            {
                "id": "q",
                "type": "choice",
                "tracks": [{"audio": "yoto:#q"}],
                "left": "right-answer",
                "right": "wrong",
            },
            {"id": "wrong", "tracks": [{"audio": "yoto:#w"}], "next": "q"},
            {"id": "right-answer", "tracks": [{"audio": "yoto:#r1"}, {"audio": "yoto:#r2"}], "next": "stop"},
        ],
        "expect": [
            {
                "presses": ["right", "end", "left"],
                "plays": ["q", "wrong", "q", "right-answer", "right-answer"],
            },
        ],
    }
    card = story.compile_graph(graph, "Quiz")
    ra = card["content"]["chapters"][2]
    assert ra["tracks"][0]["events"]["onEnd"]["params"] == {
        "chapterKey": "right-answer",
        "trackKey": "right-answer-02",
    }
    report = story.walk(card, graph["expect"])
    assert report["ok"], report


def test_from_card_round_trips(hilo):
    card = story.compile_graph(selector(hilo), "t")
    graph = story.from_card(card)
    kinds = {n["id"]: n["type"] for n in graph["nodes"]}
    assert kinds["choose-book4"] == "choice"
    assert kinds["book5-03"] == "segment"
    again = story.compile_graph(graph, "t")

    def events(c):
        return [(ch["key"], [t.get("events") for t in ch["tracks"]]) for ch in c["content"]["chapters"]]

    assert events(again) == events(card)


def test_from_linear_card_plays_in_order(hilo):
    graph = story.from_card(copy.deepcopy(hilo))
    assert story.validate(graph)["valid"]
    assert graph["nodes"][0]["next"] == graph["nodes"][1]["id"]
    assert graph["nodes"][-1]["next"] == "stop"


def test_mermaid_lists_buttons(hilo):
    text = story.mermaid(story.compile_graph(selector(hilo), "t"))
    assert "n_choose_book4 -->|left| n_announce_book4" in text
