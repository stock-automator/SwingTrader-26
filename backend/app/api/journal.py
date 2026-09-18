"""`/api/v1/journal/*` - trade journal summary, decay, and MAE/MFE analytics.

Thin wrapper around `journal.executor.TradeJournal`, which does the actual
CSV-backed bookkeeping; nothing here holds state of its own; each request
reads `settings.journal_path` fresh, the same "no in-memory singleton"
choice `api/data_sync.py` and every other stateless route here already make.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings, get_settings
from ..data.loader import DataUnavailableError, load_prices
from ..journal.executor import TradeJournal
from .schemas import (
    JournalDecayResponse,
    JournalSummaryResponse,
    JournalTradesResponse,
    MaeMfeDistributionResponse,
    MaeMfeRequest,
    MaeMfeResponse,
)

router = APIRouter(prefix="/api/v1/journal", tags=["journal"])


def _journal(settings: Settings = Depends(get_settings)) -> TradeJournal:
    return TradeJournal(journal_file=str(settings.journal_path))


@router.get("/summary", response_model=JournalSummaryResponse)
def journal_summary(journal: TradeJournal = Depends(_journal)) -> dict:
    """All-time trade performance: win rate, expectancy, drawdown, etc."""
    return journal.analyze_all_trades()


@router.get("/trades", response_model=JournalTradesResponse)
def journal_trades(journal: TradeJournal = Depends(_journal)) -> dict:
    """Every logged trade signal - the raw feed behind the Trade Journal
    UI's table."""
    return {"trades": journal.list_trades()}


@router.get("/decay", response_model=JournalDecayResponse)
def journal_decay(
    windows: str = "30,60,90", journal: TradeJournal = Depends(_journal)
) -> dict:
    """Rolling win-rate/expectancy over trailing day-count windows, to catch
    an edge decaying before it's obvious in the all-time numbers."""
    try:
        window_days = tuple(int(w.strip()) for w in windows.split(",") if w.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"windows must be comma-separated integers: {exc}"
        ) from exc
    if not window_days:
        raise HTTPException(status_code=422, detail="windows must not be empty")

    result = journal.analyze_decay(windows=window_days)
    return {"windows": {str(k): v for k, v in result.items()}}


@router.post("/mae-mfe", response_model=MaeMfeResponse)
def journal_mae_mfe(
    request: MaeMfeRequest,
    settings: Settings = Depends(get_settings),
    journal: TradeJournal = Depends(_journal),
) -> dict:
    """Maximum Adverse/Favorable Excursion for one closed trade."""
    try:
        price_df = load_prices(
            request.ticker,
            data_dir=settings.data_dir,
            allow_download=settings.allow_downloads,
        )
    except DataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        return journal.compute_mae_mfe(request.trade_id, price_df)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/mae-mfe-distribution", response_model=MaeMfeDistributionResponse)
def journal_mae_mfe_distribution(
    settings: Settings = Depends(get_settings),
    journal: TradeJournal = Depends(_journal),
) -> dict:
    """MAE/MFE for every completed trade, one price load per distinct
    ticker - the data behind the Strategy Decay Visualizer's distribution
    chart. A ticker whose price data can't be loaded is skipped (see
    `warnings`) rather than failing the whole response."""

    def _load(ticker: str):
        return load_prices(
            ticker, data_dir=settings.data_dir, allow_download=settings.allow_downloads
        )

    return journal.compute_mae_mfe_all(_load)
