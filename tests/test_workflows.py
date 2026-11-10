from __future__ import annotations

import pytest

from yoto_mcp import cards, tts, workflows


@pytest.fixture(autouse=True)
def fake_tts(monkeypatch):
    """Speech becomes bytes named after the text; the 'transcript' is the spoken text itself."""
    spoken = []

    def synth(text, out, model, voice):
        spoken.append(text)
        out.write_bytes(b"ID3" + f"{model}/{voice}/{text}".encode())

    monkeypatch.setattr(tts, "synthesize", synth)
    monkeypatch.setattr(tts, "transcribe", lambda path: path.read_bytes().decode().rsplit("/", 1)[-1])
    return spoken


def test_spoken_text_uses_the_pronunciation_map():
    assert (
        tts.spoken_text("Hilo Book 7: Gina—The Girl", {"Hilo": "High-low"})
        == "High-low Book 7: Gina, The Girl"
    )
    assert tts.similarity("High-low book 4", "Hi, low book four") > 0.75


def test_prompt_media_caches_and_uploads(session, fake_tts):
    a = workflows.prompt_media(session, "Hilo Book 4.", "Eugene")
    b = workflows.prompt_media(session, "Hilo Book 4.", "Eugene")
    assert a["ref"] == b["ref"] and a["ref"].startswith("yoto:#")
    assert fake_tts == ["High-low Book 4."]  # the second call reused the cached clip
    assert b["tts"]["cached"] and b["tts"]["match"] == 1.0


def test_book_selector_from_a_family_card_needs_its_owner(session):
    with pytest.raises(PermissionError, match="account='Eugene'"):
        workflows.book_selector(session, "Hilo Books 4-8", account="Family")


def test_book_selector_plans_applies_and_verifies(session):
    out = workflows.book_selector(session, "Hilo Books 4-8", {"4": "Waking the Monsters"}, account="Eugene")
    assert out["compiled"] and out["simulator"]["ok"], out
    assert out["can_apply"], out["errors"]
    assert out["card"] == "(new) Hilo Books 4-8 (Choose a Book)"
    assert out["books"] == {"4": 3, "5": 3}
    assert out["tts"]["clips"] == 4
    applied = session.apply(out["plan_id"], verify_playback=False)
    assert applied["created"] and applied["verified"], applied

    # Running it again rebuilds the same card in place, and changes nothing.
    again = workflows.book_selector(session, "Hilo Books 4-8", {"4": "Waking the Monsters"}, account="Eugene")
    assert again["card"] == "Hilo Books 4-8 (Choose a Book)"
    assert not again["can_apply"] and not again["errors"]
    assert not (again["changes"]["added"] or again["changes"]["changed"] or again["changes"]["removed"])


def test_title_tracks_insert_before_each_book_and_fix_the_intro(session):
    out = workflows.title_tracks(session, "Hilo Books 4-8", {"4": "Waking the Monsters"}, account="Eugene")
    assert out["can_apply"], out["errors"]
    added = [a["key"] for a in out["changes"]["added"]]
    assert added == ["announce-book4", "announce-book5"]
    assert out["book_starts"] == {"4": 2, "5": 6}
    assert out["intro_text"] == "Hilo books 4 through 5. Book 4, track 2. Book 5, track 6."
    assert any(c["title"] == "intro" for c in out["changes"]["changed"])
    applied = session.apply(out["plan_id"], verify_playback=False)
    assert applied["verified"], applied
    _a, _api, _e, doc, _o = session.read("Hilo Books 4-8", "Eugene")
    titles = [c["title"] for c in cards.chapters(doc)]
    assert titles[1] == "Hilo Book 4: Waking the Monsters"
    assert titles[5] == "Hilo Book 5"

    # Idempotent: a second run finds nothing to do.
    again = workflows.title_tracks(session, "Hilo Books 4-8", {"4": "Waking the Monsters"}, account="Eugene")
    assert not again["can_apply"] and not again["changes"]["added"] and not again["changes"]["changed"]


def test_compile_plan_refuses_a_bad_walk(session):
    graph = {
        "nodes": [{"id": "a", "type": "choice", "tracks": [{"audio": "yoto:#x"}], "left": "a", "right": "a"}],
        "expect": [{"presses": ["left"], "plays": ["a", "b"]}],
    }
    out = workflows.compile_plan(session, graph, "Loop", account="Eugene")
    assert out["compiled"] and not out["simulator"]["ok"]
    assert "plan_id" not in out


def test_simulate_a_graph(session):
    graph = {
        "nodes": [
            {
                "id": "start",
                "type": "choice",
                "tracks": [{"audio": "yoto:#s"}],
                "left": "cave",
                "right": "woods",
            },
            {"id": "cave", "type": "end", "tracks": [{"audio": "yoto:#c"}]},
            {"id": "woods", "type": "end", "tracks": [{"audio": "yoto:#w"}]},
        ]
    }
    out = workflows.simulate(session, None, graph, ["right"], None)
    assert [p["node"] for p in out["plays"]] == ["start", "woods"]
    assert out["walk"]["ok"]
