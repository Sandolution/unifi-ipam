from __future__ import annotations

import asyncio

from app import sync
from app.config import load_settings
from app.db import connect


def test_setting_defaults_to_ten_minutes():
    assert load_settings({}).sync_idle_minutes == 10
    assert load_settings({"SYNC_IDLE_MINUTES": "0"}).sync_idle_minutes == 0


def _run_loop(db_path, idle_answers):
    """Run the loop for a few ticks; idle() answers from the list."""
    calls = []
    answers = list(idle_answers)

    async def go():
        stop = asyncio.Event()

        def idle():
            if not answers:
                stop.set()
                return True
            return answers.pop(0)

        await sync.sync_loop(db_path, object(), 0.01, stop, idle)

    original = sync.sync_once
    sync.sync_once = lambda db, adapter: calls.append("sync")
    try:
        asyncio.run(go())
    finally:
        sync.sync_once = original
    return calls


def test_loop_skips_and_marks_paused_while_idle(settings, conn):
    calls = _run_loop(settings.db_path, [False, True, True])
    assert calls == ["sync"]  # one sync while viewed, none while idle
    assert sync.is_paused(settings.db_path)


def test_any_sync_attempt_ends_the_pause(settings, conn):
    sync.mark_paused(settings.db_path)
    assert sync.is_paused(settings.db_path)

    class Down:
        def info(self):
            raise sync.UnifiError("unreachable")

    c = connect(settings.db_path)
    try:
        sync.run_sync(c, Down())
    except sync.UnifiError:
        pass
    assert not sync.is_paused(settings.db_path)


def test_presence_wakes_a_paused_sync(client, settings, monkeypatch):
    def fake_sync(db_path, adapter):  # what any sync attempt does to the pause flag
        calls.append("sync")
        c = connect(db_path)
        with c:
            sync.set_state(c, paused_since=None)
        c.close()

    calls = []
    monkeypatch.setattr("app.main.sync_once", fake_sync)
    c = connect(settings.db_path)
    with c:
        sync.set_state(c, last_success="1791700000")
    c.close()
    assert client.post("/presence").json() == {"resumed": False} and calls == []
    sync.mark_paused(settings.db_path)
    assert "paused while idle" in client.get("/checks").text
    assert client.post("/presence").json() == {"resumed": True} and calls == ["sync"]
    assert "paused while idle" not in client.get("/checks").text
