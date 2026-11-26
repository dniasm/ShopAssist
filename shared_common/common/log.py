"""Shared logging: one format, a request id per trace, daily files, an audit file, secrets masked."""
import json
import logging
import os
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Callable

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_service = "app"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATE_IN_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")

_MASKS = (
    (re.compile(r"gsk_[A-Za-z0-9]{10,}"), "gsk_***"),
    (re.compile(r"pa-[A-Za-z0-9_-]{20,}"), "pa-***"),
    (re.compile(r"(postgres(?:ql)?://[^:/@\s]+):[^@\s]+@"), r"\1:***@"),
    (re.compile(r"""(?i)\b(password|passwd|token|secret|api_key|authorization)(["']?\s*[=:]\s*["']?)[^\s"',}]+"""),
     r"\1\2***"),
)


def mask(text: str) -> str:
    """Hide API keys, DB passwords and password/token-like values in a log line."""
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    return text


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


class DailyFileHandler(logging.Handler):
    """Appends to <directory>/<pattern with {date}>, a new file each local day; old files are pruned on each open."""

    def __init__(self, directory: Path, pattern: str, retention_days: int,
                 today: Callable[[], date] = date.today) -> None:
        super().__init__()
        self.directory = Path(directory)
        self.pattern = pattern
        self.retention_days = retention_days
        self._today = today
        self._day: date | None = None
        self._stream = None

    def _open(self, day: date) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self.directory.mkdir(parents=True, exist_ok=True)
        self._stream = open(self.directory / self.pattern.format(date=day.isoformat()), "a", encoding="utf-8")
        self._day = day
        self._prune(day)

    def _prune(self, today: date) -> None:
        if self.retention_days <= 0:
            return
        for path in self.directory.glob("*.log"):
            found = _DATE_IN_NAME.search(path.name)
            if not found:
                continue
            try:
                if (today - date.fromisoformat(found.group())).days > self.retention_days:
                    path.unlink()
            except (ValueError, OSError):
                pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = self._today()
            if self._stream is None or day != self._day:
                self._open(day)
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


class _Context(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.service = _service
        record.request_id = _request_id.get()
        return True


class _AuditOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == "audit"


def setup_logging(service: str) -> None:
    """Call once in each entry point (main.py, worker.py)."""
    global _service
    _service = service

    formatter = MaskingFormatter(
        "%(asctime)s %(levelname)-7s [%(service)s] [%(request_id)s] %(name)s: %(message)s",
        "%H:%M:%S",
    )
    log_dir = Path(os.getenv("LOG_DIR") or _REPO_ROOT / "logs")
    retention = int(os.getenv("LOG_RETENTION_DAYS") or 30)

    console = logging.StreamHandler(sys.stdout)
    service_file = DailyFileHandler(log_dir / service, "{date}.log", retention)
    audit_file = DailyFileHandler(log_dir / "audit", f"{service}-{{date}}.log", 0)
    audit_file.addFilter(_AuditOnly())
    handlers = [console, service_file, audit_file]
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_Context())

    for old in logging.getLogger().handlers:
        if isinstance(old, DailyFileHandler):
            old.close()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        handlers=handlers,
        force=True,          # replaces any earlier basicConfig
    )
    logging.getLogger("audit").setLevel(logging.INFO)   # audit lines are kept whatever LOG_LEVEL is


def set_request_id(value: str | None = None) -> str:
    """Start a trace (no argument) or continue one (pass the incoming ID)."""
    rid = value or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


def audit(event: str, **fields) -> None:
    """Record a business event: `event key=value ...` (values with spaces are JSON-quoted)."""
    parts = [event]
    for key, value in fields.items():
        text = str(value)
        if not text or re.search(r"[\s\"]", text):
            text = json.dumps(text)
        parts.append(f"{key}={text}")
    logging.getLogger("audit").info(" ".join(parts))
