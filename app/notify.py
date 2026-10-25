"""OneBot HTTP notifications; never include raw page content or credentials."""
import asyncio
import json
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NotificationManager:
    def __init__(self, config):
        self.config = config

    def send(self, text):
        url = self.config.napcat_url.rstrip('/')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username:
            raise ValueError('napcat_url')
        headers = {'Content-Type': 'application/json'}
        if self.config.napcat_token:
            headers['Authorization'] = f'Bearer {self.config.napcat_token}'
        body = {'user_id': self.config.qq_user_id,
                'message': [{'type': 'text', 'data': {'text': text}}]}
        req = Request(url + '/send_private_msg', json.dumps(body).encode(), headers, method='POST')
        # Do not forward the bearer token via redirects or ambient system proxies.
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=8) as response:
            result = json.loads(response.read(65536))
        if (result.get('status') != 'ok' or result.get('retcode') != 0
                or not (result.get('data') or {}).get('message_id')):
            raise ValueError('notification_rejected')

    async def flush(self, store):
        if not (self.config.napcat_url and self.config.qq_user_id):
            return 'disabled'
        failed = False
        # Bound each invocation. Failures remain in JSON for the next invocation.
        pending = [item for item in store.data['notifications'].values() if not item['sent']][:3]
        for item in pending:
            item['attempts'] += 1
            try:
                await asyncio.to_thread(self.send, item['text'])
                item['sent'] = True
            except Exception:
                failed = True
            store.save()
        return 'pending' if failed or any(not n['sent'] for n in store.data['notifications'].values()) else 'sent'
