"""`POST /api/v1/order-ticket` - broker-ready tickets for Trading 212 / IBKR.

Turns one already-resolved setup (entry/stop/target) into a side-by-side
comparison across account-size tiers ($1k/$5k/$10k by default), each sized
and gated by the full risk engine (`RiskManager.build_risk_managed_order`).
Stateless: the caller supplies the setup's own numbers (as read off the
screener grid or a backtest). The only data read is the cached `GBPUSD=X`
rate for the display-only GBP figures; a missing rate leaves them null.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..data import fx
from ..quant.engine import build_order_tickets
from .schemas import OrderTicketRequest, OrderTicketsResponse

router = APIRouter(prefix="/api/v1", tags=["order-ticket"])


@router.post("/order-ticket", response_model=OrderTicketsResponse)
def build_order_ticket_endpoint(
    request: OrderTicketRequest, settings: Settings = Depends(get_settings)
) -> dict:
    tickets = build_order_tickets(
        ticker=request.ticker,
        entry_price=request.entry_price,
        sl_type=request.sl_type,
        sl_value=request.sl_value,
        tp_type=request.tp_type,
        tp_value=request.tp_value,
        account_tiers=tuple(request.account_tiers),
        atr=request.atr,
        direction=request.direction,
        order_type=request.order_type,
    )
    fx_round_trip = settings.fx_cost_pct * settings.fx_conversions_per_round_trip
    with fx.fx_context(settings.data_dir, offline=not settings.allow_downloads):
        found = fx.latest_gbp_rate()
    usd_per_gbp = None if found is None else found.rate
    rate_date = None if found is None else found.as_of.isoformat()

    payload = []
    for ticket in tickets:
        row = ticket.as_dict()
        row["fx_rate_date"] = rate_date
        row["fx_rate_source"] = None if found is None else found.source
        row["fx_cost_usd"] = round(ticket.notional_value * fx_round_trip, 2)
        if usd_per_gbp is None:
            row["risk_gbp"] = row["notional_gbp"] = None
            row["fx_note"] = (
                "GBP rate unavailable: GBPUSD=X is not cached yet (run a data sync)."
            )
        else:
            row["risk_gbp"] = round(ticket.risk_amount / usd_per_gbp, 2)
            row["notional_gbp"] = round(ticket.notional_value / usd_per_gbp, 2)
            row["fx_note"] = None
        payload.append(row)
    return {"tickets": payload}
