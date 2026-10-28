from dataclasses import replace
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.config import Config
from app.main import task_lock
from app.management import Controller, make_server
from app.settings import apply_settings, read_settings, save_settings


def config_at(directory):
    return Config(profile=directory / 'browser-profile', username='student', password='original-secret')


def test_private_overrides_preserve_password_and_reject_account_switch(tmp_path):
    config = config_at(tmp_path)
    save_settings(config, {'username': 'student', 'password': 'new-secret $ #', 'checkin_enabled': False})
    save_settings(config, {'password': ''})
    current = apply_settings(config)
    assert current.password == 'new-secret $ #'
    assert not current.checkin_enabled
    assert (tmp_path / 'settings.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        save_settings(config, {'username': 'another-student'})
    with pytest.raises(ValueError):
        save_settings(config, {'checkin_enabled': 'false'})
    with pytest.raises(ValueError):
        save_settings(config, {'password': 'a\nb'})
    with pytest.raises(ValueError):
        save_settings(config, {'checkin_enabled': True})  # No location/business configuration.
    assert not apply_settings(config).checkin_enabled
    assert apply_settings(config).password == current.password
    (tmp_path / 'settings.json').chmod(0o644)
    with pytest.raises(ValueError):
        read_settings(tmp_path)


@pytest.fixture
def api(tmp_path):
    controller = Controller(config_at(tmp_path))
    token = 'test-token-with-at-least-32-characters'
    server = make_server(controller, token, ('127.0.0.1', 0),
                         login_verifier=lambda body: body.get('token') == 'correct-private-admin-token')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None, auth=True, raw=None, extra_headers=None):
        headers = {'Content-Type': 'application/json', **(extra_headers or {})}
        if auth:
            headers['Authorization'] = 'Bearer ' + token
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = Request(f'http://127.0.0.1:{server.server_port}{path}', data=data, headers=headers)
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as error:
            response = error
        return response.status, json.loads(response.read()), response.headers

    yield controller, request
    server.shutdown()
    server.server_close()
    thread.join()


def test_api_requires_auth_and_never_returns_password(api):
    controller, request = api
    for path, body in [('/status', None), ('/qr', None), ('/settings', {'password': 'stolen'}),
                       ('/jobs', {'action': 'login'}), ('/cancel', {})]:
        assert request(path, body, auth=False)[0] == 401
    status, body, headers = request('/status')
    assert status == 200 and body['password_set']
    assert 'original-secret' not in json.dumps(body)
    assert 'password' not in body
    assert headers['Cache-Control'] == 'no-store'
    assert request('/settings', {'password': 'updated-private'})[0] == 200
    assert 'updated-private' not in json.dumps(request('/status')[1])


def test_web_login_cookie_csrf_and_logout(api):
    _, request = api
    assert request('/session', {'token': 'wrong'}, auth=False)[0] == 401
    status, body, headers = request('/session', {'token': 'correct-private-admin-token'}, auth=False)
    assert status == 200 and body == {'authenticated': True}
    cookie = headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    assert 'correct-private-admin-token' not in cookie
    cookie_header = {'Cookie': cookie.split(';')[0]}
    assert request('/api/status', auth=False, extra_headers=cookie_header)[0] == 200
    assert request('/api/settings', {'password': 'new'}, auth=False,
                   extra_headers={**cookie_header, 'Origin': 'http://attacker.invalid'})[0] == 403
    assert request('/api/logout', {}, auth=False, extra_headers=cookie_header)[0] == 200
    assert request('/api/status', auth=False, extra_headers=cookie_header)[0] == 401


def test_web_login_rate_limit(api):
    _, request = api
    for _ in range(6):
        assert request('/session', {'token': 'wrong'}, auth=False)[0] == 401
    assert request('/session', {'token': 'wrong'}, auth=False)[0] == 429


