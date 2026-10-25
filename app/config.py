from dataclasses import dataclass, field
from pathlib import Path
import os
import math
import stat
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Config:
    profile: Path = ROOT / 'data/browser-profile'
    hall_url: str = 'https://jzsz.uestc.edu.cn/#/hall/home'
    login_origin: str = 'https://idas.uestc.edu.cn'
    success_selector: str = 'button:has-text("未读通知")'
    username: str = field(default='', repr=False)
    password: str = field(default='', repr=False)
    page_timeout: float = 25
    auth_wait: float = 30
    task_timeout: float = 120
    checkin_url: str = ''
    entry_selector: str = ''
    frame_selector: str = ''
    pending_selector: str = ''
    completed_selector: str = ''
    time_selector: str = ''
    not_started_selector: str = ''
    submit_selector: str = ''
    confirm_selector: str = ''
    checkin_enabled: bool = False
    checkin_adapter: str = 'generic'
    wechat_ticket_enabled: bool = False
    wechat_python: str = ''
    latitude: float | None = None
    longitude: float | None = None
    accuracy: float = 30
    checkin_hour: int = 21
    checkin_minute: int = 5
    napcat_url: str = ''
    napcat_token: str = field(default='', repr=False)
    qq_user_id: str = field(default='', repr=False)

    @property
    def data_dir(self):
        return self.profile.parent

    @property
    def geolocation(self):
        if self.latitude is None:
            return None
        return {'latitude': self.latitude, 'longitude': self.longitude, 'accuracy': self.accuracy}

    def validate_checkin(self, *, submit=False):
        if not (self.checkin_url or self.entry_selector):
            raise ValueError('checkin_navigation_not_configured')
        if self.checkin_adapter not in ('generic', 'uestc'):
            raise ValueError('unknown_checkin_adapter')
        if self.checkin_adapter == 'generic' and not all((self.pending_selector, self.completed_selector, self.time_selector)):
            raise ValueError('checkin_markers_not_configured')
        if submit and not (self.checkin_enabled and self.submit_selector):
            raise ValueError('checkin_submission_not_enabled')
        if submit and self.checkin_adapter == 'uestc' and self.geolocation is None:
            raise ValueError('checkin_geolocation_not_configured')

    @classmethod
    def load(cls):
        env = ROOT / '.env'
        if env.exists():
            if stat.S_IMODE(env.stat().st_mode) & 0o077:
                raise ValueError('env_permissions')
            load_dotenv(env)
        if os.getenv('TZ', 'Asia/Shanghai') != 'Asia/Shanghai':
            raise ValueError('timezone')
        values = {name: float(os.getenv(key, default)) for name, key, default in (
            ('page_timeout', 'PAGE_TIMEOUT_SECONDS', '25'),
            ('auth_wait', 'AUTH_WAIT_SECONDS', '30'),
            ('task_timeout', 'TASK_TIMEOUT_SECONDS', '120'))}
        if any(not math.isfinite(v) or not 1 <= v <= 600 for v in values.values()):
            raise ValueError('timeout_range')
        selector = os.getenv('AUTH_SUCCESS_SELECTOR', cls.success_selector).strip()
        if not selector:
            raise ValueError('success_selector')
        fields = {name: os.getenv(key, '').strip() for name, key in (
            ('checkin_url', 'CHECKIN_URL'), ('entry_selector', 'CHECKIN_ENTRY_SELECTOR'),
            ('frame_selector', 'CHECKIN_FRAME_SELECTOR'), ('pending_selector', 'CHECKIN_PENDING_SELECTOR'),
            ('completed_selector', 'CHECKIN_COMPLETED_SELECTOR'), ('time_selector', 'CHECKIN_TIME_SELECTOR'),
            ('not_started_selector', 'CHECKIN_NOT_STARTED_SELECTOR'), ('submit_selector', 'CHECKIN_SUBMIT_SELECTOR'),
            ('confirm_selector', 'CHECKIN_CONFIRM_SELECTOR'), ('napcat_url', 'NAPCAT_HTTP_URL'),
            ('napcat_token', 'NAPCAT_TOKEN'), ('qq_user_id', 'QQ_USER_ID'))}
        enabled = os.getenv('CHECKIN_ENABLED', 'false').lower()
        if enabled not in ('true', 'false'):
            raise ValueError('checkin_enabled')
        ticket_enabled = os.getenv('WECHAT_TICKET_ENABLED', 'true').lower()
        if ticket_enabled not in ('true', 'false'):
            raise ValueError('wechat_ticket_enabled')
        lat, lon = os.getenv('GEO_LATITUDE', ''), os.getenv('GEO_LONGITUDE', '')
        if bool(lat) != bool(lon):
            raise ValueError('coordinate_pair')
        latitude, longitude = (float(lat), float(lon)) if lat else (None, None)
        if lat and not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError('coordinate_range')
        accuracy = float(os.getenv('GEO_ACCURACY_METERS', '30'))
        if not math.isfinite(accuracy) or accuracy < 0:
            raise ValueError('accuracy')
        hour, minute = int(os.getenv('CHECKIN_HOUR') or '21'), int(os.getenv('CHECKIN_MINUTE') or '5')
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError('schedule')
        if bool(fields['napcat_url']) != bool(fields['qq_user_id']):
            raise ValueError('notification_configuration_incomplete')
        if fields['qq_user_id'] and not fields['qq_user_id'].isdigit():
            raise ValueError('qq_user_id')
        return cls(username=os.getenv('UESTC_USERNAME', ''),
                   password=os.getenv('UESTC_PASSWORD', ''),
                   success_selector=selector, checkin_enabled=enabled == 'true',
                   checkin_adapter=os.getenv('CHECKIN_ADAPTER', 'generic').strip(),
                   wechat_ticket_enabled=ticket_enabled == 'true',
                   wechat_python=os.getenv('WECHAT_PYTHON', '').strip(),
                   latitude=latitude, longitude=longitude, accuracy=accuracy,
                   checkin_hour=hour, checkin_minute=minute, **fields, **values)
