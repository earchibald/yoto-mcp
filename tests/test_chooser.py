from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request

import pytest

from yoto_mcp import icons, server


@pytest.fixture
def srv(session, monkeypatch):
    monkeypatch.setattr(server, "_session", session)
    monkeypatch.setattr(server, "_choosers", server.chooser.ChooserServer())
    return server


def moon_spec() -> dict:
    rows = ["." * 16] * 3 + ["....YYYYYY......"] * 10 + ["." * 16] * 3
    return {"name": "moon", "rows": rows, "palette": {"Y": "#ffdd33"}}


def test_rows_offer_generated_and_matching_icons(srv, hilo):
    drawn = icons.render(moon_spec())
    first = hilo["content"]["chapters"][0]["key"]
    ch = srv._open_chooser("Hilo Books 4-8", {first: [drawn.sha256]}, False, "Eugene")
    assert len(ch.rows) == len(hilo["content"]["chapters"])
    sources = [c["source"] for c in ch.rows[0]["candidates"]]
    assert sources[0] == "generated" and ch.rows[0]["candidates"][0]["src"].startswith(
        "data:image/png;base64,"
    )


def _http(url: str, body: dict | None = None) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if r.headers.get_content_type() == "application/json" else raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_browser_chooser_round_trip_becomes_a_plan(srv, monkeypatch, hilo):
    drawn = icons.render(moon_spec())
    first = hilo["content"]["chapters"][0]["key"]
    second = hilo["content"]["chapters"][1]["key"]

    def user_in_browser(url: str) -> bool:
        def act():
            status, page = _http(url)
            assert status == 200 and "Yoto icon chooser" in page
            base, query = url.split("?", 1)
            assert _http(base + "state?c=bad&t=bad")[0] == 403
            status, state = _http(base + "state?" + query)
            assert status == 200 and state["card"] == "Hilo Books 4-8"
            _http(
                base + "select?" + query,
                {
                    "selections": [
                        {"chapter_key": first, "icon": drawn.sha256},
                        {"chapter_key": second, "icon": "yoto:#pubMoon"},
                    ],
                    "requests": [{"chapter_key": first, "note": "a friendlier moon"}],
                    "done": True,
                },
            )

        threading.Thread(target=act).start()
        return True

    monkeypatch.setattr("webbrowser.open", user_in_browser)
    out = asyncio.run(
        srv.yoto_icon_chooser(
            "Hilo Books 4-8", {first: [drawn.sha256]}, account="Eugene", mode="browser", wait_minutes=0.2
        )
    )
    assert out["done"] and out["mode"] == "browser"
    assert out["requests"] == [{"chapter_key": first, "note": "a friendlier moon"}]
    assert drawn.sha256 in out["uploaded_icons"]
    plan = out["plan"]
    assert plan["can_apply"], plan["errors"]
    changed = {c["key"] for c in plan["changes"]["changed"]}
    assert changed == {first, second}


def test_inline_mode_returns_state_and_select_saves(srv, hilo):
    out = asyncio.run(srv.yoto_icon_chooser("Hilo Books 4-8", account="Eugene", mode="inline"))
    assert out["mode"] == "inline" and out["rows"]
    key = hilo["content"]["chapters"][2]["key"]
    srv.yoto_icon_select(out["chooser_id"], [{"chapter_key": key, "icon": "yoto:#pubStar"}], done=True)
    result = srv.yoto_icon_chooser_result(out["chooser_id"])
    assert result["selections"] == [{"chapter_key": key, "icon": "yoto:#pubStar"}]
    assert result["plan"]["can_apply"]


def test_chooser_page_is_an_mcp_app_resource(srv):
    tool = {t.name: t for t in asyncio.run(srv.mcp.list_tools())}["yoto_icon_chooser"]
    assert tool.meta["ui"]["resourceUri"] == "ui://yoto/icon-chooser"
    res = asyncio.run(srv.mcp.read_resource("ui://yoto/icon-chooser"))
    content = list(res)[0]
    assert "text/html" in content.mime_type and "ui/initialize" in content.content
