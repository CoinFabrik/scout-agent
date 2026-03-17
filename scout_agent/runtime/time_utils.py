from __future__ import annotations

from datetime import datetime, UTC


def utc_now_iso() -> str:
    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
