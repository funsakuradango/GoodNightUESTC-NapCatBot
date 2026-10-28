"""Authenticated management API. No attendance submission or notification action."""
import base64
from contextlib import ExitStack
import hmac
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from http.cookies import SimpleCookie
from pathlib import Path

from .config import Config
from .main import task_lock
from .settings import apply_settings, save_settings
from .state import StateStore, now
from .wechat_worker import atomic_private

ACTIONS = {'check', 'login', 'status', 'wechat-ticket', 'wechat-authorize', 'manual-login'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def stop_process(proc):
    # Also clean descendants if their direct parent has already exited.
    if proc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


class Controller:
    def __init__(self, config):
        self.config = config
        self.gate = threading.Lock()
        self.job = None
        self.cancel = threading.Event()
        self.last_path = config.data_dir / 'admin-last-job.json'
        if self.last_path.exists():
            self.job = json.loads(self.last_path.read_text())
            if self.job.get('running'):
                self.job.update(running=False, result={'status': 'ERROR', 'reason': 'management_restarted'})

    def snapshot(self):
        config = apply_settings(self.config)
        store = StateStore(config.data_dir)
        with self.gate:
            job = dict(self.job) if self.job else None
        qr = config.data_dir / 'wechat-authorization.png'
        return {
            'username': config.username, 'password_set': bool(config.password),
            'checkin_enabled': config.checkin_enabled,
            'schedule': f'{config.checkin_hour:02}:{config.checkin_minute:02} Asia/Shanghai',
            'date': now().date().isoformat(), 'today': store.day(now().date().isoformat()),
            'scheduled_date': store.data.get('scheduled_date'),
            'auth_paused': (config.data_dir / 'auth-paused').exists(),
            'wechat_saved': (config.data_dir / 'wechat/wechat.enc').exists(),
            'qr_ready': bool(job and job['running'] and job['action'] == 'wechat-authorize' and qr.exists()),
            'job': job,
        }

    def save(self, changes):
        with self.gate:
            if self.job and self.job['running']:
                raise BlockingIOError()
            with task_lock(self.config.data_dir / 'task.lock'):
                save_settings(self.config, changes)

    def start(self, action, resume=False):
        if action not in ACTIONS or type(resume) is not bool or (resume and action != 'login'):
            raise ValueError('unsupported_action')
        with self.gate:
            if self.job and self.job['running']:
                raise BlockingIOError()
            # Claim the same cross-container lock before acknowledging the job.
            lock = task_lock(self.config.data_dir / 'task.lock')
            lock.__enter__()
            self.cancel.clear()
            self.job = {'action': action, 'running': True, 'started_at': now().isoformat(), 'result': None}
            try:
                threading.Thread(target=self._run, args=(action, resume, lock), daemon=True).start()
            except Exception:
                self.job['running'] = False
                lock.__exit__(None, None, None)
                raise

    def _run(self, action, resume, lock):
        proc = None
        result = {'status': 'ERROR', 'reason': 'management_operation_failed'}
        try:
            config = apply_settings(self.config)
            if resume:
                (config.data_dir / 'auth-paused').unlink(missing_ok=True)
            env = dict(os.environ, NAPCAT_HTTP_URL='', QQ_USER_ID='', NAPCAT_TOKEN='')
            with ExitStack() as services:
                if action == 'manual-login':
                    env['DISPLAY'] = ':99'
                    # This stack only exists for a bounded interactive login.
                    for args in (
                        ['Xvfb', ':99', '-screen', '0', '1280x900x24', '-nolisten', 'tcp'],
                        ['x11vnc', '-display', ':99', '-localhost', '-rfbport', '5900', '-nopw', '-forever', '-shared', '-quiet'],
                        ['websockify', '--web=/usr/share/novnc/', '6080', 'localhost:5900'],
                    ):
                        service = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                   start_new_session=True, env=env)
                        services.callback(stop_process, service)
                        time.sleep(0.5)
                        if service.poll() is not None:
                            raise RuntimeError('browser_service_unavailable')
                    env['TASK_TIMEOUT_SECONDS'] = '600'
                budget = 615 if action == 'manual-login' else max(config.task_timeout + 15, 375 if action == 'wechat-authorize' else 0)
                proc = subprocess.Popen([sys.executable, '-m', 'app.main', action, '--worker'],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                        start_new_session=True, text=True)
                deadline = time.monotonic() + budget
                while True:
                    if self.cancel.is_set() or time.monotonic() >= deadline:
                        stop_process(proc)
                        result = {'status': 'ERROR', 'reason': 'cancelled' if self.cancel.is_set() else 'task_or_page_timeout'}
                        break
                    try:
                        output, _ = proc.communicate(timeout=0.25)
                        for line in output.splitlines():
                            try:
                                event = json.loads(line)
                            except (ValueError, TypeError):
                                continue
                            # Never return diagnostics, raw URLs, tickets or worker output.
                            if all(isinstance(event.get(k), str) and re.fullmatch(r'[A-Za-z0-9_]{1,100}', event[k])
                                   for k in ('status', 'reason')):
                                result = {k: event[k] for k in ('status', 'reason')}
                                if action == 'status' and re.fullmatch(r'[0-9T:+.\-]{1,50}', event.get('checkin_time', '')):
                                    result['checkin_time'] = event['checkin_time']
                        break
                    except subprocess.TimeoutExpired:
                        pass
        except Exception:
            pass  # Fixed result above; exception strings can contain private input.
        finally:
            stop_process(proc)
            (self.config.data_dir / 'wechat-authorization.png').unlink(missing_ok=True)
            try:
                with self.gate:
                    self.job.update(running=False, finished_at=now().isoformat(), result=result)
                    atomic_private(self.last_path, json.dumps(self.job).encode())
            finally:
                lock.__exit__(None, None, None)


