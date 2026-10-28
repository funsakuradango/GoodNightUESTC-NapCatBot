"""Private, atomic overrides shared by CLI, scheduler and management service."""
from dataclasses import replace
import json
import stat

from .wechat_worker import atomic_private

KEYS = {'username', 'password', 'checkin_enabled'}


def read_settings(directory):
    path = directory / 'settings.json'
    if not path.exists():
        return {}
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('settings_permissions')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) - KEYS:
        raise ValueError('settings_invalid')
    for key, value in data.items():
        if key == 'checkin_enabled':
            if type(value) is not bool:
                raise ValueError('settings_invalid')
        elif not isinstance(value, str) or not value or len(value) > 512 or any(c in value for c in '\r\n\x00'):
            raise ValueError('settings_invalid')
    return data


def apply_settings(config):
    return replace(config, **read_settings(config.data_dir))


def save_settings(config, changes):
    """Caller holds task.lock; a blank password retains the current password."""
    if not isinstance(changes, dict) or set(changes) - KEYS:
        raise ValueError('unsupported_settings')
    current = apply_settings(config)
    data = read_settings(config.data_dir)
    for key, value in changes.items():
        if key == 'checkin_enabled':
            if type(value) is not bool:
                raise ValueError('invalid_enabled')
        else:
            if not isinstance(value, str) or len(value) > 512 or any(c in value for c in '\r\n\x00'):
                raise ValueError('invalid_credentials')
            if not value:
                continue
            if key == 'username':
                value = value.strip()
                if not value or (current.username and value != current.username):
                    raise ValueError('account_change_requires_separate_profile')
        data[key] = value
    if changes.get('checkin_enabled') is True:
        replace(config, **data).validate_checkin(submit=True)
    atomic_private(config.data_dir / 'settings.json', json.dumps(data, ensure_ascii=False).encode())
