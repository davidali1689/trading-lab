"""Grafana CSV export, S3 persist, feed auth, and EMF metrics."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from trading_lab.journal.export_grafana import SKIP_COLUMNS, TRADE_COLUMNS, export_journal_csv
from trading_lab.journal.grafana_feed import fetch_latest_csv, token_matches
from trading_lab.journal.persist import persist_journal_to_s3
from trading_lab.journal.sqlite import SqliteJournal
from trading_lab.observability.cw_emf import NAMESPACE, emit_tick_metric


def test_export_empty_writes_headers(tmp_path: Path) -> None:
    db = tmp_path / "j.sqlite"
    SqliteJournal(db)
    paths = export_journal_csv(db, tmp_path / "out")
    trades = paths["trades"].read_text(encoding="utf-8").strip().splitlines()
    skips = paths["skips"].read_text(encoding="utf-8").strip().splitlines()
    assert trades[0] == ",".join(TRADE_COLUMNS)
    assert skips[0] == ",".join(SKIP_COLUMNS)
    assert len(trades) == 1
    assert len(skips) == 1


def test_persist_uploads_sqlite_and_csvs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "trading-lab-journal.sqlite"
    SqliteJournal(db)
    monkeypatch.setenv("JOURNAL_S3_BUCKET", "test-bucket")

    uploaded: list[tuple[str, str, str]] = []

    class FakeS3:
        def upload_file(self, filename: str, bucket: str, key: str) -> None:
            uploaded.append((filename, bucket, key))

    with patch("boto3.client", return_value=FakeS3()):
        out = persist_journal_to_s3(db)

    assert out["ok"] is True
    assert out["bucket"] == "test-bucket"
    keys = [k for _, _, k in uploaded]
    assert any(k.endswith("trading-lab-journal.sqlite") for k in keys)
    assert "grafana/latest/trades.csv" in keys
    assert "grafana/latest/skips.csv" in keys
    assert any("/trades.csv" in k and k.startswith("journals/") for k in keys)


def test_token_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAFANA_FEED_TOKEN", raising=False)
    assert token_matches("x") is False
    monkeypatch.setenv("GRAFANA_FEED_TOKEN", "secret-token")
    assert token_matches("secret-token") is True
    assert token_matches("wrong") is False
    assert token_matches(None) is False


def test_fetch_latest_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOURNAL_S3_BUCKET", "b")
    body = b"trade_id\n1\n"
    fake = MagicMock()
    fake.get_object.return_value = {"Body": MagicMock(read=lambda: body)}

    with patch("boto3.client", return_value=fake):
        data, ctype = fetch_latest_csv("trades")
    assert data == body
    assert "csv" in ctype
    fake.get_object.assert_called_once_with(Bucket="b", Key="grafana/latest/trades.csv")


def test_emit_tick_metric_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    payload = emit_tick_metric(
        symbol="AAPL",
        status="ORDER_SUBMITTED",
        agent="large_cap_sniper",
        orders=1,
        skips=0,
    )
    assert payload["Orders"] == 1
    assert payload["_aws"]["CloudWatchMetrics"][0]["Namespace"] == NAMESPACE
    line = capsys.readouterr().out.strip()
    parsed = json.loads(line)
    assert parsed["symbol"] == "AAPL"
    assert parsed["status"] == "ORDER_SUBMITTED"


def test_grafana_feed_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAFANA_FEED_TOKEN", "tok")
    monkeypatch.setenv("SECRET_ARN", "")
    # Avoid Secrets Manager / Alpaca side effects on import path
    from api import server

    client = TestClient(server.app)

    r = client.get("/grafana/trades.csv")
    assert r.status_code == 401

    with patch(
        "api.server.fetch_latest_csv",
        return_value=(b"trade_id\n", "text/csv; charset=utf-8"),
    ):
        ok = client.get("/grafana/trades.csv", headers={"X-Grafana-Token": "tok"})
    assert ok.status_code == 200
    assert ok.text.startswith("trade_id")

    with patch(
        "api.server.fetch_latest_csv",
        return_value=(b"event_id\n", "text/csv; charset=utf-8"),
    ):
        skips = client.get("/grafana/skips.csv", headers={"X-Grafana-Token": "tok"})
    assert skips.status_code == 200


def test_events_route_accepts_scheduler_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ARN", "")
    from api import server

    client = TestClient(server.app)
    with patch("api.server._run_phase") as mock_run:
        mock_run.return_value = server.PhaseResult(
            ok=True,
            phase="tick",
            clock_phase="closed",
            detail="noop",
            ts="2026-07-13T00:00:00+00:00",
        )
        resp = client.post("/events", json={"phase": "tick"})
    assert resp.status_code == 200
    mock_run.assert_called_once()
    req = mock_run.call_args[0][0]
    assert req.phase == "tick"
