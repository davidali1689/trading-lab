"""Journal hydrate/persist across Lambda /tmp cold starts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from trading_lab.journal.persist import hydrate_journal_from_s3, persist_journal_to_s3
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.schemas.trades import RunMode, SkipEvent, SkipReason


def test_hydrate_refreshes_even_when_local_exists(tmp_path, monkeypatch):
    """Warm Lambda /tmp must not block S3 refresh or persist stomps remote trades."""
    monkeypatch.setenv("JOURNAL_S3_BUCKET", "bucket")
    path = tmp_path / "trading-lab-journal.sqlite"
    path.write_bytes(b"stale-local")
    client = MagicMock()

    def _download(_bucket, _key, dest):
        Path(dest).write_bytes(b"remote-sqlite")

    client.download_file.side_effect = _download
    with patch("boto3.client", return_value=client):
        out = hydrate_journal_from_s3(path)
    assert out["ok"] is True
    assert out["detail"] == "downloaded"
    assert path.read_bytes() == b"remote-sqlite"
    client.download_file.assert_called_once()


def test_hydrate_downloads_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_S3_BUCKET", "bucket")
    path = tmp_path / "trading-lab-journal.sqlite"
    client = MagicMock()

    def _download(_bucket, _key, dest):
        Path(dest).write_bytes(b"remote-sqlite")

    client.download_file.side_effect = _download
    with patch("boto3.client", return_value=client):
        out = hydrate_journal_from_s3(path)
    assert out["ok"] is True
    assert out["detail"] == "downloaded"
    assert path.read_bytes() == b"remote-sqlite"
    assert client.download_file.call_args[0][1] == "journals/latest/trading-lab-journal.sqlite"


def test_persist_writes_latest_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_S3_BUCKET", "bucket")
    path = tmp_path / "trading-lab-journal.sqlite"
    journal = SqliteJournal(path)
    journal.write_skip(
        SkipEvent(
            event_id=uuid4(),
            run_id=uuid4(),
            found_by_agent="large_cap_sniper",
            symbol="AAPL",
            ts=datetime(2026, 7, 14, 15, 0, tzinfo=UTC),
            mode=RunMode.PAPER,
            skip_reason=SkipReason.SETUP_MISSING,
            detail="rvol",
        )
    )
    client = MagicMock()
    with patch("boto3.client", return_value=client):
        out = persist_journal_to_s3(path)
    assert out["ok"] is True
    assert out["latest_key"] == "journals/latest/trading-lab-journal.sqlite"
    keys = [c.args[2] for c in client.upload_file.call_args_list]
    assert "journals/latest/trading-lab-journal.sqlite" in keys
    assert "grafana/latest/skips.csv" in keys
