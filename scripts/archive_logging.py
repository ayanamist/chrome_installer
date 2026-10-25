"""Structured UTC stage logs shared by local and Actions executions."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import threading
import time

_CONTEXT = ContextVar('chrome_archive_log_context', default={})
_SENSITIVE = re.compile(r'token|secret|password|authorization|cookie', re.I)
_OUTPUT_LOCK = threading.Lock()


def _redact(value):
    if isinstance(value, dict):
        return {key: '<REDACTED>' if _SENSITIVE.search(str(key)) else _redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def log_event(event, **fields):
    record = {'timestamp': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
              'event': event, **_CONTEXT.get(), **fields}
    with _OUTPUT_LOCK:
        print(json.dumps(_redact(record), ensure_ascii=False, default=str), flush=True)


@contextmanager
def log_context(**fields):
    token = _CONTEXT.set({**_CONTEXT.get(), **fields})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def _cpu_snapshot():
    child = os.times()
    return time.process_time(), child.children_user + child.children_system


@contextmanager
def stage(name, heartbeat_seconds=30, **fields):
    common = {**_CONTEXT.get(), **fields, 'stage': name}
    stats = {}
    start = time.perf_counter()
    own_cpu, child_cpu = _cpu_snapshot()
    stop = threading.Event()

    def timing():
        own_now, child_now = _cpu_snapshot()
        return {'wall_seconds': round(time.perf_counter() - start, 3),
                'self_cpu_seconds': round(own_now - own_cpu, 3),
                'finished_children_cpu_seconds': round(child_now - child_cpu, 3)}

    def heartbeat():
        while not stop.wait(heartbeat_seconds):
            log_event('stage_heartbeat', **common, **timing())

    log_event('stage_start', **common)
    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    outcome = 'success'
    error = {}
    try:
        yield stats
    except BaseException as exc:
        outcome = 'error'
        error = {'error_type': type(exc).__name__, 'error': str(exc)[:1000]}
        raise
    finally:
        stop.set()
        worker.join(timeout=1)
        log_event('stage_end', **common, **stats, **timing(), status=outcome, **error)


def path_bytes(path):
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob('*')
               if not item.is_symlink() and item.is_file())


def log_runtime(**fields):
    quota = Path('/sys/fs/cgroup/cpu.max')
    log_event('runtime', python=platform.python_version(), os=platform.system(),
              machine=platform.machine(), cpu_count=os.cpu_count(),
              affinity_cpu_count=len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
              cgroup_cpu_max=quota.read_text().strip() if quota.is_file() else None,
              github_actions=os.environ.get('GITHUB_ACTIONS') == 'true', **fields)
