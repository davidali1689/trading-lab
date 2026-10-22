"""Today's watchlist file is the morning research input. Yesterday is not."""

from __future__ import annotations

import json
from io import BytesIO

from trading_lab.selection.watchlist import load_watchlist_for_day


class _Body:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return BytesIO(self._raw).read()


class _Store:
    def __init__(self, objects: dict[str, dict]) -> None:
        self.objects = objects
        self.keys: list[str] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        self.keys.append(Key)
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": _Body(self.objects[Key])}


def test_loads_todays_file_not_latest() -> None:
    store = _Store(
        {
            "watchlists/2026-10-22.json": {
                "symbols": ["AAPL", "MSFT"],
                "candidates": [],
                "built_at": "2026-10-22T12:00:00+00:00",
                "size": 2,
            }
        }
    )
    doc = load_watchlist_for_day("2026-10-22", bucket="lab-journal", client=store)
    assert store.keys == ["watchlists/2026-10-22.json"]
    assert doc.symbols == ["AAPL", "MSFT"]
    assert doc.detail != "not_ready"


def test_missing_day_is_not_ready() -> None:
    store = _Store({"watchlists/latest.json": {"symbols": ["OLD"], "candidates": []}})
    doc = load_watchlist_for_day("2026-10-22", bucket="lab-journal", client=store)
    assert doc.symbols == []
    assert doc.detail == "not_ready"
