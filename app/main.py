import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
import signal
import subprocess
import sys

from .config import Config
from .auth import AuthManager, Result, Status


def failure_result(error, command):
    """Return fixed diagnostics, never raw exception strings containing page data."""
    from playwright.async_api import TimeoutError as BrowserTimeout, Error as BrowserError
    from .wechat import WeChatFailure
    if isinstance(error, WeChatFailure):
        reasons = {'school_session_required', 'school_identity_mismatch', 'wechat_account_changed',
                   'wechat_authorization_required', 'wechat_qr_expired', 'wechat_school_not_bound',
                   'wechat_identity_mismatch', 'wechat_code_reused', 'wechat_ticket_invalid',
                   'wechat_worker_incomplete', 'WeChatAuthorizationError', 'WeChatProtocolError',
                   'AuthenticationError', 'CheckinTicketError'}
        reason = str(error)
        return Result(Status.MANUAL_ACTION_REQUIRED,
                      reason if reason in reasons else 'wechat_authorization_failed')
    if isinstance(error, (TimeoutError, BrowserTimeout)):
        return Result(Status.MANUAL_ACTION_REQUIRED if command == 'manual-login' else Status.ERROR,
                      'manual_timeout' if command == 'manual-login' else 'task_or_page_timeout')
    if isinstance(error, ValueError):
        return Result(Status.ERROR, 'configuration_or_state_invalid')
    if isinstance(error, BrowserError):
        for code in ('ERR_NAME_NOT_RESOLVED', 'ERR_CONNECTION_REFUSED', 'ERR_CONNECTION_RESET',
                     'ERR_TIMED_OUT', 'ERR_CERT_AUTHORITY_INVALID', 'ERR_NETWORK_CHANGED'):
            if code in str(error):
                return Result(Status.ERROR, code.lower())
        return Result(Status.ERROR, 'browser_operation_failed')
    return Result(Status.ERROR, 'operation_failed')


def emit(result):
    # Never print exception messages, page content, URLs, credentials or browser console.
    print(json.dumps({'status': result.status, 'reason': result.reason}), flush=True)


@contextmanager
def task_lock(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


async def run_browser(config, command, *, setup_context=None):
    from playwright.async_api import async_playwright
    from .geo import configure_geolocation
    from .checkin import CheckinManager
    from .state import StateStore
    if command in ('status', 'run'):
        config.validate_checkin(submit=command == 'run')
    config.profile.mkdir(parents=True, mode=0o700, exist_ok=True)
    config.profile.chmod(0o700)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(config.profile), headless=command != 'manual-login',
            timezone_id='Asia/Shanghai', accept_downloads=False,
            service_workers='block',
            permissions=[], chromium_sandbox=True,
            # Debug-only endpoint stays inside this container; no host port mapping.
            args=['--remote-debugging-port=9222', '--remote-debugging-address=127.0.0.1']
                 if command == 'manual-login' else [],
        )
        try:
            await configure_geolocation(context, config)
            if setup_context:
                await setup_context(context)
            context.set_default_timeout(config.page_timeout * 1000)
            context.set_default_navigation_timeout(config.page_timeout * 1000)
            page = context.pages[0] if context.pages else await context.new_page()
            async with asyncio.timeout(max(config.task_timeout, 360) if command == 'wechat-authorize' else config.task_timeout):
                result = await AuthManager(config).ensure_authenticated(
                    page, check_only=command in ('check', 'status', 'wechat-authorize', 'wechat-ticket'),
                    manual=command == 'manual-login')
                if result.status == Status.AUTHENTICATED and command in ('wechat-authorize', 'wechat-ticket'):
                    from .wechat import acquire_ticket
                    await acquire_ticket(context, config, allow_qr=command == 'wechat-authorize')
                    return Result(Status.AUTHENTICATED, 'wechat_ticket_verified_no_submission')
                if result.status == Status.AUTHENTICATED and command in ('status', 'run'):
                    manager = CheckinManager
                    if config.checkin_adapter == 'uestc':
                        from .uestc import UestcCheckinManager
                        manager = UestcCheckinManager
                    return await manager(config, StateStore(config.data_dir)).run(
                        page, submit=command == 'run')
                return result
        finally:
            await context.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'login', 'manual-login', 'resume', 'status', 'run',
                                             'flush-notifications', 'schedule', 'demo',
                                             'wechat-authorize', 'wechat-ticket'])
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.command == 'demo':
            from .demo import run_demo
            print(json.dumps(asyncio.run(run_demo()), ensure_ascii=False), flush=True)
            return 0
        config = Config.load()
        if args.command == 'schedule':
            from .scheduler import serve
            with task_lock(config.data_dir / 'scheduler.lock'):
                serve(config)
            return 0
        if args.worker:
            try:
                result = asyncio.run(run_browser(config, args.command))
            except Exception as error:
                result = failure_result(error, args.command)
            notification = 'disabled'
            if args.command in ('run', 'status'):
                from .state import StateStore, now
                from .notify import NotificationManager
                store = StateStore(config.data_dir)
                if config.napcat_url and config.qq_user_id:
                    store.enqueue(now().date().isoformat(), result)
                    notification = asyncio.run(NotificationManager(config).flush(store))
            output = {'status': result.status, 'reason': result.reason, 'notification': notification}
            if getattr(result, 'checkin_time', ''):
                output['checkin_time'] = result.checkin_time
            print(json.dumps(output, ensure_ascii=False), flush=True)
            return 0 if result.status in (Status.AUTHENTICATED, 'COMPLETED', 'PENDING', 'NOT_STARTED', 'EXEMPT', 'REPORTED') else 2
        with task_lock(config.profile.parent / 'task.lock'):
            if args.command == 'flush-notifications':
                from .state import StateStore
                from .notify import NotificationManager
                state = asyncio.run(NotificationManager(config).flush(StateStore(config.data_dir)))
                print(json.dumps({'notification': state}), flush=True)
                return 2 if state == 'pending' else 0
            if args.command == 'resume':
                (config.profile.parent / 'auth-paused').unlink(missing_ok=True)
                emit(Result(Status.AUTH_REQUIRED, 'login_unpaused'))
                return 0
            # Bound the entire process group, including Chromium and Playwright driver.
            proc = subprocess.Popen(
                [sys.executable, '-m', 'app.main', args.command, '--worker'],
                start_new_session=True, stderr=subprocess.DEVNULL)
            def stop_worker(signum, frame):
                raise KeyboardInterrupt
            previous = signal.signal(signal.SIGTERM, stop_worker)
            try:
                budget = max(config.task_timeout, 360) if args.command == 'wechat-authorize' else config.task_timeout
                return proc.wait(timeout=budget + 15)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                emit(Result(Status.ERROR, 'task_interrupted_or_timeout'))
                return 3
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                signal.signal(signal.SIGTERM, previous)
    except BlockingIOError:
        emit(Result(Status.ERROR, 'task_already_running'))
        return 4
    except Exception:
        emit(Result(Status.ERROR, 'operation_failed'))
        return 3


if __name__ == '__main__':
    sys.exit(main())
