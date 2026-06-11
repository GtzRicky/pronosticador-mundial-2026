from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(data: dict[str, Any] | None) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_params(params: dict[str, Any] | None) -> str:
    payload = canonical_json(params)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
