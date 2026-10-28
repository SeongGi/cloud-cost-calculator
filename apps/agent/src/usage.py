"""Usage statistics for the admin page: one sqlite table of events on the settings volume.

Only metadata is stored: a time, an event kind, HMAC hashes of the visitor IP and the thread id,
and fixed strings (cloud, result state, price-list service ids). Never a question, a spec, an
answer or a raw IP. Recording never raises and never blocks an answer for long."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pricing.engine import CLOUDS, RESULT_STATES, SERVICE_LABEL

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))  # fixed offset: Korea has no daylight saving time
KINDS = frozenset({"question", "limited", "estimate", "estimate_peer", "answer_failed", "answer_empty", "busy"})
STATES = frozenset(RESULT_STATES | {"invalid_arguments", "error"})
SERVICE_ID = re.compile(r"^[a-z0-9_]{1,40}$")
PERIODS = (7, 30, 90)
RETENTION_S = 400 * 86400
PRUNE_EVERY_S = 86400
LOCK_TIMEOUT_S = 0.5
# One row must not grow without bound, whatever a (forged) result lists.
MAX_SERVICES = 20
SALT_BYTES = 32

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  ts INTEGER NOT NULL,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  visitor TEXT,
  thread TEXT,
  cloud TEXT,
  state TEXT,
  services TEXT
);
CREATE INDEX IF NOT EXISTS events_day ON events(day, kind);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_dir: Path | None = None
_salt: bytes | None = None
_salt_dir: Path | None = None
_last_prune = 0.0
_last_warn = float("-inf")


def _reset() -> None:
    """Forget the open connection and salt (tests switch SETTINGS_DIR between cases)."""
    global _conn, _conn_dir, _salt, _salt_dir, _last_prune, _last_warn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
        _conn = _conn_dir = _salt = _salt_dir = None
        _last_prune, _last_warn = 0.0, float("-inf")


def _dir() -> Path:
    return Path(os.environ.get("SETTINGS_DIR", "/data/settings"))


def _warn(message: str, *args) -> None:
    # A failing disk would otherwise log on every tool call.
    global _last_warn
    if time.monotonic() - _last_warn > 60:
        _last_warn = time.monotonic()
        logger.warning(message, *args)


def _write_salt(path: Path, replace: bool) -> bytes:
    """Write a complete salt to a temp file first, so no reader ever sees a partial one: link()
    publishes it only if none exists, replace() swaps a broken one."""
    salt = os.urandom(SALT_BYTES)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".usage-salt-")
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(salt)
            f.flush()
            os.fsync(f.fileno())
        if replace:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return salt


def _get_salt(directory: Path) -> bytes:
    global _salt, _salt_dir
    if _salt is not None and _salt_dir == directory:
        return _salt
    path = directory / "usage-salt"
    try:
        salt = _write_salt(path, replace=False)
    except FileExistsError:
        salt = path.read_bytes()
        if len(salt) != SALT_BYTES:
            _warn("usage salt has %d bytes instead of %d; creating a new one", len(salt), SALT_BYTES)
            salt = _write_salt(path, replace=True)
    _salt, _salt_dir = salt, directory
    return salt


def _hash(salt: bytes, value: str | None) -> str | None:
    if not value:
        return None
    return hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()[:16]


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d")


def _prune(conn: sqlite3.Connection, now: float) -> None:
    global _last_prune
    # By day, so the events_day index serves it.
    conn.execute("DELETE FROM events WHERE day < ?", (_day(now - RETENTION_S),))
    conn.commit()
    _last_prune = now


def _connect() -> sqlite3.Connection:
    """The shared connection, opened on first use (call with the lock held). It does not prune:
    the first blocking record does, so a restart never prunes on the event loop."""
    global _conn, _conn_dir
    directory = _dir()
    if _conn is not None and _conn_dir == directory:
        return _conn
    if _conn is not None:
        _conn.close()
        _conn = _conn_dir = None
    path = directory / "usage.sqlite3"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    conn = sqlite3.connect(path, check_same_thread=False, timeout=1.0)
    try:
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
    except BaseException:
        conn.close()
        raise
    _conn, _conn_dir = conn, directory
    return conn


def _acquire(blocking: bool) -> bool:
    return _lock.acquire(timeout=LOCK_TIMEOUT_S) if blocking else _lock.acquire(blocking=False)


def _services(value: str | Iterable[str] | None) -> str:
    raw = value.split(",") if isinstance(value, str) else list(value or [])
    ids = {s if isinstance(s, str) and SERVICE_ID.fullmatch(s) else "unknown" for s in raw}
    return ",".join(sorted(ids)[:MAX_SERVICES]) or "unknown"


def current_thread_id() -> str | None:
    """The conversation id of the run this code executes in, or None outside a run."""
    try:
        from langgraph.config import get_config

        thread = (get_config().get("configurable") or {}).get("thread_id")
        return str(thread)[:200] if thread else None
    except Exception:
        return None


def record(kind: str, *, ip: str | None = None, thread_id: str | None = None, cloud: str | None = None,
           state: str | None = None, services: str | Iterable[str] | None = None, now: float | None = None,
           blocking: bool = True) -> None:
    """Store one event. Never raises. Callers on the event loop pass blocking=False: on lock
    contention the event is dropped rather than stalling every request."""
    try:
        if kind not in KINDS:
            return
        ts = time.time() if now is None else now
        is_estimate = kind in ("estimate", "estimate_peer")
        row_cloud = row_state = row_services = None
        if is_estimate:
            row_cloud = cloud if cloud in CLOUDS else ("other" if cloud else None)
            row_state = state if state in STATES else "other"
            row_services = _services(services)
        if not _acquire(blocking):
            return
        try:
            conn = _connect()
            hashed_ip = ip if kind in ("question", "limited") else None
            # Only rows that carry an identifier need the salt, so a salt problem
            # cannot stop busy or estimate records.
            salt = _get_salt(_dir()) if hashed_ip or thread_id else b""
            visitor = _hash(salt, hashed_ip)
            thread = _hash(salt, thread_id)
            # Pruning is a heavy write: not on the event loop (blocking=False callers).
            if blocking and ts - _last_prune >= PRUNE_EVERY_S:
                _prune(conn, ts)
            conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                         (int(ts), _day(ts), kind, visitor, thread, row_cloud, row_state, row_services))
            conn.commit()
        finally:
            _lock.release()
    except Exception as exc:  # the answer to the visitor matters more than the statistics
        _warn("usage record skipped: kind=%s error=%s", kind, type(exc).__name__)


def _empty(days: int, today: datetime) -> dict:
    first = today - timedelta(days=days - 1)
    return {
        "days": days, "from": first.strftime("%Y-%m-%d"), "to": today.strftime("%Y-%m-%d"),
        "totals": {"questions": 0, "limited": 0, "visitors": 0, "limited_visitors": 0, "conversations": 0,
                   "estimates": 0, "estimate_errors": 0, "answer_failures": 0, "busy": 0},
        "daily": [{"day": (first + timedelta(days=i)).strftime("%Y-%m-%d"), "questions": 0, "visitors": 0}
                  for i in range(days)],
        "clouds": {c: 0 for c in CLOUDS},
        "services": [],
        "all_time": {"questions": 0, "visitors": 0, "since": None},
    }


def summary(days: int, now: float | None = None) -> dict:
    """Counts for the last `days` KST days (today included). estimate_peer rows, the VPN the
    system adds on the other end of a link, count nowhere."""
    if days not in PERIODS:
        raise ValueError(f"days must be one of {PERIODS}")
    ts = time.time() if now is None else now
    result = _empty(days, datetime.fromtimestamp(ts, KST))
    lo, hi = result["from"], result["to"]
    path = _dir() / "usage.sqlite3"
    if not path.exists():
        return result
    conn = None
    try:
        # Its own read-only connection: under WAL it neither blocks nor is blocked by writers.
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1.0)
        span = (lo, hi)
        one = lambda sql, *args: conn.execute(sql, args).fetchone()[0]  # noqa: E731
        t = result["totals"]
        t["questions"] = one("SELECT COUNT(*) FROM events WHERE kind='question' AND day BETWEEN ? AND ?", *span)
        t["limited"] = one("SELECT COUNT(*) FROM events WHERE kind='limited' AND day BETWEEN ? AND ?", *span)
        t["visitors"] = one("SELECT COUNT(DISTINCT visitor) FROM events WHERE kind='question' "
                            "AND day BETWEEN ? AND ?", *span)
        t["limited_visitors"] = one("SELECT COUNT(DISTINCT visitor) FROM events WHERE kind='limited' "
                                    "AND day BETWEEN ? AND ?", *span)
        t["conversations"] = one("SELECT COUNT(DISTINCT thread) FROM events WHERE kind='question' "
                                 "AND day BETWEEN ? AND ?", *span)
        t["estimates"] = one("SELECT COUNT(*) FROM events WHERE kind='estimate' AND day BETWEEN ? AND ?", *span)
        t["estimate_errors"] = one("SELECT COUNT(*) FROM events WHERE kind='estimate' "
                                   "AND COALESCE(state,'')!='ok' AND day BETWEEN ? AND ?", *span)
        t["answer_failures"] = one("SELECT COUNT(*) FROM events WHERE kind IN ('answer_failed','answer_empty') "
                                   "AND day BETWEEN ? AND ?", *span)
        t["busy"] = one("SELECT COUNT(*) FROM events WHERE kind='busy' AND day BETWEEN ? AND ?", *span)
        per_day = {d: (q, v) for d, q, v in conn.execute(
            "SELECT day, COUNT(*), COUNT(DISTINCT visitor) FROM events WHERE kind='question' "
            "AND day BETWEEN ? AND ? GROUP BY day", span)}
        for row in result["daily"]:
            row["questions"], row["visitors"] = per_day.get(row["day"], (0, 0))
        for cloud, n in conn.execute("SELECT cloud, COUNT(*) FROM events WHERE kind='estimate' "
                                     "AND day BETWEEN ? AND ? GROUP BY cloud", span):
            if cloud in result["clouds"]:
                result["clouds"][cloud] = n
        counts: dict[str, int] = {}
        for services, n in conn.execute("SELECT services, COUNT(*) FROM events WHERE kind='estimate' "
                                        "AND day BETWEEN ? AND ? GROUP BY services", span):
            for sid in (services or "unknown").split(","):
                counts[sid] = counts.get(sid, 0) + n
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        result["services"] = [{"service": s, "label": SERVICE_LABEL.get(s, s), "count": n} for s, n in top]
        since = conn.execute("SELECT MIN(day) FROM events").fetchone()[0]
        if since:
            q, v = conn.execute("SELECT COUNT(*), COUNT(DISTINCT visitor) FROM events "
                                "WHERE kind='question'").fetchone()
            result["all_time"] = {"questions": q, "visitors": v, "since": since}
    except Exception as exc:
        _warn("usage summary failed: error=%s", type(exc).__name__)
        return _empty(days, datetime.fromtimestamp(ts, KST))
    finally:
        if conn is not None:
            conn.close()
    return result
