"""Small atomic JSON journal. Callers hold the process-wide task lock."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')


def now():
    return datetime.now(TZ)


class StateStore:
    def __init__(self, directory: Path):
        self.path = directory / 'state.json'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data.get('version') != 1:
                raise ValueError('unsupported_state')
        else:
            self.data = {'version': 1, 'days': {}, 'notifications': {}}

    def save(self):
        fd, name = tempfile.mkstemp(prefix='.state-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def day(self, date):
        return self.data['days'].get(date, {})

    def record(self, date, status, reason, checkin_time=''):
        self.data['days'][date] = {'status': status, 'reason': reason,
                                  'checkin_time': checkin_time, 'updated_at': now().isoformat()}
        self.save()

    def enqueue(self, date, result):
        key = f'{date}:{result.status}:{result.reason}'
        if key not in self.data['notifications']:
            labels = {'COMPLETED': '签到成功，已复查记录', 'PENDING': '尚未签到',
                      'NOT_STARTED': '签到暂未开放或按钮不可用', 'AUTH_REQUIRED': '需要登录',
                      'MANUAL_ACTION_REQUIRED': '登录需要人工处理', 'UNKNOWN': '结果未确认，请核查',
                      'ERROR': '任务异常，请检查服务日志'}
            labels.update({'EXEMPT': '今日免签到', 'REPORTED': '今日已填报情况，请核查，未作为签到成功'})
            text = f'晚安成电 {date}\n{labels.get(result.status, str(result.status))}'
            if getattr(result, 'checkin_time', ''):
                text += f'\n签到时间：{result.checkin_time}'
            self.data['notifications'][key] = {
                'text': text,
                'sent': False, 'attempts': 0}
            self.save()
