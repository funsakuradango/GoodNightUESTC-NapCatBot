import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import BrowserContext, Error
from playwright.async_api._context_manager import PlaywrightContextManager

from app.checkin import CheckinManager, CheckinResult
from app.demo import DemoSite, demo_config
from app.main import run_browser
from app.state import StateStore, now
from app.uestc import UestcCheckinManager


@pytest.mark.parametrize('method,stage', [
    ('unroute_all', 'browser_unroute_all'),
    ('close', 'browser_context_close'),
    ('stop', 'playwright_stop'),
])
def test_cleanup_failure_keeps_verified_result(tmp_path, monkeypatch, capsys, method, stage):
    # start() assigns the context manager's __aexit__ to the stop instance attribute.
    cls = PlaywrightContextManager if method == 'stop' else BrowserContext
    attribute = '__aexit__' if method == 'stop' else method
    original = getattr(cls, attribute)

    async def fail_after_cleanup(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise Error('private-ticket=password-secret')

    monkeypatch.setattr(cls, attribute, fail_after_cleanup)

    async def scenario():
        site = DemoSite()
        cfg = demo_config(tmp_path)
        result = await run_browser(cfg, 'run', setup_context=site.setup)
        assert result.status == 'COMPLETED'
        assert site.submissions == 1
        store = StateStore(cfg.data_dir)
        date = now().date().isoformat()
        assert store.day(date)['status'] == 'COMPLETED'
        store.enqueue(date, result)
        assert ':COMPLETED:' in next(iter(store.data['notifications']))

    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert stage in output and 'fail_after_cleanup' in output
    assert 'private-ticket' not in output and 'password-secret' not in output


def test_ticket_cleanup_waits_and_keeps_verified_result(tmp_path, monkeypatch, capsys):
    import app.wechat as wechat
    guard = SimpleNamespace(ticket='private-ticket')
    monkeypatch.setattr(wechat, 'acquire_ticket', AsyncMock(return_value=guard.ticket))
    monkeypatch.setattr(wechat, 'install_ticket', AsyncMock(return_value=guard))
    expected = CheckinResult('COMPLETED', 'today_record_verified', '21:05:00')
    monkeypatch.setattr(CheckinManager, 'run', AsyncMock(return_value=expected))
    context = SimpleNamespace(on=lambda *args: None, remove_listener=lambda *args: None,
                              unroute_all=AsyncMock(side_effect=Error('private-ticket')))
    cfg = replace(demo_config(tmp_path), wechat_ticket_enabled=True)
    manager = UestcCheckinManager(cfg, StateStore(tmp_path))
    result = asyncio.run(manager.run(SimpleNamespace(context=context), submit=True))
    assert result is expected
    assert guard.ticket is None
    context.unroute_all.assert_awaited_once_with(behavior='wait')
    output = capsys.readouterr().out
    assert 'checkin_ticket_unroute' in output and 'private-ticket' not in output


def test_cleanup_does_not_mask_business_error(tmp_path, monkeypatch, capsys):
    from app.auth import AuthManager
    original = BrowserContext.close

    async def fail_close(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise Error('cleanup-secret')

    monkeypatch.setattr(BrowserContext, 'close', fail_close)
    monkeypatch.setattr(AuthManager, 'ensure_authenticated',
                        AsyncMock(side_effect=ValueError('original-business-error')))
    with pytest.raises(ValueError, match='original-business-error'):
        asyncio.run(run_browser(demo_config(tmp_path), 'run'))
    assert 'cleanup-secret' not in capsys.readouterr().out
