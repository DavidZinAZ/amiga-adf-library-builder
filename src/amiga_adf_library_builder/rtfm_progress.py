"""Thread-local document progress routed to the pipeline's live activity log."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time

_current = ContextVar("rtfm_progress", default=None)


def report(message: str) -> None:
    reporter = _current.get()
    if reporter is not None:
        reporter.emit(message)


class _Reporter:
    def __init__(self, activity, interval):
        self.activity = activity
        self.interval = interval
        self.lock = threading.Lock()
        self.last = "discovering manual sources"
        self.since = time.monotonic()
        self.stopped = threading.Event()

    def emit(self, message):
        with self.lock:
            self.last, self.since = message, time.monotonic()
        self.send(message)

    def send(self, message):
        try:
            self.activity("RTFM: " + message)
        except Exception:
            pass  # Progress must never prevent document extraction.

    def heartbeat(self):
        while not self.stopped.wait(self.interval):
            with self.lock:
                message, elapsed = self.last, time.monotonic() - self.since
            if elapsed >= self.interval:
                self.send(f"still working — {message} ({elapsed:.0f}s since last update)")


@contextmanager
def progress_scope(activity, interval=10.0):
    if activity is None:
        yield
        return
    reporter = _Reporter(activity, interval)
    token = _current.set(reporter)
    thread = threading.Thread(target=reporter.heartbeat, daemon=True, name="rtfm-progress")
    thread.start()
    try:
        yield
    finally:
        reporter.stopped.set()
        thread.join(timeout=1)
        _current.reset(token)
