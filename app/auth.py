"""Read-only hall authentication; never invokes a check-in action."""
import asyncio
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as BrowserTimeout

class Status(StrEnum):
    AUTHENTICATED = 'AUTHENTICATED'
    AUTH_REQUIRED = 'AUTH_REQUIRED'
    MANUAL_ACTION_REQUIRED = 'MANUAL_ACTION_REQUIRED'
    ERROR = 'ERROR'

@dataclass(frozen=True)
class Result:
    status: Status
    reason: str


def origin(url):
    u = urlsplit(url)
    return u.scheme, u.netloc


class AuthManager:
    # Live page has desktop/mobile forms sharing even the form ID. Use the visible one.
    username = '#pwdFromId #username:visible'
    password = '#pwdFromId #password:visible'
    submit = '#pwdFromId #login_submit:visible'
    challenge = ('#pwdFromId #captcha', '#sliderCaptchaDiv', '#dynamicCode',
                 'input[autocomplete="one-time-code"]')
    errors = '#pwdFromId #showErrorTip, #pwdFromId .item-error-tip'

    def __init__(self, config):
        self.config = config
        self.pause = config.profile.parent / 'auth-paused'

    def block(self, reason):
        # Written BEFORE credential submission: crash/timeout cannot cause a login loop.
        self.pause.write_text(reason + '\n', encoding='utf-8')
        self.pause.chmod(0o600)

    async def visible(self, page, selector):
        for loc in await page.locator(selector).all():
            if await loc.is_visible():
                return True
        return False

    async def authenticated(self, page):
        return (origin(page.url) == origin(self.config.hall_url)
                and await self.visible(page, self.config.success_selector))

    async def challenged(self, page):
        if any([await self.visible(page, s) for s in self.challenge]):
            return True
        # Secondary-auth products can change. Unknown forms never cause another submission.
        return await self.visible(page, 'text=/短信验证|二次认证|安全验证|人机验证/')

    async def wait_state(self, page, *, after_submit=False):
        end = asyncio.get_running_loop().time() + self.config.auth_wait
        while asyncio.get_running_loop().time() < end:
            if await self.authenticated(page):
                return 'authenticated'
            if await self.challenged(page):
                return 'challenge'
            if origin(page.url) == origin(self.config.login_origin):
                if after_submit:
                    if await self.visible(page, self.errors):
                        return 'rejected'
                elif await self.visible(page, self.username):
                    return 'login'
                elif await self.visible(page, '#userNameLogin_a'):
                    await page.locator('#userNameLogin_a:visible').click()
            await asyncio.sleep(0.2)
        return 'unknown'

    async def open_hall(self, page):
        # Only navigation is retried. Credential submission is never retried.
        for attempt in range(2):
            try:
                response = await page.goto(self.config.hall_url, wait_until='domcontentloaded')
                if response and response.status >= 400:
                    raise RuntimeError('http_error')
                return
            except BrowserTimeout:
                if attempt:
                    raise
                await asyncio.sleep(1)

    async def ensure_authenticated(self, page, *, check_only=False, manual=False):
        await self.open_hall(page)
        state = await self.wait_state(page)
        if state == 'authenticated':
            self.pause.unlink(missing_ok=True)
            return Result(Status.AUTHENTICATED, 'session_restored')
        if manual:
            # User completes login in this dedicated local headed browser.
            self.block('manual_session')
            end = asyncio.get_running_loop().time() + self.config.task_timeout
            while asyncio.get_running_loop().time() < end:
                if await self.authenticated(page):
                    self.pause.unlink(missing_ok=True)
                    return Result(Status.AUTHENTICATED, 'manual_login_completed')
                await asyncio.sleep(0.5)
            return Result(Status.MANUAL_ACTION_REQUIRED, 'manual_timeout')
        if state == 'challenge':
            self.block('verification_required')
            return Result(Status.MANUAL_ACTION_REQUIRED, 'verification_required')
        if self.pause.exists():
            return Result(Status.MANUAL_ACTION_REQUIRED, 'login_paused')
        if state != 'login':
            return Result(Status.ERROR, 'unrecognized_page')
        if check_only or not (self.config.username and self.config.password):
            return Result(Status.AUTH_REQUIRED, 'credentials_required')
        # Recheck trusted origin before entering each credential.
        if origin(page.url) != origin(self.config.login_origin):
            return Result(Status.ERROR, 'unexpected_origin')
        self.block('submission_pending_review')
        await page.locator(self.username).fill(self.config.username)
        if origin(page.url) != origin(self.config.login_origin):
            return Result(Status.ERROR, 'unexpected_origin')
        await page.locator(self.password).fill(self.config.password)
        if await self.challenged(page):
            return Result(Status.MANUAL_ACTION_REQUIRED, 'verification_required')
        if origin(page.url) != origin(self.config.login_origin):
            return Result(Status.ERROR, 'unexpected_origin')
        await page.locator(self.submit).click()
        state = await self.wait_state(page, after_submit=True)
        if state == 'authenticated':
            self.pause.unlink(missing_ok=True)
            return Result(Status.AUTHENTICATED, 'login_completed')
        return Result(Status.MANUAL_ACTION_REQUIRED,
                      'verification_required' if state == 'challenge' else 'login_unconfirmed')
