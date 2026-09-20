"""Request-scoped import progress, shared by the desktop and browser transports."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import re
import threading
import time

_reporter = ContextVar("import_progress", default=None)


class Reporter:
    def __init__(self, callback, interval=0.1):
        self.callback, self.interval = callback, interval
        self.last, self.pending = None, None
        self.sent_at, self.sequence = 0, 0

    def send(self, event):
        self.sequence += 1
        self.last, self.pending = event, None
        self.sent_at = time.monotonic()
        # A disconnected progress consumer must not interrupt a catalog write.
        try:
            self.callback({**event, "sequence": self.sequence})
        except Exception:
            pass

    def update(self, event):
        changed = self.last is None or any(self.last.get(key) != event.get(key) for key in ("stage", "message", "total", "unit"))
        if changed and self.pending:
            self.send(self.pending)
        finished = event.get("total") is not None and event.get("completed") == event["total"]
        if changed or finished or time.monotonic() - self.sent_at >= self.interval:
            self.send(event)
        else:
            self.pending = event

    def finish(self, failed):
        self.send({**(self.pending or self.last or {}), "done": True, "failed": failed})


@contextmanager
def track(callback, interval=0.1):
    reporter = Reporter(callback, interval)
    token = _reporter.set(reporter)
    failed = True
    try:
        yield
        failed = False
    finally:
        reporter.finish(failed)
        _reporter.reset(token)


def report(stage, message, completed=None, total=None, unit=None, current=""):
    reporter = _reporter.get()
    if reporter is not None:
        reporter.update({"stage": stage, "message": message, "completed": completed,
                         "total": total, "unit": unit, "current": str(current)[:240]})


class ProgressStore:
    """Polling never takes the catalog mutex held by long-running imports."""
    def __init__(self, limit=64):
        self.limit, self.entries = limit, OrderedDict()
        self.lock = threading.Lock()

    @staticmethod
    def validate(operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", operation_id):
            raise ValueError("Invalid progress ID.")

    def update(self, operation_id, event):
        with self.lock:
            self.entries[operation_id] = event
            self.entries.move_to_end(operation_id)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)

    def get(self, operation_id):
        self.validate(operation_id)
        with self.lock:
            return dict(self.entries.get(operation_id, {}))

    @contextmanager
    def operation(self, operation_id):
        if operation_id is None:
            yield
            return
        self.validate(operation_id)
        with track(lambda event: self.update(operation_id, event)):
            report("starting", "Starting import…")
            yield
