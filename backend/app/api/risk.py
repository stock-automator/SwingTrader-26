"""`POST /api/v1/position-sizer/preview` - live ATR position-sizing preview
for the Order Ticket Drawer's risk-percentage selector (0.5% / 1.0% / 2.0%).

Stateless, like `order_ticket.py`: the caller supplies ATR and the chosen
risk %, this returns whole shares via `quant.risk.PositionSizer` - the same
class the rest of the risk engine's ATR-multiple sizing is built on
(`RiskManager.volatility_parity_size`), so the UI preview and the backend's
own sizing math can never drift apart.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..quant.risk import PositionSizer
from .schemas import PositionSizerRequest, PositionSizerResponse

router = APIRouter(prefix="/api/v1/position-sizer", tags=["risk"])


@router.post("/preview", response_model=PositionSizerResponse)
def preview_position_size(request: PositionSizerRequest) -> dict:
    sizer = PositionSizer(
        account_capital=request.account_capital, risk_pct=request.risk_pct
    )
    shares = sizer.shares(atr=request.atr, atr_multiplier=request.atr_multiplier)
    return {"shares": shares, "risk_amount": sizer.risk_amount}
