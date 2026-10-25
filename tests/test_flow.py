import asyncio
from dataclasses import replace
import json
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from app.demo import DemoSite, demo_config, run_demo
from app.main import run_browser
from app.notify import NotificationManager
from app.state import StateStore, now


def test_full_demo():
    result = asyncio.run(run_demo())
    assert result['status'] == 'COMPLETED'
    assert result['submissions'] == 1
    assert result['real_qq_messages'] == 0


def test_read_only_status_and_submission_disabled(tmp_path):
    async def scenario():
        site = DemoSite()
        cfg = demo_config(tmp_path)
        assert (await run_browser(cfg, 'login', setup_context=site.setup)).status == 'AUTHENTICATED'
        assert (await run_browser(cfg, 'status', setup_context=site.setup)).status == 'PENDING'
        assert site.submissions == 0
        with pytest.raises(ValueError, match='submission_not_enabled'):
            await run_browser(replace(cfg, checkin_enabled=False), 'run', setup_context=site.setup)
        assert site.submissions == 0
    asyncio.run(scenario())


def test_unconfirmed_submission_not_repeated_after_restart(tmp_path):
    async def scenario():
        site = DemoSite()
        site.lose_response = True
        cfg = demo_config(tmp_path)
        first = await run_browser(cfg, 'run', setup_context=site.setup)
        assert first.status == 'UNKNOWN'
        assert site.submissions == 1
        assert StateStore(cfg.data_dir).day(now().date().isoformat())['status'] == 'UNKNOWN'
        second = await run_browser(cfg, 'run', setup_context=site.setup)
        assert second.reason == 'submission_blocked_check_record'
        assert site.submissions == 1
    asyncio.run(scenario())


def test_notifications_retry_without_browser(tmp_path, monkeypatch):
    from app.auth import Result
    cfg = replace(demo_config(tmp_path), napcat_url='http://127.0.0.1:1', qq_user_id='10000')
    store = StateStore(cfg.data_dir)
    result = Result('COMPLETED', 'record_verified')
    date = now().date().isoformat()
    store.record(date, 'COMPLETED', 'record_verified', '21:05:00')
    store.enqueue(date, result)
    notifier = NotificationManager(cfg)
    send = Mock(side_effect=URLError('do not log credentials'))
    monkeypatch.setattr(notifier, 'send', send)
    assert asyncio.run(notifier.flush(store)) == 'pending'
    # A new store instance models a container restart.
    store = StateStore(cfg.data_dir)
    assert store.day(date)['status'] == 'COMPLETED'
    send.side_effect = None
    assert asyncio.run(notifier.flush(store)) == 'sent'
    assert asyncio.run(notifier.flush(store)) == 'sent'
    assert send.call_count == 2
    assert 'do not log' not in store.path.read_text()
    assert json.loads(store.path.read_text())['version'] == 1


def test_unknown_or_missing_timestamp_never_submits(tmp_path):
    async def scenario():
        site = DemoSite()
        cfg = replace(demo_config(tmp_path), pending_selector='#does-not-exist', page_timeout=0.3)
        result = await run_browser(cfg, 'run', setup_context=site.setup)
        assert result.status == 'UNKNOWN' and site.submissions == 0
        site.completed = True
        cfg = replace(cfg, time_selector='#missing')
        result = await run_browser(cfg, 'run', setup_context=site.setup)
        assert result.reason == 'completion_time_missing'
    asyncio.run(scenario())


def test_corrupt_json_fails_closed(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text('{invalid')
    with pytest.raises(ValueError):
        StateStore(tmp_path)
    assert path.read_text() == '{invalid'


def test_onebot_http_200_rejection_stays_pending(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from app.auth import Result
    import threading

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"failed","retcode":1200,"data":null}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = replace(demo_config(tmp_path), napcat_url=f'http://127.0.0.1:{server.server_port}',
                      qq_user_id='10000')
        store = StateStore(cfg.data_dir)
        store.enqueue('2026-10-21', Result('COMPLETED', 'record_verified'))
        assert asyncio.run(NotificationManager(cfg).flush(store)) == 'pending'
        assert not next(iter(store.data['notifications'].values()))['sent']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_daily_scheduler_survives_restart(tmp_path, monkeypatch):
    from datetime import datetime
    from app import scheduler
    from app.state import TZ

    cfg = demo_config(tmp_path)
    monkeypatch.setattr(scheduler, 'now', lambda: datetime(2026, 10, 21, 21, 5, tzinfo=TZ))
    commands = []
    monkeypatch.setattr(scheduler.subprocess, 'run', lambda command, **kwargs: commands.append(command))

    def stop(_):
        raise InterruptedError('stop test loop')

    monkeypatch.setattr(scheduler.time, 'sleep', stop)
    for _ in range(2):
        with pytest.raises(InterruptedError):
            scheduler.serve(cfg)
    assert sum(command[-1] == 'run' for command in commands) == 1
    assert StateStore(cfg.data_dir).data['scheduled_date'] == '2026-10-21'


def test_diagnostics_do_not_expose_browser_exception_details():
    from playwright.async_api import Error, TimeoutError as BrowserTimeout
    from app.main import failure_result
    secret = 'password=private-value&ticket=private-ticket'
    result = failure_result(Error('net::ERR_NAME_NOT_RESOLVED ' + secret), 'check')
    assert result.reason == 'err_name_not_resolved'
    assert 'private' not in repr(result)
    assert failure_result(BrowserTimeout(secret), 'manual-login').reason == 'manual_timeout'
    assert failure_result(TimeoutError(secret), 'run').reason == 'task_or_page_timeout'
