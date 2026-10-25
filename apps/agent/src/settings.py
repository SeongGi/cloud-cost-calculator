"""LLM settings an admin changes at runtime (Gemini key and model), kept in a 0600 JSON file on a
volume. The key never leaves the agent except masked."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
_lock = threading.Lock()


@dataclass(frozen=True)
class LLMSettings:
    gemini_api_key: str = ""
    gemini_model: str = DEFAULT_GEMINI_MODEL
    gemini_enabled: bool = True

    @property
    def gemini_active(self) -> bool:
        return self.gemini_enabled and bool(self.gemini_api_key)

    def public(self) -> dict:
        """What the admin page may see: never the key itself."""
        key = self.gemini_api_key
        return {
            "gemini_key_set": bool(key),
            "gemini_key_hint": f"…{key[-4:]}" if len(key) >= 8 else "",
            "gemini_model": self.gemini_model,
            "gemini_enabled": self.gemini_enabled,
        }


def settings_path() -> Path:
    return Path(os.environ.get("SETTINGS_DIR", "/data/settings")) / "llm.json"


_cache: tuple[tuple[int, int, int], LLMSettings] | None = None


def _stamp(path: Path) -> tuple[int, int, int]:
    st = path.stat()
    return st.st_mtime_ns, st.st_ino, st.st_size


def load() -> LLMSettings:
    """Read the file, re-reading only when it changed. GEMINI_API_KEY in the environment is the
    fallback when no key was saved from the admin page."""
    global _cache
    path = settings_path()
    try:
        stamp = _stamp(path)
    except OSError:
        return LLMSettings(gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
                           gemini_model=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL))
    if _cache and _cache[0] == stamp:
        return _cache[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        s = LLMSettings(
            gemini_api_key=str(raw.get("gemini_api_key", "")).strip(),
            gemini_model=str(raw.get("gemini_model") or DEFAULT_GEMINI_MODEL),
            gemini_enabled=bool(raw.get("gemini_enabled", True)),
        )
    except (OSError, ValueError):
        s = LLMSettings()
    _cache = (stamp, s)
    return s


def save(s: LLMSettings) -> None:
    global _cache
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".llm-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(s), f)
            os.replace(tmp, path)
            _cache = (_stamp(path), s)  # each save gets a new inode, so the next load sees it
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
