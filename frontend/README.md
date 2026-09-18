# SwingTrader Terminal (frontend)

Dark-mode quantitative trading terminal UI for the SwingTrader backend. React + Vite + TypeScript + Tailwind CSS v4 + lightweight-charts + Recharts + TanStack Table.

## Setup

```bash
npm install
cp .env.example .env   # optional, defaults to http://localhost:8000
```

## Run

```bash
npm run dev
```

Expects the backend (`FastAPI`) running at `http://localhost:8000` (CORS already allows `http://localhost:5173`).

## Build

```bash
npm run build
```

## Views

- **Live Screener** — polls `GET /api/v1/screener/live` on load and subscribes to `WS /ws/screener` for push updates; strategy/equity/risk selectors, color-coded direction/regime.
- **Signal Matrix** — cross-strategy "what to look at today" grid (`GET /api/v1/signals/live-today`), sortable/filterable (ticker, strategy, direction) via TanStack Table, one-click paper-trade action per row.
- **Backtesting Studio** — form that POSTs to `/api/v1/backtest`; renders headline, summary stat cards (strategy vs buy & hold vs SPY), an equity curve chart (`lightweight-charts`), relative metrics vs SPY, and a trades table.
- **Portfolio** (`components/portfolio/`) — Alpaca paper account equity/cash/buying-power/daily P&L, an equity-curve chart, an open-positions table, and an emergency "Close All Positions" action gated behind an explicit modal confirmation.
- **Trade Journal** (`components/journal/`) — rolling 30/60/90-day win-rate and expectancy vs. the all-time baseline (Recharts bar charts with a baseline reference line), a MAE/MFE scatter chart across every completed trade, and the raw trade log table.
- **Alerts** (`components/alerts/`) — configure Telegram/Discord/generic-webhook credentials from the browser (no backend file edit or restart) and fire a "Send Test Alert" per channel with toast feedback.

Every tab that talks to an unconfigured backend integration (Alpaca, an
alert channel) renders a calm "not configured" empty state instead of an
error — see `e2e/` for the Playwright coverage of both the configured and
unconfigured paths.

## Tests

```bash
npx playwright test   # e2e/ — tab switching, filtering, paper-trade
                       # submission, modal confirmations, error toasts
npm run lint           # oxlint
```
