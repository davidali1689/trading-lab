"""Deterministic synthetic bars for local vertical-slice / tests (no API keys)."""

from datetime import timedelta, timezone
from decimal import Decimal

from trading_lab.market_data.types import Bar, BarRequest


class MockMarketData:
    """Generates a simple uptrend with a VWAP-ish session for sniper gates."""

    def get_bars(self, request: BarRequest) -> list[Bar]:
        bars: list[Bar] = []
        ts = request.start.astimezone(timezone.utc)
        end = request.end.astimezone(timezone.utc)
        px = Decimal("100")
        i = 0
        while ts <= end and i < 400:
            o = px
            c = px + Decimal("0.15")
            h = c + Decimal("0.05")
            low = o - Decimal("0.02")
            vol = Decimal("2000000") if i % 20 == 0 else Decimal("800000")
            vwap = (o + c) / 2
            bars.append(
                Bar(
                    symbol=request.symbol,
                    ts=ts,
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=vol,
                    vwap=vwap,
                    timeframe=request.timeframe,
                )
            )
            px = c
            step = timedelta(minutes=1) if "Min" in request.timeframe else timedelta(days=1)
            ts = ts + step
            i += 1
        return bars
