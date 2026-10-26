"""Stage diagnostics without exception messages, URLs, or local variable values."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import traceback


def report_error(stage, error):
    frames = [{'file': Path(frame.filename).name, 'line': frame.lineno,
               'function': frame.name}
              for frame in traceback.extract_tb(error.__traceback__)]
    print(json.dumps({'event': 'operation_error', 'stage': stage,
                      'error_type': type(error).__name__, 'frames': frames}), flush=True)


async def cleanup(stage, operation):
    # Cleanup must not replace a verified business result or the original error.
    try:
        async with asyncio.timeout(5):
            await operation()
    except Exception as error:
        report_error(stage, error)


@asynccontextmanager
async def browser_runtime():
    from playwright.async_api import async_playwright
    runtime = await async_playwright().start()
    try:
        yield runtime
    finally:
        await cleanup('playwright_stop', runtime.stop)
