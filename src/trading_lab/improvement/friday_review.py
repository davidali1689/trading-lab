"""Friday weekend pack: scorecard + four coaches in one phase."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from trading_lab.improvement.coaches import run_weekly_coaches
from trading_lab.improvement.miss_harvest import build_miss_report
from trading_lab.improvement.scorecard import build_weekly_scorecard, persist_scorecard
from trading_lab.schemas.misses import DailyMissReport
from trading_lab.schemas.scorecard import WeeklyScorecard

logger = logging.getLogger("trading_lab.improvement.friday_review")


def run_friday_review(
    journal_path: str | Path,
    *,
    report: DailyMissReport | None = None,
    prior_scorecard: WeeklyScorecard | None = None,
    miss_shards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Single Friday pack for weekend review:
    1) weekly scorecard (improving / flat / worse)
    2) four strategy coaches (proposals pending green-light)
    """
    report = report or build_miss_report(journal_path=journal_path)
    card = build_weekly_scorecard(
        journal_path,
        miss_shards=miss_shards,
        prior=prior_scorecard,
    )
    score_persist = persist_scorecard(card)
    coaches = run_weekly_coaches(report=report, scorecard=card.to_dict())
    pack = {
        "ok": bool(coaches.get("ok")),
        "week_id": card.week_id,
        "scorecard": card.to_dict(),
        "scorecard_persist": score_persist,
        "scorecard_summary": card.summary,
        "coaches": coaches,
        "miss_report_detail": report.detail,
        "note": (
            "Weekend review pack — green-light proposals manually; propose_revert is a flag only"
        ),
    }
    logger.info(
        "friday_review week=%s scorecard=%s coaches_ok=%s",
        pack["week_id"],
        pack.get("scorecard_summary"),
        coaches.get("ok"),
    )
    return pack
