from __future__ import annotations

import json
from pathlib import Path


def sanitize(value):
    if isinstance(value, dict):
        return {
            key: sanitize(item) for key, item in value.items()
            if key.lower() not in {"token", "authorization", "credential", "api_key", "secret"}
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def write_bad_case(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sanitize(record), ensure_ascii=False, sort_keys=True) + "\n")


def failed_case_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("status") == "FAIL":
            result.append(record["case_id"])
    return sorted(set(result))
