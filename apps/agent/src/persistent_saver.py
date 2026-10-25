"""Conversation memory that survives agent restarts.

BoundedMemorySaver keeps threads in memory; this subclass also keeps a snapshot on the settings
volume, written by a background thread at most every couple of seconds and on shutdown, and loads
it at start. Each thread keeps only its last few checkpoints, and an evicted thread takes its
writes and blobs with it, so the file stays small (it holds only data this process serialized)."""

from __future__ import annotations

import logging
import os
import pickle
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from src.bounded_memory_saver import BoundedMemorySaver

logger = logging.getLogger(__name__)

KEEP_CHECKPOINTS = 6
FORMAT = 1


class _SafeUnpickler(pickle.Unpickler):
    """The snapshot holds only dicts, lists, tuples, str, bytes and numbers; refuse anything that
    would import code, so a tampered file cannot run it."""

    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"refused {module}.{name}")


class PersistentMemorySaver(BoundedMemorySaver):
    def __init__(self, path: Path, max_threads: int = 300, flush_every_s: float = 2.0):
        super().__init__(max_threads=max_threads)
        self.path = path
        self._lock = threading.RLock()
        self._dirty = False
        self._load()
        self._stop = threading.Event()
        self._flusher = threading.Thread(target=self._loop, args=(flush_every_s,), name="memory-flush", daemon=True)
        self._flusher.start()

    # --- persistence ---
    # The base class nests defaultdicts built from lambdas, which pickle cannot store: the snapshot
    # holds plain dicts and _load() refills the defaultdicts.
    def _load(self) -> None:
        try:
            with open(self.path, "rb") as f:
                data = _SafeUnpickler(f).load()
        except FileNotFoundError:
            return
        except Exception:
            logger.exception("conversation snapshot unreadable; starting empty")
            return
        if data.get("format") != FORMAT:
            return
        for thread, namespaces in data["storage"].items():
            for ns, checkpoints in namespaces.items():
                self.storage[thread][ns].update(checkpoints)
        for key, value in data["writes"].items():
            self.writes[key].update(value)
        self.blobs.update(data["blobs"])
        self._insertion_order = OrderedDict((t, None) for t in data["order"] if t in self.storage)
        logger.info("restored %d conversations", len(self.storage))

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data = {
                "format": FORMAT,
                # Threads that were only looked up (no checkpoint yet) are not worth keeping.
                "storage": {t: {ns: dict(cps) for ns, cps in nss.items() if cps}
                            for t, nss in list(self.storage.items()) if any(nss.values())},
                "writes": {k: dict(v) for k, v in list(self.writes.items()) if v},
                "blobs": dict(self.blobs),
                "order": list(self._insertion_order),
            }
            self._dirty = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".threads-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _loop(self, every: float) -> None:
        while not self._stop.wait(every):
            try:
                self.flush()
            except Exception:
                logger.exception("conversation snapshot write failed")

    def close(self) -> None:
        self._stop.set()
        self._flusher.join(timeout=10)  # let an in-flight write finish before the final one
        self.flush()

    # --- bookkeeping on every write ---
    def _prune(self, thread: str) -> None:
        """Keep the last few checkpoints of a thread, and only the channel values they point to.
        (Each version of the messages channel holds the whole conversation, so stale versions
        would make storage grow with the square of the conversation length.)"""
        for ns, checkpoints in self.storage.get(thread, {}).items():
            old_ids = sorted(checkpoints)[:-KEEP_CHECKPOINTS]  # ids sort by time
            if not old_ids:
                continue
            for old in old_ids:
                del checkpoints[old]
                self.writes.pop((thread, ns, old), None)
            live = set()
            for saved, _meta, _parent in checkpoints.values():
                versions = self.serde.loads_typed(saved).get("channel_versions", {})
                live.update((thread, ns, ch, v) for ch, v in versions.items())
            for key in [k for k in self.blobs if k[0] == thread and k[1] == ns and k not in live]:
                del self.blobs[key]

    def _drop_evicted(self) -> None:
        live = set(self.storage)
        for key in [k for k in self.writes if k[0] not in live]:
            del self.writes[key]
        for key in [k for k in self.blobs if k[0] not in live]:
            del self.blobs[key]

    def put(self, config, checkpoint, metadata, new_versions):
        with self._lock:
            before = set(self.storage)
            result = super().put(config, checkpoint, metadata, new_versions)
            self._prune(config["configurable"]["thread_id"])
            if before - set(self.storage):  # the base class evicted the oldest thread
                self._drop_evicted()
            self._dirty = True
            return result

    def put_writes(self, config, writes, task_id, task_path=""):
        with self._lock:
            result = super().put_writes(config, writes, task_id, task_path)
            self._dirty = True
            return result

    def delete_thread(self, thread_id):
        with self._lock:
            super().delete_thread(thread_id)
            self._insertion_order.pop(thread_id, None)
            self._dirty = True


def default_path() -> Path:
    return Path(os.environ.get("SETTINGS_DIR", "/data/settings")) / "threads.pkl"

