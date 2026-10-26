"""Observed UESTC H5 schema and optional protocol-based operation authorization."""
import asyncio
from datetime import datetime, time, timedelta
from urllib.parse import urlsplit

from .checkin import CheckinManager, CheckinResult
from .state import TZ, now
from .diagnostics import cleanup

TODAY_PATH = '/daily-checkin/api/student-checkin-record/today'
SCHEDULE_PATH = '/daily-checkin/api/checkin-schedule/effective'


def interpret_record(record, schedule, current):
    if not isinstance(record, dict):
        return CheckinResult('UNKNOWN', 'awaiting_today_record')
    if record.get('businessDate') != current.date().isoformat():
        return CheckinResult('UNKNOWN', 'business_date_mismatch')
    if record.get('abnormalFlag'):
        return CheckinResult('MANUAL_ACTION_REQUIRED', 'abnormal_record')
    state = record.get('recordStatusCode')
    if state == 'CHECKED_IN':
        value = record.get('checkinTime')
        try:
            checked_at = datetime.fromisoformat(value)
            checked_at = (checked_at.replace(tzinfo=TZ) if checked_at.tzinfo is None
                          else checked_at.astimezone(TZ))
            if checked_at.date() != current.date() or checked_at > current + timedelta(minutes=1):
                raise ValueError('invalid_time')
        except (TypeError, ValueError):
            return CheckinResult('UNKNOWN', 'completion_time_invalid')
        return CheckinResult('COMPLETED', 'today_record_verified', checked_at.isoformat())
    if state == 'EXEMPT':
        return CheckinResult('EXEMPT', 'exempt_record')
    if state in ('STUDENT_REPORTED', 'INSTRUCTOR_REPORTED'):
        return CheckinResult('REPORTED', 'reported_not_a_checkin')
    if state != 'PENDING':
        return CheckinResult('UNKNOWN', 'unrecognized_record_status')
    if not isinstance(schedule, dict) or schedule.get('effective') is not True:
        return CheckinResult('UNKNOWN', 'awaiting_effective_schedule')
    start, end = (schedule.get('studentCheckinStartOffsetMinute'),
                  schedule.get('studentCheckinEndOffsetMinute'))
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= 2880):
        return CheckinResult('UNKNOWN', 'schedule_invalid')
    midnight = datetime.combine(current.date(), time(), tzinfo=TZ)
    if current < midnight + timedelta(minutes=start):
        return CheckinResult('NOT_STARTED', 'checkin_window_not_open')
    if current >= midnight + timedelta(minutes=end):
        return CheckinResult('NOT_STARTED', 'checkin_window_closed')
    return CheckinResult('PENDING', 'today_record_pending')


class UestcCheckinManager(CheckinManager):
    def __init__(self, config, store):
        super().__init__(config, store)
        self.record = None
        self.schedule = None
        self.error = None
        self.generation = 0
        self.tasks = set()

    def invalidate_observation(self):
        self.generation += 1
        self.record = self.schedule = self.error = None

    async def capture(self, response, generation):
        actual = urlsplit(response.url)
        expected = urlsplit(self.config.hall_url)
        if ((actual.scheme, actual.netloc) != (expected.scheme, expected.netloc)
                or actual.path not in (TODAY_PATH, SCHEDULE_PATH)
                or response.request.method != 'GET'):
            return
        try:
            if generation != self.generation:
                return
            if response.status == 401:
                self.error = CheckinResult('AUTH_REQUIRED', 'business_auth_required')
                return
            payload = await response.json()
            if generation != self.generation:
                return
            if (response.status != 200 or not isinstance(payload, dict)
                    or payload.get('code') != 0 or payload.get('ok') is not True
                    or not isinstance(payload.get('data'), dict)):
                self.error = CheckinResult('UNKNOWN', 'business_response_rejected')
                return
            data = payload['data']
            if actual.path == TODAY_PATH:
                self.record = {key: data.get(key) for key in (
                    'businessDate', 'recordStatusCode', 'checkinTime', 'abnormalFlag')}
            else:
                self.schedule = {key: data.get(key) for key in (
                    'effective', 'studentCheckinStartOffsetMinute', 'studentCheckinEndOffsetMinute')}
        except Exception:
            if generation == self.generation:
                self.error = CheckinResult('UNKNOWN', 'business_response_unreadable')

    def observe(self, response):
        if urlsplit(response.url).path not in (TODAY_PATH, SCHEDULE_PATH):
            return
        task = asyncio.create_task(self.capture(response, self.generation))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def read_status(self, page):
        return self.error or interpret_record(self.record, self.schedule, now())

    async def run(self, page, *, submit=False):
        self.config.validate_checkin(submit=submit)
        context = page.context
        guard = None
        if submit and self.config.wechat_ticket_enabled:
            from .wechat import acquire_ticket, install_ticket
            ticket = await acquire_ticket(context, self.config)
            guard = await install_ticket(context, ticket)
        context.on('response', self.observe)
        try:
            return await super().run(page, submit=submit)
        finally:
            context.remove_listener('response', self.observe)
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            if guard:
                guard.ticket = None
                # This dedicated context is about to close. Unlike unroute(),
                # wait for active handlers before tearing down the browser.
                await cleanup('checkin_ticket_unroute',
                              lambda: context.unroute_all(behavior='wait'))
