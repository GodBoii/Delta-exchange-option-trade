import multiprocessing
import time

import pytest

from news_analyzer.worker import run_in_worker


def test_worker_returns_results_and_propagates_failures():
    assert run_in_worker(dict, [("ok", True)], timeout_seconds=10) == {"ok": True}
    with pytest.raises(RuntimeError, match="invalid literal"):
        run_in_worker(int, "invalid", timeout_seconds=10)


def test_worker_deadline_terminates_blocking_work():
    before = {child.pid for child in multiprocessing.active_children()}
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        run_in_worker(time.sleep, 30, timeout_seconds=0.3)
    assert time.monotonic() - started < 5
    assert {child.pid for child in multiprocessing.active_children()} == before


@pytest.mark.asyncio
async def test_recheck_endpoint_assigns_a_five_minute_worker_deadline(monkeypatch):
    from types import SimpleNamespace

    from news_analyzer import main

    received = {}

    def worker(function, *args, **kwargs):
        received.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(main, "run_in_worker", worker)
    monkeypatch.setattr(main, "settings", SimpleNamespace(openrouter_api_key="test", supabase_db_url="test"))
    request = SimpleNamespace(state=SimpleNamespace(trace_id="test"))
    body = SimpleNamespace(trigger="activation_recheck")
    assert await main.analyze_automation(body, request) == {"success": True}
    assert received["timeout_seconds"] == 300
