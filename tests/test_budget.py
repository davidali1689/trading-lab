"""Budget slices: current equity/5 per agent; max 3 concurrent."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from trading_lab.broker.types import BrokerAccount
from trading_lab.execution.budget import (
    ACTIVE_SLICES,
    BUDGET_SLICES,
    SPECULATIVE_BUDGET_SLICES,
    agent_slice_notional,
    risk_config_from_equity,
    slice_notional,
)
from trading_lab.pipeline.paper_submit import make_risk_gate


def test_slice_notional_is_one_fifth():
    assert slice_notional(Decimal("100000")) == Decimal("20000.00")
    assert slice_notional(Decimal("105000")) == Decimal("21000.00")


def test_speculative_slice_is_half_of_standard():
    """Speculative sizes to equity/10 (half of the standard equity/5 slice)."""
    assert SPECULATIVE_BUDGET_SLICES == 10
    assert agent_slice_notional(Decimal("100000"), "speculative_sniper") == Decimal("10000.00")
    assert agent_slice_notional(Decimal("100000"), "large_cap_sniper") == Decimal("20000.00")
    assert agent_slice_notional(Decimal("100000"), "mid_cap_sniper") == Decimal("20000.00")
    assert agent_slice_notional(Decimal("100000"), "swing_momentum") == Decimal("20000.00")
    assert agent_slice_notional(Decimal("100000"), "gainer_sniper") == Decimal("20000.00")


def test_budget_constants():
    assert BUDGET_SLICES == 5
    assert ACTIVE_SLICES == 3


def test_risk_config_scales_with_equity():
    cfg = risk_config_from_equity(Decimal("100000"))
    assert cfg.max_open_positions == 3
    assert cfg.max_position_notional_usd == Decimal("20000.00")
    assert cfg.max_daily_loss_usd == Decimal("20000.00")
    assert cfg.starting_capital == Decimal("100000")


def test_make_risk_gate_reads_platform_equity():
    broker = MagicMock()
    broker.get_account.return_value = BrokerAccount(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        buying_power=Decimal("200000"),
        paper=True,
    )
    broker.get_open_positions.return_value = []
    risk, equity, notional = make_risk_gate(broker)
    assert equity == Decimal("100000")
    assert notional == Decimal("20000.00")
    assert risk.config.max_open_positions == 3


def test_make_risk_gate_rejects_missing_equity():
    broker = MagicMock()
    broker.get_account.return_value = BrokerAccount(
        equity=Decimal("0"),
        cash=Decimal("0"),
        buying_power=Decimal("0"),
        paper=True,
    )
    try:
        make_risk_gate(broker)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "equity" in str(exc).lower()


def test_vertical_slice_uses_passed_equity(tmp_path):
    from trading_lab.pipeline.vertical_slice import run_vertical_slice

    summary = run_vertical_slice(
        journal_path=str(tmp_path / "j.sqlite"),
        equity=Decimal("100000"),
    )
    assert summary["slice_notional"] == "20000.00"
    assert summary["equity"] == "100000"


def test_vertical_slice_pulls_platform_equity_when_keys(tmp_path):
    from trading_lab.pipeline.vertical_slice import run_vertical_slice

    with (
        patch("trading_lab.pipeline.vertical_slice.has_alpaca_keys", return_value=True),
        patch(
            "trading_lab.pipeline.vertical_slice._platform_equity",
            return_value=Decimal("100000"),
        ),
    ):
        summary = run_vertical_slice(journal_path=str(tmp_path / "j.sqlite"))
    assert summary["slice_notional"] == "20000.00"
