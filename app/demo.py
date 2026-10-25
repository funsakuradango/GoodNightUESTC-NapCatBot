"""Isolated end-to-end demo: intercepted websites + loopback fake OneBot server."""
import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading

from .config import Config
from .main import run_browser
from .notify import NotificationManager
from .state import StateStore, now


class DemoSite:
    def __init__(self):
        self.submissions = 0
        self.completed = False
        self.coordinates = None
        self.lose_response = False

    async def setup(self, context):
        async def route(r):
            url = r.request.url
            if url == 'https://auth.test/login':
                html = '''<form id="pwdFromId"><input id="username"><input id="password" type="password">
                <button id="login_submit" type="button" onclick="location='https://hall.test/finish'">登录</button></form>'''
            elif url == 'https://hall.test/finish':
                await context.add_cookies([{'name': 'demo_session', 'value': 'ok', 'domain': 'hall.test',
                                           'path': '/', 'expires': 2000000000}])
                html = '<button>未读通知</button>'
            elif url == 'https://hall.test/':
                cookies = await context.cookies()
                html = '<button>未读通知</button>' if any(c['name'] == 'demo_session' for c in cookies) else '<script>location="https://auth.test/login"</script>'
            elif url == 'https://hall.test/checkin':
                if self.completed:
                    html = '<div id="completed">已签到</div><time id="time">21:05:00</time>'
                else:
                    html = '''<div id="pending">待签到</div><button id="submit" disabled>签到</button>
                    <script>navigator.geolocation.getCurrentPosition(p => {
                      window.demoCoordinates = [p.coords.latitude, p.coords.longitude];
                      const b = document.querySelector('#submit'); b.disabled = false;
                      b.onclick = async () => { await fetch('/record', {method:'POST',
                        body:JSON.stringify(window.demoCoordinates)}); location.reload(); };
                    });</script>'''
            elif url == 'https://hall.test/record' and r.request.method == 'POST':
                self.submissions += 1
                self.coordinates = json.loads(r.request.post_data)
                self.completed = not self.lose_response
                await r.fulfill(status=200, body='{}')
                return
            else:
                await r.abort()
                return
            await r.fulfill(content_type='text/html; charset=utf-8', body=html)
        await context.route('**/*', route)


def demo_config(directory):
    return Config(profile=Path(directory) / 'browser-profile', hall_url='https://hall.test/',
                  login_origin='https://auth.test', username='demo-user', password='demo-password',
                  checkin_url='https://hall.test/checkin', pending_selector='#pending',
                  completed_selector='#completed', time_selector='#time', submit_selector='#submit',
                  checkin_enabled=True, latitude=0.0, longitude=0.0, page_timeout=3, auth_wait=3)


async def run_demo():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path != '/send_private_msg':
                self.send_error(404)
                return
            received.append(payload)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok","retcode":0,"data":{"message_id":1}}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='checkin-demo-') as directory:
            config = replace(demo_config(directory), napcat_url=f'http://127.0.0.1:{server.server_port}',
                             qq_user_id='10000')
            site = DemoSite()
            first = await run_browser(config, 'run', setup_context=site.setup)
            second = await run_browser(config, 'run', setup_context=site.setup)
            store = StateStore(config.data_dir)
            store.enqueue(now().date().isoformat(), first)
            notification = await NotificationManager(config).flush(store)
            await NotificationManager(config).flush(store)
            assert first.status == second.status == 'COMPLETED'
            assert site.submissions == 1 and site.coordinates == [0, 0]
            assert notification == 'sent' and len(received) == 1
            return {'mode': 'demo', 'status': 'COMPLETED', 'login': 'mock_school_account',
                    'geolocation': 'verified', 'submissions': site.submissions,
                    'second_run': 'skipped', 'notification': 'mock_onebot_sent_once',
                    'external_school_requests': 0, 'real_qq_messages': 0}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
