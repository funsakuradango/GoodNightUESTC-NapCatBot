"""Isolated Python >=3.13 authentication worker. No attendance endpoints allowed."""
import base64
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ORIGIN = 'https://jzsz.uestc.edu.cn'
APP_ID = 'wxaa78bf2905f01cee'


class AuthFailure(Exception):
    pass


def atomic_private(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.wechat-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class CredentialStore:
    def __init__(self, directory):
        from cryptography.fernet import Fernet
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        key = self.directory / 'wechat.key'
        self.path = self.directory / 'wechat.enc'
        if not key.exists():
            if self.path.exists():
                raise AuthFailure('wechat_key_missing')
            atomic_private(key, Fernet.generate_key())
        if key.stat().st_mode & 0o077:
            raise AuthFailure('wechat_key_permissions')
        self.cipher = Fernet(key.read_bytes())

    def read(self, student_id):
        if not self.path.exists():
            return None
        saved = json.loads(self.cipher.decrypt(self.path.read_bytes()))
        if saved['student_id'] != student_id:
            raise AuthFailure('wechat_account_changed')
        return saved['credentials']

    def save(self, student_id, credentials):
        value = {'student_id': student_id, 'credentials': {
            name: getattr(credentials, name).get_secret_value()
            for name in ('openid', 'access_token', 'refresh_token')}}
        value['credentials']['expires_at'] = credentials.expires_at
        atomic_private(self.path, self.cipher.encrypt(json.dumps(value).encode()))


def acquire(config, school, wechat, credentials_type, store, emit):
    """Verify both school identities and consume two independently acquired codes."""
    with school(config['session']) as h5:
        expected = h5.auth.user_info().data.user_data
    if config.get('student_id') and expected.username != config['student_id']:
        raise AuthFailure('school_identity_mismatch')
    cached = store.read(expected.username)
    if cached:
        credentials = credentials_type.model_validate(cached)
        if credentials.expires_at <= time.time() + 60:
            credentials = wechat.refresh(credentials)
    else:
        if not config.get('allow_qr'):
            raise AuthFailure('wechat_authorization_required')
        qr = wechat.create_qr()
        emit('qr', image=base64.b64encode(qr.image).decode('ascii'))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = wechat.poll_qr(qr.uuid)
            if result.status == 'authorized' and result.code:
                credentials = wechat.exchange_code(result.code)
                break
            if result.status in ('cancelled', 'expired'):
                raise AuthFailure('wechat_qr_expired')
            time.sleep(1)
        else:
            raise AuthFailure('wechat_qr_expired')
    # Persist token rotation even if a subsequent protocol operation fails.
    store.save(expected.username, credentials)
    code = wechat.get_code(app_id=APP_ID, credentials=credentials)
    with school(None) as bootstrap:
        binding = bootstrap.auth.check_wechat_bind(code=code).data
    if not binding.bind or not binding.session_id:
        raise AuthFailure('wechat_school_not_bound')
    with school(binding.session_id) as mini:
        actual = mini.auth.mini_program_user_info().data.user_data
        if (actual.id, actual.username) != (expected.id, expected.username):
            raise AuthFailure('wechat_identity_mismatch')
        fresh = wechat.get_code(app_id=APP_ID, credentials=credentials)
        if fresh.get_secret_value() == code.get_secret_value():
            raise AuthFailure('wechat_code_reused')
        ticket = mini.auth.request_checkin_ticket(code=fresh).data.ticket.get_secret_value()
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', ticket):
        raise AuthFailure('wechat_ticket_invalid')
    emit('complete', ticket=ticket)


def school_transport(httpx2, inner=None):
    class AuthOnlyTransport(httpx2.BaseTransport):
        def __init__(self):
            self.inner = inner if inner is not None else httpx2.HTTPTransport(retries=0, trust_env=False)

        def handle_request(self, request):
            allowed = {('GET', '/daily-checkin/api/auth/user-info'),
                       ('GET', '/platform-app/api/auth/user-info'),
                       ('POST', '/platform-app/api/auth/check-bind'),
                       ('POST', '/platform-app/api/auth/checkin-ticket')}
            if (request.url.scheme != 'https' or request.url.host != 'jzsz.uestc.edu.cn'
                    or request.url.port not in (None, 443) or request.url.userinfo
                    or (request.method, request.url.path) not in allowed):
                raise AuthFailure('school_request_blocked')
            return self.inner.handle_request(request)

        def close(self):
            self.inner.close()

    return AuthOnlyTransport()


def run(config, emit):
    import httpx2
    from moon_uestc import MoonClient, WeChatClient
    from moon_uestc.wechat_models import WeChatCredentials

    def school(session):
        return MoonClient(session=session, timeout=15, transport=school_transport(httpx2))

    with WeChatClient(timeout=45, transport=httpx2.HTTPTransport(retries=0, trust_env=False)) as wechat:
        acquire(config, school, wechat, WeChatCredentials,
                CredentialStore(config['directory']), emit)


def main():
    os.umask(0o077)

    def emit(event, **values):
        print(json.dumps({'event': event, **values}), flush=True)

    try:
        run(json.loads(sys.stdin.readline()), emit)
        return 0
    except Exception as error:
        # Never serialize upstream exception messages or request/response bodies.
        reason = str(error) if isinstance(error, AuthFailure) else type(error).__name__
        emit('error', reason=reason)
        return 2


if __name__ == '__main__':
    sys.exit(main())
