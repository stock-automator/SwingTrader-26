"""`POST /api/v1/alerts/dispatch` - on-demand multichannel setup alerts.

Dispatch only - nothing here auto-fires from a screener or signal-matrix
scan. See `alerts/dispatcher.py`'s module docstring for why.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..alerts.dispatcher import AlertDispatcher, AlertMessage
from ..config import Settings, get_settings
from .schemas import AlertDispatchRequest, AlertDispatchResponse

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.post("/dispatch", response_model=AlertDispatchResponse)
def dispatch_alert(
    request: AlertDispatchRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Sends one alert to every configured channel (Telegram/Discord/generic
    webhook). Returns `{"results": {}}` if none are configured - that's a
    valid, non-error state."""
    dispatcher = AlertDispatcher.from_settings(settings)
    message = AlertMessage(
        title=request.title,
        body=request.body,
        ticker=request.ticker,
        direction=request.direction,
        url=request.url,
    )
    return {"results": dispatcher.dispatch(message)}
