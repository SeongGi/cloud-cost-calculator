"""Compare unit prices of two catalogs with the same item-key shape (e.g. a new build vs a reference)."""

import json
import sys


def compare(new_path: str, ref_path: str) -> tuple[int, int, list[str]]:
    new, ref = (json.load(open(p, encoding="utf-8"))["items"] for p in (new_path, ref_path))
    same, total, diffs = 0, 0, []
    for key in sorted(set(new) | set(ref)):
        if key not in new or key not in ref:
            diffs.append(f"{key}: only in {'new' if key in new else 'reference'}")
            continue
        for region in sorted(set(new[key]["prices"]) | set(ref[key]["prices"])):
            total += 1
            a = new[key]["prices"].get(region, {}).get("usd")
            b = ref[key]["prices"].get(region, {}).get("usd")
            if a is not None and b is not None and float(a) == float(b):
                same += 1
            else:
                diffs.append(f"{key} [{region}]: new={a} ref={b}")
    return same, total, diffs


if __name__ == "__main__":
    same, total, diffs = compare(sys.argv[1], sys.argv[2])
    print(f"match {same}/{total}")
    print("\n".join(diffs))
