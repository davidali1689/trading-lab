"""Backtest run + per-agent accuracy report schemas."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from trading_lab.schemas.trades import RunMode, SkipEvent, TradeRecord


class BacktestWindow(BaseModel):
    """Inclusive calendar window; walk-forward uses monthly slices."""

    start: date
    end: date
    bar_timeframe: str = "1Min"
    symbols: list[str]
    starting_capital: Decimal = Decimal("100000")


class BacktestRunSpec(BaseModel):
    run_id: UUID
    created_at: datetime
    mode: RunMode = RunMode.BACKTEST
    window: BacktestWindow
    agent_ids: list[str]
    data_vendor: str = "alpaca"
    data_feed: str = "iex"
    notes: str = ""


class AgentAccuracyReport(BaseModel):
    """Historical setup accuracy for one agent on one window."""

    agent_id: str
    window_start: date
    window_end: date
    symbols: list[str]

    session_days: int = 0
    setup_fires: int = 0
    trades_taken: int = 0
    skips: int = 0

    wins: int = 0
    losses: int = 0
    scratches: int = 0

    gross_pnl_usd: Decimal = Decimal("0")
    net_pnl_usd: Decimal = Decimal("0")
    net_pnl_pct_on_capital: Decimal = Decimal("0")

    avg_pnl_usd: Decimal = Decimal("0")
    avg_pnl_pct: Decimal = Decimal("0")
    avg_win_usd: Decimal = Decimal("0")
    avg_loss_usd: Decimal = Decimal("0")
    avg_bars_held: Decimal = Decimal("0")
    avg_sessions_held: Decimal = Decimal("0")

    max_drawdown_usd: Decimal = Decimal("0")
    max_drawdown_pct: Decimal = Decimal("0")

    false_setup_count: int = 0
    false_setup_max_bars: int = Field(
        default=3,
        description="Stop-out within this many bars counts as false setup",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def win_rate(self) -> Decimal:
        closed = self.wins + self.losses
        if closed == 0:
            return Decimal("0")
        return (Decimal(self.wins) / Decimal(closed)) * Decimal("100")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def selectivity(self) -> Decimal:
        if self.setup_fires == 0:
            return Decimal("0")
        return (Decimal(self.trades_taken) / Decimal(self.setup_fires)) * Decimal("100")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def setup_fire_rate_per_day(self) -> Decimal:
        if self.session_days == 0:
            return Decimal("0")
        return Decimal(self.setup_fires) / Decimal(self.session_days)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expectancy_usd(self) -> Decimal:
        closed = self.wins + self.losses
        if closed == 0:
            return Decimal("0")
        p_win = Decimal(self.wins) / Decimal(closed)
        p_loss = Decimal(self.losses) / Decimal(closed)
        avg_loss_abs = abs(self.avg_loss_usd)
        return p_win * self.avg_win_usd - p_loss * avg_loss_abs

    @computed_field  # type: ignore[prop-decorator]
    @property
    def false_setup_rate(self) -> Decimal:
        if self.trades_taken == 0:
            return Decimal("0")
        return (Decimal(self.false_setup_count) / Decimal(self.trades_taken)) * Decimal("100")


class BacktestReport(BaseModel):
    """Full bake-off: one run, many agents, same bars."""

    run: BacktestRunSpec
    agents: list[AgentAccuracyReport]
    trades: list[TradeRecord] = Field(default_factory=list)
    skips: list[SkipEvent] = Field(default_factory=list)

    daily_pnl_usd: dict[str, Decimal] = Field(default_factory=dict)
    weekly_pnl_usd: dict[str, Decimal] = Field(default_factory=dict)
    monthly_pnl_usd: dict[str, Decimal] = Field(default_factory=dict)
    daily_pnl_pct: dict[str, Decimal] = Field(default_factory=dict)
    weekly_pnl_pct: dict[str, Decimal] = Field(default_factory=dict)
    monthly_pnl_pct: dict[str, Decimal] = Field(default_factory=dict)

    def ranked_by_expectancy(self) -> list[AgentAccuracyReport]:
        return sorted(self.agents, key=lambda a: a.expectancy_usd, reverse=True)
