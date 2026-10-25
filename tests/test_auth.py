import asyncio
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from app.auth import AuthManager, Status
from app.config import Config
from app.main import run_browser, task_lock

FORM = '''<form id="pwdFromId"><input id="username"><input id="password" type="password">
<input id="captcha" style="display:none"><a id="login_submit" onclick="SUBMIT">登录</a></form>'''


def test_browser_auth_lifecycle(tmp_path):
    async def scenario():
        cfg = Config(profile=tmp_path / 'data/browser-profile',
                     hall_url='https://hall.test/', login_origin='https://auth.test',
                     username='mock-user', password='mock-password', auth_wait=0.8)
        cfg.profile.mkdir(parents=True)
        mode = {'value': 'success', 'submits': 0}
        async def route(r):
            if r.request.url.startswith(cfg.hall_url):
                cookies = await r.request.all_headers()
                if 'mock_session=ok' in cookies.get('cookie', ''):
                    await r.fulfill(content_type='text/html; charset=utf-8', body='<button>未读通知 0 条</button>')
                else:
                    await r.fulfill(content_type='text/html; charset=utf-8', body='<script>location="https://auth.test/login"</script>')
            elif r.request.url == 'https://auth.test/submit':
                mode['submits'] += 1
                if mode['value'] == 'success':
                    await r.fulfill(content_type='text/html; charset=utf-8', body='<script>location="https://hall.test/finish"</script>')
                elif mode['value'] == 'reject':
                    await r.fulfill(content_type='text/html; charset=utf-8', body='<form id="pwdFromId"><span id="showErrorTip">错误</span></form>')
                else:
                    await r.fulfill(content_type='text/html; charset=utf-8', body='<input autocomplete="one-time-code">')
            else:
                html = FORM.replace('SUBMIT', "location='/submit'")
                if mode['value'] == 'captcha':
                    html = html.replace('display:none', 'display:block')
                # The real page duplicates IDs across visible desktop and hidden mobile forms.
                await r.fulfill(content_type='text/html; charset=utf-8',
                                body=html + '<section hidden>' + html + '</section>')
        async with async_playwright() as p:
            async def context():
                c = await p.chromium.launch_persistent_context(str(cfg.profile), chromium_sandbox=True)
                async def handler(r):
                    if r.request.url == 'https://hall.test/finish':
                        await c.add_cookies([{'name':'mock_session', 'value':'ok',
                            'domain':'hall.test', 'path':'/', 'expires':2000000000}])
                        await r.fulfill(content_type='text/html; charset=utf-8', body='<button>未读通知 0 条</button>')
                        return
                    await route(r)
                await c.route('**/*', handler)
                return c
            c = await context()
            auth = AuthManager(cfg)
            try:
                result = await auth.ensure_authenticated(c.pages[0], check_only=True)
                assert result.status == Status.AUTH_REQUIRED
                result = await auth.ensure_authenticated(c.pages[0])
                assert result.reason == 'login_completed'
                assert mode['submits'] == 1
            finally:
                await c.close()
            # New browser process, same on-disk profile; no login submission.
            c = await context()
            try:
                assert (await auth.ensure_authenticated(c.pages[0])).reason == 'session_restored'
                assert mode['submits'] == 1
                await c.clear_cookies()
                assert (await auth.ensure_authenticated(c.pages[0])).reason == 'login_completed'
                assert mode['submits'] == 2
                await c.clear_cookies()
                mode['value'] = 'captcha'
                assert (await auth.ensure_authenticated(c.pages[0])).status == Status.MANUAL_ACTION_REQUIRED
                assert mode['submits'] == 2
                mode['value'] = 'success'
                assert (await auth.ensure_authenticated(c.pages[0])).reason == 'login_paused'
                auth.pause.unlink()
                mode['value'] = 'challenge'
                assert (await auth.ensure_authenticated(c.pages[0])).reason == 'verification_required'
                assert mode['submits'] == 3
                auth.pause.unlink()
                mode['value'] = 'reject'
                assert (await auth.ensure_authenticated(c.pages[0])).reason == 'login_unconfirmed'
                assert auth.pause.exists()
                assert (await auth.ensure_authenticated(c.pages[0])).reason == 'login_paused'
                assert mode['submits'] == 4
            finally:
                await c.close()
    asyncio.run(scenario())


def test_navigation_retries_only_timeouts(tmp_path):
    async def scenario():
        auth = AuthManager(Config(profile=tmp_path))
        page = AsyncMock()
        page.goto.side_effect = [BrowserTimeout('private details'), None]
        await auth.open_hall(page)
        assert page.goto.await_count == 2
        page.goto.reset_mock()
        page.goto.side_effect = BrowserTimeout('private details')
        with pytest.raises(BrowserTimeout):
            await auth.open_hall(page)
        assert page.goto.await_count == 2
    asyncio.run(scenario())


def test_lock_prevents_overlap(tmp_path):
    with task_lock(tmp_path / 'task.lock'):
        with pytest.raises(BlockingIOError):
            with task_lock(tmp_path / 'task.lock'):
                pass
    with task_lock(tmp_path / 'task.lock'):
        pass


def test_unrecognized_page_never_submits(tmp_path):
    async def scenario():
        auth = AuthManager(Config(profile=tmp_path, username='u', password='p'))
        auth.open_hall = AsyncMock()
        auth.wait_state = AsyncMock(return_value='unknown')
        page = AsyncMock()
        assert (await auth.ensure_authenticated(page)).reason == 'unrecognized_page'
        page.locator.assert_not_called()
    asyncio.run(scenario())


def test_browser_cleanup_on_error(tmp_path, monkeypatch):
    async def scenario():
        seen = []
        async def failure(self, page, **kwargs):
            seen.append(page.context)
            raise RuntimeError('private error')
        monkeypatch.setattr(AuthManager, 'ensure_authenticated', failure)
        with pytest.raises(RuntimeError):
            await run_browser(Config(profile=tmp_path / 'profile'), 'check')
        assert seen and not seen[0].pages
    asyncio.run(scenario())


def test_credentials_not_in_repr():
    assert 'top-secret' not in repr(Config(password='top-secret'))


def test_env_permissions(tmp_path, monkeypatch):
    import app.config as config_module
    monkeypatch.setattr(config_module, 'ROOT', tmp_path)
    env = tmp_path / '.env'
    env.write_text('UESTC_PASSWORD=mock-secret\n')
    env.chmod(0o644)
    with pytest.raises(ValueError, match='env_permissions'):
        Config.load()


def test_success_marker_on_wrong_origin_is_not_authenticated(tmp_path):
    async def scenario():
        auth = AuthManager(Config(profile=tmp_path))
        page = AsyncMock()
        page.url = 'https://unexpected.test/'
        auth.visible = AsyncMock(return_value=True)
        assert not await auth.authenticated(page)
    asyncio.run(scenario())