def test_qr_is_only_available_for_active_authorization(api):
    controller, request = api
    qr = controller.config.data_dir / 'wechat-authorization.png'
    qr.write_bytes(b'private-qr-image')
    assert request('/qr')[0] == 404
    controller.job = {'action': 'check', 'running': True}
    assert request('/qr')[0] == 404
    controller.job = {'action': 'wechat-authorize', 'running': True}
    assert request('/qr', auth=False)[0] == 401
    assert request('/qr')[1]['image'].startswith('data:image/png;base64,')
    controller.job['running'] = False
    assert request('/qr')[0] == 404


def test_no_submission_notification_or_arbitrary_command_api(api):
    _, request = api
    for action in ['run', 'flush-notifications', 'resume', 'sh', ['login']]:
        assert request('/jobs', {'action': action})[0] == 400
    assert request('/settings', {'NAPCAT_TOKEN': 'secret'})[0] == 400
    assert request('/settings', {'username': 'other'})[0] == 400
    assert request('/settings', raw=b'{broken')[0] == 400
    assert request('/settings', raw=b' ' * 5000)[0] == 413
    assert request('/qr')[0] == 404


def test_shared_lock_prevents_editing_or_starting_during_business_task(api):
    controller, request = api
    with task_lock(controller.config.data_dir / 'task.lock'):
        assert request('/jobs', {'action': 'check'})[0] == 409
        assert request('/settings', {'password': 'should-not-save'})[0] == 409
    assert not (controller.config.data_dir / 'settings.json').exists()


def test_management_worker_disables_notifications_and_redacts_output(api, monkeypatch):
    controller, request = api
    seen = []

    class Process:
        pid = 99999999
        def __init__(self, args, **kwargs):
            seen.append((args, kwargs['env']))
        def communicate(self, timeout):
            return ('{"event":"qr","image":"private-image"}\n'
                    '{"status":"AUTHENTICATED","reason":"session_restored","ticket":"private-ticket"}\n', None)
        def poll(self):
            return 0
        def wait(self):
            return 0

    monkeypatch.setattr('app.management.subprocess.Popen', Process)
    assert request('/jobs', {'action': 'check'})[0] == 202
    deadline = time.monotonic() + 3
    while controller.snapshot()['job']['running'] and time.monotonic() < deadline:
        time.sleep(0.01)
    result = controller.snapshot()['job']
    assert result['result'] == {'status': 'AUTHENTICATED', 'reason': 'session_restored'}
    assert 'private' not in json.dumps(result)
    assert seen[0][0][-2:] == ['check', '--worker']
    assert all(seen[0][1][key] == '' for key in ('QQ_USER_ID', 'NAPCAT_HTTP_URL', 'NAPCAT_TOKEN'))
    assert controller.last_path.stat().st_mode & 0o777 == 0o600
    with task_lock(controller.config.data_dir / 'task.lock'):
        pass


def test_scheduler_pause_does_not_consume_daily_attempt(tmp_path, monkeypatch):
    from datetime import datetime
    from app import scheduler
    from app.demo import demo_config
    from app.state import StateStore, TZ
    config = demo_config(tmp_path)
    monkeypatch.setattr(scheduler, 'now', lambda: datetime(2026, 10, 28, 21, 5, tzinfo=TZ))
    commands = []
    monkeypatch.setattr(scheduler.subprocess, 'run', lambda args, **kw: commands.append(args))
    def stop(_):
        raise InterruptedError()
    monkeypatch.setattr(scheduler.time, 'sleep', stop)
    save_settings(config, {'checkin_enabled': False})
    with pytest.raises(InterruptedError):
        scheduler.serve(config)
    assert not any(cmd[-1] == 'run' for cmd in commands)
    assert not StateStore(config.data_dir).data.get('scheduled_date')
    save_settings(config, {'checkin_enabled': True})
    with pytest.raises(InterruptedError):
        scheduler.serve(config)
    assert sum(cmd[-1] == 'run' for cmd in commands) == 1
