"""Configurable UI adapter. Production selectors must be verified on the real page."""
import asyncio
from dataclasses import dataclass
from playwright.async_api import expect, Error as BrowserError, TimeoutError as BrowserTimeout

from .auth import Result
from .state import now


@dataclass(frozen=True)
class CheckinResult(Result):
    checkin_time: str = ''


class CheckinManager:
    def __init__(self, config, store):
        self.config = config
        self.store = store

    async def navigate(self, page):
        if self.config.checkin_url:
            await page.goto(self.config.checkin_url, wait_until='domcontentloaded')
        else:
            previous = set(page.context.pages)
            await page.locator(self.config.entry_selector).click()
            # For popups, wait for either the new page or a configured status marker.
            end = asyncio.get_running_loop().time() + self.config.page_timeout
            while asyncio.get_running_loop().time() < end:
                new = [p for p in page.context.pages if p not in previous]
                if new:
                    page = new[-1]
                    await page.wait_for_load_state('domcontentloaded')
                    break
                scope = self.scope(page)
                if any([await self.visible(scope, s) for s in (
                        self.config.completed_selector, self.config.pending_selector,
                        self.config.not_started_selector)]):
                    break
                await asyncio.sleep(0.2)
        return page

    def scope(self, page):
        return page.frame_locator(self.config.frame_selector) if self.config.frame_selector else page

    async def visible(self, scope, selector):
        if not selector:
            return False
        return any([await item.is_visible() for item in await scope.locator(selector).all()])

    async def read_status(self, page):
        scope = self.scope(page)
        markers = [('COMPLETED', self.config.completed_selector),
                   ('NOT_STARTED', self.config.not_started_selector),
                   ('PENDING', self.config.pending_selector)]
        found = [status for status, selector in markers if await self.visible(scope, selector)]
        if len(found) != 1:
            return CheckinResult('UNKNOWN', 'ambiguous_or_unrecognized_page')
        if found[0] == 'COMPLETED':
            locator = scope.locator(self.config.time_selector)
            if await locator.count() != 1 or not await locator.is_visible():
                return CheckinResult('UNKNOWN', 'completion_time_missing')
            # This selector must identify today's record, not arbitrary historical text.
            value = (await locator.inner_text()).strip()
            if not value or len(value) > 100:
                return CheckinResult('UNKNOWN', 'completion_time_missing')
            return CheckinResult('COMPLETED', 'record_verified', value)
        return CheckinResult(found[0], 'page_state_observed')

    async def wait_status(self, page, *, completed_only=False):
        end = asyncio.get_running_loop().time() + self.config.page_timeout
        result = CheckinResult('UNKNOWN', 'page_not_ready')
        while asyncio.get_running_loop().time() < end:
            try:
                result = await self.read_status(page)
            except BrowserError:
                if page.is_closed():
                    raise
                # DOM queries can race a normal navigation following a click.
                await asyncio.sleep(0.2)
                continue
            if result.status == 'COMPLETED' or (not completed_only and result.status != 'UNKNOWN'):
                return result
            await asyncio.sleep(0.2)
        return result

    def invalidate_observation(self):
        """Adapters can require a fresh backend response after reloading."""
        pass

    async def run(self, page, *, submit=False):
        self.config.validate_checkin(submit=submit)
        page = await self.navigate(page)
        date = now().date().isoformat()
        result = await self.wait_status(page)
        if result.status == 'COMPLETED':
            self.store.record(date, result.status, result.reason, result.checkin_time)
            return result
        if not submit or result.status != 'PENDING':
            return result
        previous = self.store.day(date)
        if previous.get('status') in ('SUBMITTING', 'UNKNOWN', 'COMPLETED'):
            return CheckinResult('UNKNOWN', 'submission_blocked_check_record')
        scope = self.scope(page)
        button = scope.locator(self.config.submit_selector)
        # Backend responses can arrive before the H5 button finishes rendering.
        try:
            await button.wait_for(state='visible', timeout=self.config.page_timeout * 1000)
        except BrowserTimeout:
            return CheckinResult('NOT_STARTED', 'submit_unavailable')
        if await button.count() != 1 or not await button.is_visible():
            return CheckinResult('NOT_STARTED', 'submit_unavailable')
        try:
            await expect(button).to_be_enabled(timeout=self.config.page_timeout * 1000)
        except (AssertionError, BrowserTimeout):
            return CheckinResult('NOT_STARTED', 'submit_unavailable')
        self.store.record(date, 'SUBMITTING', 'awaiting_confirmation')
        try:
            await button.click()
            if self.config.confirm_selector:
                await scope.locator(self.config.confirm_selector).click()
            # A click or toast is insufficient. Reload the business page and read today's record.
            await self.wait_status(page, completed_only=True)
            self.invalidate_observation()
            await page.reload(wait_until='domcontentloaded')
            result = await self.wait_status(page)
            if result.status != 'COMPLETED':
                result = CheckinResult('UNKNOWN', 'submission_unconfirmed')
        except Exception:
            result = CheckinResult('UNKNOWN', 'submission_unconfirmed')
        self.store.record(date, result.status, result.reason, result.checkin_time)
        return result
