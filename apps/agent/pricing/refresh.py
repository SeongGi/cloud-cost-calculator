"""Daily catalog refresh. A new catalog replaces the current one only if it has at least as
many items; on any failure the previous catalog stays in use."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pricing import build_aws, build_azure, build_gcp
from pricing.paths import BUNDLED_DIR, SPEC_DIR, catalog_dir, readable_catalog_path

log = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def item_keys(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8"))["items"])
    except (OSError, ValueError, KeyError):
        return set()


def _items_of(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["items"]
    except (OSError, ValueError, KeyError):
        return {}


def refresh_one(cloud: str) -> str:
    target = catalog_dir() / f"{cloud}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Build next to the target so the final swap is an atomic rename on the same filesystem.
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        new = Path(tmp) / f"{cloud}.json"
        if cloud == "aws":
            cache = Path(os.environ.get("AWS_PRICE_CACHE", Path(tempfile.gettempdir()) / "aws-price-cache"))
            cache.mkdir(parents=True, exist_ok=True)
            build_aws.build(SPEC_DIR / "aws_spec.json", new, cache)
        elif cloud == "azure":
            build_azure.build(SPEC_DIR / "azure_spec.json", new)
        else:
            build_gcp.build(SPEC_DIR / "gcp_spec.json", new)
        spec_keys = set(json.loads((SPEC_DIR / f"{cloud}_spec.json").read_text(encoding="utf-8"))["specs"])
        previous = readable_catalog_path(cloud)
        bundled = BUNDLED_DIR / f"{cloud}.json"
        # Keys the bundled catalog added with a code update count as previous prices too.
        before, after = (item_keys(previous) | item_keys(bundled)) & spec_keys, item_keys(new)
        if not after:
            return f"{cloud}: kept previous catalog (new build is empty)"
        missing = sorted(before - after)
        if missing:
            # One failed SKU must not freeze the whole catalog: keep the old entry for just that key.
            data = json.loads(new.read_text(encoding="utf-8"))
            # The refreshed file wins over the bundled one; the bundled one covers keys it never had.
            old_items = {**_items_of(bundled), **_items_of(target)}
            data["items"].update({k: old_items[k] for k in missing})
            data["carried_over"] = missing
            new.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            log.warning("%s: carried over previous prices for %s", cloud, missing)
        os.replace(new, target)
        return f"{cloud}: updated ({len(after)} items" + (f", {len(missing)} carried over)" if missing else ")")


def refresh_all() -> list[str]:
    results = []
    for cloud in ("aws", "azure", "gcp"):
        try:
            results.append(refresh_one(cloud))
        except build_gcp.MissingCredentials:
            results.append("gcp: skipped (no GCP_ACCESS_TOKEN)")
        except Exception as exc:  # keep the previous catalog, report the failure
            log.exception("catalog refresh failed for %s", cloud)
            results.append(f"{cloud}: failed ({exc.__class__.__name__}), kept previous catalog")
    for r in results:
        log.info("price refresh: %s", r)
    return results


def seconds_until(hour: int, now: datetime | None = None) -> float:
    now = now or datetime.now(KST)
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def start_scheduler(hour: int = 4) -> threading.Thread:
    def loop():
        while True:
            time.sleep(seconds_until(hour))
            refresh_all()

    thread = threading.Thread(target=loop, name="price-refresh", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    refresh_all()
