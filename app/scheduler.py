"""A single daily task in Asia/Shanghai; the browser exists only during a task."""
import subprocess
import sys
import time

from .state import StateStore, now


def serve(config):
    from .main import task_lock
    config.validate_checkin(submit=True)
    while True:
        current = now()
        date = current.date().isoformat()
        due = False
        try:
            with task_lock(config.data_dir / 'task.lock'):
                store = StateStore(config.data_dir)
                # Catch up only within the configured hour, once per calendar day.
                due = (current.hour == config.checkin_hour and current.minute >= config.checkin_minute
                       and store.data.get('scheduled_date') != date)
                if due:
                    store.data['scheduled_date'] = date
                    store.save()
        except BlockingIOError:
            pass
        if due:
            subprocess.run([sys.executable, '-m', 'app.main', 'run'], check=False)
        subprocess.run([sys.executable, '-m', 'app.main', 'flush-notifications'],
                       check=False, stdout=subprocess.DEVNULL)
        time.sleep(30)
