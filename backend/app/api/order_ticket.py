"""`POST /api/v1/order-ticket` - broker-ready tickets for Trading 212 / IBKR.

Turns one already-resolved setup (entry/stop/target) into a side-by-side
comparison across account-size tiers ($1k/$5k/$10k by default), each sized
and gated by the full risk engine (`RiskManager.build_risk_managed_order`).
Stateless: the caller supplies the setup's own numbers (as read off the
screener grid or a backtest), so this never touches price data itself.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..quant.engine import build_order_tickets
from .schemas import OrderTicketRequest, OrderTicketsResponse

router = APIRouter(prefix="/api/v1", tags=["order-ticket"])


@router.post("/order-ticket", response_model=OrderTicketsResponse)
def build_order_ticket_endpoint(request: OrderTicketRequest) -> dict:
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
    return {"tickets": [t.as_dict() for t in tickets]}
