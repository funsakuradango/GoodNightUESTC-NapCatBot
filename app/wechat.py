"""Browser bridge: worker secrets stay on pipes; one ticket permits one exact POST."""
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import sys

from .wechat_worker import atomic_private

ORIGIN = 'https://jzsz.uestc.edu.cn'
CHECKIN_URL = ORIGIN + '/daily-checkin/api/student-checkin-record/checkin'
STORAGE_KEY = 'daily-checkin-operation-ticket'


class WeChatFailure(Exception):
    pass


async def acquire_ticket(context, config, *, allow_qr=False):
    cookies = await context.cookies(ORIGIN + '/daily-checkin/api/auth/user-info')
    sessions = {c['value'] for c in cookies if c['name'] == 'SESSION'
                and c['domain'].lstrip('.') == 'jzsz.uestc.edu.cn'}
    if len(sessions) != 1:
        raise WeChatFailure('school_session_required')
    interpreter = config.wechat_python or sys.executable
    worker = Path(__file__).with_name('wechat_worker.py')
    child_env = dict(os.environ)
    # The container carries a separate >=3.13 runtime for the pinned SDK.
    if interpreter == '/opt/wechat/bin/python3':
        child_env.update(PYTHONHOME='/opt/wechat', LD_LIBRARY_PATH='/opt/wechat/lib')
    proc = await asyncio.create_subprocess_exec(
        interpreter, '-u', str(worker), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=child_env, limit=2_000_000)
    qr_path = config.data_dir / 'wechat-authorization.png'
    try:
        async with asyncio.timeout(300 if allow_qr else 110):
            data = {'session': sessions.pop(), 'student_id': config.username,
                    'directory': str(config.data_dir / 'wechat'), 'allow_qr': allow_qr}
            proc.stdin.write((json.dumps(data) + '\n').encode())
            await proc.stdin.drain()
            proc.stdin.close()
            ticket = None
            while line := await proc.stdout.readline():
                event = json.loads(line)
                if event['event'] == 'qr' and allow_qr:
                    atomic_private(qr_path, base64.b64decode(event['image'], validate=True))
                    print(json.dumps({'status': 'WECHAT_QR_READY', 'qr_path': str(qr_path)}), flush=True)
                elif event['event'] == 'error':
                    raise WeChatFailure(event['reason'])
                elif event['event'] == 'complete':
                    ticket = event['ticket']
            if await proc.wait() != 0 or not isinstance(ticket, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', ticket):
                raise WeChatFailure('wechat_worker_incomplete')
            return ticket
    finally:
        qr_path.unlink(missing_ok=True)
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


class TicketGuard:
    def __init__(self, ticket):
        self.ticket = ticket
        self.used = False

    async def handle(self, route):
        request = route.request
        headers = await request.all_headers()
        headers.pop('x-checkin-ticket', None)
        if request.url == CHECKIN_URL and request.method == 'POST':
            if self.used:
                await route.abort('blockedbyclient')
                return
            self.used = True
            headers['x-checkin-ticket'] = self.ticket
            # Never forward this credential along a redirect or retry a write.
            try:
                response = await route.fetch(headers=headers, max_redirects=0, max_retries=0)
                if 300 <= response.status < 400:
                    await route.abort('blockedbyclient')
                else:
                    await route.fulfill(response=response)
            except Exception:
                await route.abort('failed')
            finally:
                self.ticket = None
        else:
            await route.continue_(headers=headers)


async def install_ticket(context, ticket):
    # The H5 reads this key when building its request header. Only school frames
    # receive it. The route guard is the final authority on outgoing headers.
    guard = TicketGuard(ticket)
    await context.route('**/*', guard.handle)
    script = ('if (location.origin === ' + json.dumps(ORIGIN) + ') '
              'sessionStorage.setItem(' + json.dumps(STORAGE_KEY) + ', ' + json.dumps(ticket) + ');')
    await context.add_init_script(script=script)
    # An entry click can be an SPA transition without a new document. Install
    # into existing school frames as well as future documents/popups.
    for page in context.pages:
        for frame in page.frames:
            if not frame.is_detached():
                await frame.evaluate('() => {' + script + '}')
    return guard
