"""Offline authentication and outgoing-header contracts; no school/WeChat network."""
import asyncio
import json
from types import SimpleNamespace
import time

import pytest

from app.wechat import CHECKIN_URL, TicketGuard
from app.wechat_worker import AuthFailure, CredentialStore, acquire, school_transport

TICKET = 't' * 43


def credentials():
    pytest.importorskip('moon_uestc')
    from moon_uestc.wechat_models import WeChatCredentials
    return WeChatCredentials(openid='open', access_token='access', refresh_token='refresh',
                             expires_at=int(time.time()) + 7200)


def test_credentials_are_encrypted_account_bound_and_corruption_fails_closed(tmp_path):
    pytest.importorskip('cryptography')
    store = CredentialStore(tmp_path)
    store.save('student', credentials())
    assert b'refresh' not in store.path.read_bytes()
    assert store.path.stat().st_mode & 0o077 == 0
    assert store.read('student')['access_token'] == 'access'
    with pytest.raises(AuthFailure, match='account_changed'):
        store.read('other')
    store.path.write_bytes(b'corrupt')
    with pytest.raises(Exception):
        store.read('student')


def run_flow(tmp_path, *, mismatch=False, reused=False, expired=False):
    httpx2 = pytest.importorskip('httpx2')
    from moon_uestc import MoonClient
    from moon_uestc.wechat_models import WeChatCredentials
    from pydantic import SecretStr
    calls = []
    minted = []
    refreshed = []
    events = []

    def user(name):
        return {'userData': {'id': name, 'fullName': 'Test', 'username': name,
                            'role': '001', 'roleName': '学生', 'ability': None,
                            'roles': [{'id': 'student', 'name': '学生',
                                       'roleCode': '001', 'permissions': None}]}}

    def handler(request):
        path = request.url.path
        calls.append(path)
        assert 'X-Checkin-Ticket' not in request.headers
        if path == '/daily-checkin/api/auth/user-info':
            assert request.headers['cookie'] == 'SESSION=h5-session'
            assert request.headers['platform'] == 'h5'
            data = user('student')
        else:
            assert request.headers['platform'] == 'mp-weixin'
            assert 'h5-session' not in request.headers.get('cookie', '')
            if path.endswith('/check-bind'):
                assert 'cookie' not in request.headers
                assert json.loads(request.content) == {'code': 'code-1', 'sn': ''}
                data = {'bind': True, 'sessionId': 'mini-session'}
            elif path.endswith('/user-info'):
                assert request.headers['cookie'] == 'SESSION=mini-session'
                data = user('other' if mismatch else 'student')
            else:
                assert path == '/platform-app/api/auth/checkin-ticket'
                assert request.headers['cookie'] == 'SESSION=mini-session'
                assert json.loads(request.content) == {'code': 'code-2'}
                data = {'ticket': TICKET}
        return httpx2.Response(200, json={'code': 0, 'ok': True, 'data': data})

    def school(session):
        return MoonClient(session=session, transport=httpx2.MockTransport(handler))

    def mint(**kwargs):
        minted.append(kwargs['app_id'])
        return SecretStr('code-1' if reused else 'code-' + str(len(minted)))

    def refresh(old):
        refreshed.append(True)
        return credentials().model_copy(update={'access_token': SecretStr('rotated')})

    store = CredentialStore(tmp_path)
    value = credentials()
    if expired:
        value.expires_at = 1
    store.save('student', value)
    wechat = SimpleNamespace(get_code=mint, refresh=refresh)
    config = {'session': 'h5-session', 'student_id': 'student', 'allow_qr': False}
    try:
        acquire(config, school, wechat, WeChatCredentials, store,
                lambda kind, **values: events.append((kind, values)))
    except AuthFailure as error:
        return str(error), calls, minted, store, refreshed
    assert events == [('complete', {'ticket': TICKET})]
    return 'ok', calls, minted, store, refreshed


def test_auth_worker_blocks_business_writes_and_foreign_hosts_before_network():
    httpx2 = pytest.importorskip('httpx2')
    reached = []
    def handler(request):
        reached.append(request.url.path)
        return httpx2.Response(200, json={})
    with httpx2.Client(transport=school_transport(httpx2, httpx2.MockTransport(handler))) as client:
        client.get('https://jzsz.uestc.edu.cn/daily-checkin/api/auth/user-info')
        for url in (CHECKIN_URL, 'https://other.example/platform-app/api/auth/checkin-ticket',
                    'http://jzsz.uestc.edu.cn/platform-app/api/auth/checkin-ticket',
                    'https://jzsz.uestc.edu.cn:444/platform-app/api/auth/checkin-ticket'):
            with pytest.raises(AuthFailure, match='school_request_blocked'):
                client.post(url, json={})
    assert reached == ['/daily-checkin/api/auth/user-info']


def test_two_new_codes_and_h5_mini_sessions_remain_separate(tmp_path):
    result, calls, minted, _, _ = run_flow(tmp_path)
    assert result == 'ok'
    assert len(minted) == 2
    assert len(calls) == 4


