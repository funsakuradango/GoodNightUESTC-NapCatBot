import asyncio
from dataclasses import replace
from datetime import datetime
import json

import pytest
from app.demo import DemoSite, demo_config
from app.main import run_browser
from app.state import TZ
from app.uestc import interpret_record, TODAY_PATH, SCHEDULE_PATH

CURRENT = datetime(2026, 10, 21, 21, 5, tzinfo=TZ)
SCHEDULE = {'effective': True, 'studentCheckinStartOffsetMinute': 1260,
            'studentCheckinEndOffsetMinute': 1410}


def record(status='PENDING', **kwargs):
    return {'businessDate': '2026-10-21', 'recordStatusCode': status, **kwargs}


@pytest.mark.parametrize('current,expected', [
    (CURRENT.replace(hour=20), 'NOT_STARTED'),
    (CURRENT.replace(hour=21, minute=0), 'PENDING'),
    (CURRENT.replace(hour=23, minute=29), 'PENDING'),
    (CURRENT.replace(hour=23, minute=30), 'NOT_STARTED'),
])
def test_actual_schedule_boundaries(current, expected):
    assert interpret_record(record(), SCHEDULE, current).status == expected


def test_success_requires_backend_status_and_valid_today_timestamp():
    assert interpret_record(record('CHECKED_IN', checkinTime='2026-10-21 21:01:02'), SCHEDULE, CURRENT).status == 'COMPLETED'
    for timestamp in (None, '', '2026-10-20 21:01:02', '2026-10-21 23:00:00'):
        assert interpret_record(record('CHECKED_IN', checkinTime=timestamp), SCHEDULE, CURRENT).status == 'UNKNOWN'
    assert interpret_record(record(businessDate='2026-10-20'), SCHEDULE, CURRENT).reason == 'business_date_mismatch'
    assert interpret_record(record('STUDENT_REPORTED'), SCHEDULE, CURRENT).status == 'REPORTED'
    assert interpret_record(record('EXEMPT'), SCHEDULE, CURRENT).status == 'EXEMPT'
    assert interpret_record(record(abnormalFlag=True), SCHEDULE, CURRENT).status == 'MANUAL_ACTION_REQUIRED'


def test_observe_page_gets_without_submitting(tmp_path, monkeypatch):
    import app.uestc as uestc
    monkeypatch.setattr(uestc, 'now', lambda: CURRENT)

    async def scenario():
        cfg = replace(demo_config(tmp_path), checkin_adapter='uestc', pending_selector='',
                      completed_selector='', time_selector='', page_timeout=1)
        site = DemoSite()

        async def setup(context):
            await site.setup(context)

            async def page(r):
                await r.fulfill(content_type='text/html', body='''<div>Pending page</div>
                <script>fetch('/daily-checkin/api/student-checkin-record/today');
                fetch('/daily-checkin/api/checkin-schedule/effective');</script>''')

            async def data(r):
                payload = record() if r.request.url.endswith('/today') else SCHEDULE
                await r.fulfill(json={'code': 0, 'ok': True, 'data': payload})

            await context.route('https://hall.test/checkin', page)
            await context.route('https://hall.test/daily-checkin/api/**', data)

        await run_browser(cfg, 'login', setup_context=setup)
        result = await run_browser(cfg, 'status', setup_context=setup)
        assert result.status == 'PENDING'
        assert site.submissions == 0

    asyncio.run(scenario())


def test_page_confirmation_then_fresh_backend_verification(tmp_path, monkeypatch):
    import app.uestc as uestc
    monkeypatch.setattr(uestc, 'now', lambda: CURRENT)

    async def scenario():
        cfg = replace(demo_config(tmp_path), checkin_adapter='uestc',
                      submit_selector='.check-in-circle:not(.disabled):not(.abnormalFlag):has-text("请打卡")',
                      confirm_selector='.wd-message-box:visible:has(.wd-message-box__title:text-is("确认打卡")) .wd-message-box__actions-btn >> nth=-1')
        site = DemoSite()
        counts = {'submissions': 0, 'today_reads': 0}

        async def setup(context):
            await site.setup(context)

            async def page(r):
                await r.fulfill(content_type='text/html; charset=utf-8', body='''
                <div id="mount"></div>
                <div class="wd-message-box" hidden><div class="wd-message-box__title">确认打卡</div>
                <button class="wd-message-box__actions-btn">取消</button>
                <button class="wd-message-box__actions-btn" onclick="submitCheckin()">确定</button></div>
                <script>async function refresh(){await fetch('/daily-checkin/api/student-checkin-record/today');}
                async function submitCheckin(){await fetch('/mock-submit',{method:'POST'});await refresh();}
                refresh();fetch('/daily-checkin/api/checkin-schedule/effective');
                setTimeout(()=>{document.querySelector('#mount').innerHTML=`<uni-view class="check-in-circle" onclick="document.querySelector('.wd-message-box').hidden=false"><uni-view class="check-in-text-wrap"><uni-view>请打卡</uni-view></uni-view></uni-view>`},500);
                </script>''')

            async def data(r):
                if r.request.url.endswith('/today'):
                    counts['today_reads'] += 1
                    payload = record('CHECKED_IN', checkinTime='2026-10-21 21:05:00') if site.completed else record()
                else:
                    payload = SCHEDULE
                await r.fulfill(json={'code': 0, 'ok': True, 'data': payload})

            async def submit(r):
                counts['submissions'] += 1
                site.completed = True
                await r.fulfill(json={'code': 0, 'ok': True})

            await context.route('https://hall.test/checkin', page)
            await context.route('https://hall.test/daily-checkin/api/**', data)
            await context.route('https://hall.test/mock-submit', submit)

        first = await run_browser(cfg, 'run', setup_context=setup)
        second = await run_browser(cfg, 'run', setup_context=setup)
        assert first.status == second.status == 'COMPLETED'
        assert counts['submissions'] == 1
        assert counts['today_reads'] >= 4

    asyncio.run(scenario())


def test_business_401_html_is_auth_required(tmp_path):
    from app.uestc import UestcCheckinManager
    from app.state import StateStore
    from unittest.mock import AsyncMock

    async def scenario():
        cfg = demo_config(tmp_path)
        manager = UestcCheckinManager(cfg, StateStore(tmp_path))
        response = AsyncMock()
        response.url = 'https://hall.test' + TODAY_PATH
        response.status = 401
        response.request.method = 'GET'
        await manager.capture(response, 0)
        assert (await manager.read_status(None)).status == 'AUTH_REQUIRED'
        response.json.assert_not_awaited()
    asyncio.run(scenario())


def test_old_response_cannot_verify_after_reload(tmp_path):
    from app.uestc import UestcCheckinManager
    from app.state import StateStore
    from unittest.mock import AsyncMock

    async def scenario():
        manager = UestcCheckinManager(demo_config(tmp_path), StateStore(tmp_path))
        started, finish = asyncio.Event(), asyncio.Event()
        response = AsyncMock()
        response.url = 'https://hall.test' + TODAY_PATH
        response.status = 200
        response.request.method = 'GET'

        async def delayed():
            started.set()
            await finish.wait()
            return {'code': 0, 'ok': True, 'data': record('CHECKED_IN', checkinTime='2026-10-21 21:05:00')}

        response.json.side_effect = delayed
        task = asyncio.create_task(manager.capture(response, 0))
        await started.wait()
        manager.invalidate_observation()
        finish.set()
        await task
        assert manager.record is None
    asyncio.run(scenario())
