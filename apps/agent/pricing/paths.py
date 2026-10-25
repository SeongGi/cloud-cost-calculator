import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC_DIR = HERE / "specs"
BUNDLED_DIR = HERE / "catalogs"


def catalog_dir() -> Path:
    """Refreshed catalogs live in CATALOG_DIR (a volume); bundled ones ship with the code."""
    return Path(os.environ.get("CATALOG_DIR", BUNDLED_DIR))


def catalog_path(cloud: str) -> Path:
    return catalog_dir() / f"{cloud}.json"


_META: dict[str, tuple[tuple[int, int, int], str, frozenset[str]]] = {}


def _meta(path: Path) -> tuple[str, frozenset[str]]:
    """(as_of, item keys) of a catalog file, re-read only when the file changed."""
    try:
        st = path.stat()
        mtime = (st.st_mtime_ns, st.st_size, st.st_ino)
        cached = _META.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = (data.get("as_of", ""), frozenset(data.get("items", {})))
    except (OSError, ValueError, AttributeError):
        return "", frozenset()
    _META[str(path)] = (mtime, *meta)
    return meta


def _as_of(path: Path) -> str:
    return _meta(path)[0]


def item_keys(path: Path) -> frozenset[str]:
    return _meta(path)[1]


def refresh_enabled() -> bool:
    return os.environ.get("PRICE_REFRESH", "false") == "true"


def readable_catalog_path(cloud: str) -> Path:
    """The bundled catalog, or with refresh on, the newer of it and the refreshed one. A refreshed
    catalog that lacks some bundled item (a key added by a code update) counts as outdated."""
    refreshed, bundled = catalog_path(cloud), BUNDLED_DIR / f"{cloud}.json"
    if not refresh_enabled() or not refreshed.exists():
        return bundled
    if bundled.exists() and (_as_of(bundled) > _as_of(refreshed) or not item_keys(bundled) <= item_keys(refreshed)):
        return bundled
    return refreshed