def make_server(controller, token, address=('0.0.0.0', 8080), web_root=None, login_verifier=None):
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('management_token_required')
    sessions = {}
    attempts = {}
    session_lock = threading.Lock()
    web_root = web_root or Path(__file__).resolve().parents[1] / 'webui'

    def verify_login(body):
        if login_verifier:
            return login_verifier(body)
        supplied = body.get('token')
        if not isinstance(supplied, str) or not 1 <= len(supplied) <= 512:
            return False
        base = os.environ.get('NAPCAT_WEBUI_URL', '')
        url = urlsplit(base)
        if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password:
            return False
        payload = {'hash': hashlib.sha256((supplied + '.napcat').encode()).hexdigest()}
        code = body.get('totpCode', '')
        if not isinstance(code, str) or len(code) > 10:
            return False
        if code:
            payload['totpCode'] = code
        request = Request(base.rstrip('/') + '/api/auth/login',
                          data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        # Use the private configured service directly, never a client-supplied URL or proxy.
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=10) as response:
            data = json.load(response)
        return data.get('code') == 0 and bool(data.get('data', {}).get('Credential'))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body, cookie=None):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(data)))
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(data)

        def handle_request(self):
            self.connection.settimeout(10)
            path = urlsplit(self.path).path
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                return self.reply(403, {'error': 'origin_rejected'})
            if self.command == 'GET' and path in ('/', '/files/assets/style.css', '/files/assets/app.js'):
                filename, content_type = {
                    '/': ('index.html', 'text/html; charset=utf-8'),
                    '/files/assets/style.css': ('style.css', 'text/css; charset=utf-8'),
                    '/files/assets/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                }[path]
                try:
                    content = (web_root / filename).read_bytes()
                except OSError:
                    return self.reply(404, {'error': 'not_found'})
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(content)
                return
            if self.command == 'POST' and path == '/session':
                try:
                    if 'application/json' not in self.headers.get('Content-Type', '') or self.headers.get('Transfer-Encoding'):
                        return self.reply(415, {'error': 'json_required'})
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 2048:
                        return self.reply(413, {'error': 'invalid_body_size'})
                    # Apply a local limit in addition to NapCat's own login protection.
                    key = self.client_address[0]
                    with session_lock:
                        current = time.monotonic()
                        recent = [t for t in attempts.get(key, []) if t > current - 60]
                        if len(recent) >= 6:
                            return self.reply(429, {'error': 'login_rate_limited'})
                        attempts[key] = recent + [current]
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict) or not verify_login(body):
                        return self.reply(401, {'error': 'invalid_login'})
                    session = secrets.token_urlsafe(32)
                    with session_lock:
                        expired = [key for key, deadline in sessions.items() if deadline <= current]
                        for key in expired:
                            sessions.pop(key)
                        if len(sessions) >= 32:
                            sessions.pop(next(iter(sessions)))
                        sessions[session] = time.monotonic() + 3600
                    return self.reply(200, {'authenticated': True},
                                      f'goodnight_session={session}; Path=/; HttpOnly; SameSite=Strict; Max-Age=3600')
                except Exception:
                    return self.reply(401, {'error': 'invalid_login'})
            auth = self.headers.get('Authorization', '')
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
                session = cookie['goodnight_session'].value if 'goodnight_session' in cookie else ''
            except Exception:
                session = ''
            with session_lock:
                logged_in = sessions.get(session, 0) > time.monotonic()
            if not logged_in and not hmac.compare_digest(auth.encode(), ('Bearer ' + token).encode()):
                return self.reply(401, {'error': 'unauthorized'})
            if path.startswith('/api/'):
                path = path[4:]
            if self.command == 'POST' and path == '/logout':
                with session_lock:
                    sessions.pop(session, None)
                return self.reply(200, {'logged_out': True}, 'goodnight_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
            try:
                if self.command == 'GET' and path == '/status':
                    return self.reply(200, controller.snapshot())
                if self.command == 'GET' and path == '/qr':
                    if not controller.snapshot()['qr_ready']:
                        return self.reply(404, {'error': 'qr_not_ready'})
                    data = (controller.config.data_dir / 'wechat-authorization.png').read_bytes()
                    return self.reply(200, {'image': 'data:image/png;base64,' + base64.b64encode(data).decode()})
                if self.command != 'POST' or path not in ('/settings', '/jobs', '/cancel'):
                    return self.reply(404, {'error': 'not_found'})
                if 'application/json' not in self.headers.get('Content-Type', '') or self.headers.get('Transfer-Encoding'):
                    return self.reply(415, {'error': 'json_required'})
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096:
                    return self.reply(413, {'error': 'invalid_body_size'})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('invalid_body')
                if path == '/settings':
                    controller.save(body)
                    return self.reply(200, {'saved': True})
                if path == '/jobs':
                    if set(body) - {'action', 'resume'}:
                        raise ValueError('unsupported_action')
                    controller.start(body.get('action'), body.get('resume', False))
                    return self.reply(202, {'started': True})
                if body:
                    raise ValueError('invalid_body')
                controller.cancel.set()
                return self.reply(200, {'cancel_requested': True})
            except BlockingIOError:
                self.reply(409, {'error': 'task_already_running'})
            except (ValueError, TypeError):
                self.reply(400, {'error': 'invalid_request'})
            except Exception:
                self.reply(500, {'error': 'management_operation_failed'})

        do_GET = handle_request
        do_POST = handle_request

    return ThreadingHTTPServer(address, Handler)


def main():
    os.umask(0o077)
    config = Config.load()
    if config.wechat_python and not Path(config.wechat_python).is_file():
        raise RuntimeError('management_wechat_runtime_missing')
    controller = Controller(config)
    server = make_server(controller, os.environ.get('MANAGEMENT_TOKEN', ''))
    print(json.dumps({'event': 'management_ready', 'port': server.server_port}), flush=True)
    try:
        server.serve_forever()
    finally:
        controller.cancel.set()
        server.server_close()


if __name__ == '__main__':
    main()
