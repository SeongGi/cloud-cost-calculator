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


def _as_of(path: Path) -> str:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("as_of", "")
    except (OSError, ValueError):
        return ""


def refresh_enabled() -> bool:
    return os.environ.get("PRICE_REFRESH", "false") == "true"


def readable_catalog_path(cloud: str) -> Path:
    """The bundled catalog, or with refresh on, the newer of it and the refreshed one."""
    refreshed, bundled = catalog_path(cloud), BUNDLED_DIR / f"{cloud}.json"
    if not refresh_enabled() or not refreshed.exists():
        return bundled
    if bundled.exists() and _as_of(bundled) > _as_of(refreshed):
        return bundled
    return refreshed
