# SwingTrader Terminal (frontend)

Dark-mode quantitative trading terminal UI for the SwingTrader backend. React + Vite + TypeScript + Tailwind CSS v4 + lightweight-charts.

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
- **Backtesting Studio** — form that POSTs to `/api/v1/backtest`; renders headline, summary stat cards (strategy vs buy & hold vs SPY), an equity curve chart (`lightweight-charts`), relative metrics vs SPY, and a trades table.