def test_mismatched_identity_stops_before_second_code_and_ticket(tmp_path):
    result, calls, minted, _, _ = run_flow(tmp_path, mismatch=True)
    assert result == 'wechat_identity_mismatch'
    assert len(minted) == 1
    assert not any(p.endswith('/checkin-ticket') for p in calls)


def test_reused_code_is_rejected_before_ticket_request(tmp_path):
    result, calls, _, _, _ = run_flow(tmp_path, reused=True)
    assert result == 'wechat_code_reused'
    assert not any(p.endswith('/checkin-ticket') for p in calls)


def test_expired_credentials_refresh_and_rotation_is_persisted(tmp_path):
    result, _, _, store, refreshed = run_flow(tmp_path, expired=True)
    assert result == 'ok' and refreshed == [True]
    assert store.read('student')['access_token'] == 'rotated'


class Route:
    def __init__(self, url=CHECKIN_URL, method='POST', status=200):
        async def headers():
            return {'cookie': 'SESSION=h5', 'x-checkin-ticket': 'stale', 'content-type': 'application/json'}
        self.request = SimpleNamespace(url=url, method=method, all_headers=headers)
        self.status = status
        self.actions = []

    async def fetch(self, **kwargs):
        self.actions.append(('fetch', kwargs))
        return SimpleNamespace(status=self.status)

    async def fulfill(self, **kwargs):
        self.actions.append(('fulfill', kwargs))

    async def continue_(self, **kwargs):
        self.actions.append(('continue', kwargs))

    async def abort(self, reason):
        self.actions.append(('abort', reason))


@pytest.mark.parametrize('status', [200, 302])
def test_post_exactly_once_and_redirect_is_not_followed(status):
    async def scenario():
        guard = TicketGuard(TICKET)
        first, second = Route(status=status), Route()
        await guard.handle(first)
        await guard.handle(second)
        options = first.actions[0][1]
        assert options['headers']['x-checkin-ticket'] == TICKET
        assert options['headers']['cookie'] == 'SESSION=h5'
        assert options['max_redirects'] == options['max_retries'] == 0
        assert first.actions[1][0] == ('abort' if status == 302 else 'fulfill')
        assert second.actions == [('abort', 'blockedbyclient')]
        assert guard.ticket is None
    asyncio.run(scenario())


@pytest.mark.parametrize('url,method', [
    (CHECKIN_URL, 'GET'), (CHECKIN_URL + '?unexpected=1', 'POST'),
    ('https://other.example/checkin', 'POST'),
    ('https://jzsz.uestc.edu.cn/daily-checkin/api/student-checkin-record/today', 'GET')])
def test_ticket_never_added_to_other_requests(url, method):
    async def scenario():
        guard = TicketGuard(TICKET)
        route = Route(url, method)
        await guard.handle(route)
        assert route.actions[0][0] == 'continue'
        assert 'x-checkin-ticket' not in route.actions[0][1]['headers']
        assert guard.used is False
    asyncio.run(scenario())


def test_browser_storage_to_one_post_and_no_ticket_on_readback(monkeypatch):
    """Exercise real Chromium and route.fetch against a loopback fixture only."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    async_playwright = pytest.importorskip('playwright.async_api').async_playwright
    import app.wechat as bridge

    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/':
                body = b'''<script>
                async function exercise() {
                  const t = sessionStorage.getItem('daily-checkin-operation-ticket');
                  await fetch('/today', {headers: {'X-Checkin-Ticket': t}});
                  for (let i=0; i<2; i++) {
                    try { await fetch('/checkin', {method:'POST',
                      headers: {'Content-Type':'application/json', 'platform':'h5',
                                'X-Checkin-Ticket':t}, body:'{"fixture":true}'}); }
                    catch(e) {}
                  }
                  window.done=true;
                }
                exercise();</script>'''
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(body)
            else:
                observed.append(('GET', self.headers.get('X-Checkin-Ticket')))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            observed.append(('POST', self.headers.get('X-Checkin-Ticket'),
                             self.headers.get('Cookie'), body))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"code":0}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(bridge, 'ORIGIN', origin)
    monkeypatch.setattr(bridge, 'CHECKIN_URL', origin + '/checkin')

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                context = await browser.new_context(service_workers='block')
                await context.add_cookies([{'name': 'SESSION', 'value': 'fixture-h5', 'url': origin}])
                page = await context.new_page()
                await page.goto(origin + '/idle')
                observed.clear()
                guard = await bridge.install_ticket(context, TICKET)
                assert await page.evaluate("sessionStorage.getItem('daily-checkin-operation-ticket')") == TICKET
                await page.goto(origin)
                await page.wait_for_function('window.done === true')
                assert guard.used and guard.ticket is None
            finally:
                await browser.close()
    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert observed == [('GET', None), ('POST', TICKET, 'SESSION=fixture-h5', b'{"fixture":true}')]
