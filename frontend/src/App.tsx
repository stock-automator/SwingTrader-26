import { useEffect, useState } from "react";
import { getHealth } from "./lib/api";
import { ScreenerGrid } from "./components/ScreenerGrid";
import { BacktestStudio } from "./components/BacktestStudio";
import type { MacroRegime } from "./types";

type View = "screener" | "backtest";

const REGIME_DISPLAY: Record<MacroRegime, { emoji: string; label: string }> = {
  BULL_TRENDING: { emoji: "🟢", label: "BULL_TRENDING" },
  BEAR_TRENDING: { emoji: "🔴", label: "BEAR_TRENDING" },
  HIGH_VOLATILITY_CHOP: { emoji: "🟠", label: "HIGH_VOLATILITY_CHOP" },
  NEUTRAL: { emoji: "⚪", label: "NEUTRAL" },
  UNKNOWN: { emoji: "⚫", label: "UNKNOWN" },
};

function RegimePill({
  regime,
  circuitBreakerActive,
}: {
  regime: MacroRegime;
  circuitBreakerActive: boolean;
}) {
  if (circuitBreakerActive) {
    return (
      <span
        data-testid="circuit-breaker-pill"
        className="rounded border border-short-dim bg-short-dim/30 px-2 py-1 text-xs font-semibold text-short"
      >
        🔴 CIRCUIT BREAKER ACTIVE
      </span>
    );
  }
  const { emoji, label } = REGIME_DISPLAY[regime];
  return (
    <span
      data-testid="regime-pill"
      className="rounded border border-border bg-panel-alt px-2 py-1 text-xs font-medium text-text-dim"
    >
      {emoji} {label}
    </span>
  );
}

function StatusPill() {
  const [status, setStatus] = useState<"ok" | "down" | "checking">("checking");

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then(() => !cancelled && setStatus("ok"))
      .catch(() => !cancelled && setStatus("down"));
    return () => {
      cancelled = true;
    };
  }, []);

  const color =
    status === "ok" ? "bg-long" : status === "down" ? "bg-short" : "bg-amber";
  const label =
    status === "ok"
      ? "API online"
      : status === "down"
        ? "API offline"
        : "checking…";

  return (
    <span className="flex items-center gap-1.5 text-xs text-text-dim">
      <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  );
}

function App() {
  const [view, setView] = useState<View>("screener");
  const [regime, setRegime] = useState<MacroRegime>("UNKNOWN");
  const [circuitBreakerActive, setCircuitBreakerActive] = useState(false);

  return (
    <div className="min-h-screen bg-bg text-text">
      <header className="sticky top-0 z-10 border-b border-border bg-bg/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-4 py-3">
          <div className="text-sm font-bold tracking-widest text-text">
            SWING<span className="text-accent">TRADER</span>
          </div>
          <nav className="flex gap-1">
            <button
              data-testid="nav-live-screener"
              onClick={() => setView("screener")}
              className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                view === "screener"
                  ? "bg-panel-alt text-text"
                  : "text-text-dim hover:text-text"
              }`}
            >
              Live Screener
            </button>
            <button
              data-testid="nav-backtest-studio"
              onClick={() => setView("backtest")}
              className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                view === "backtest"
                  ? "bg-panel-alt text-text"
                  : "text-text-dim hover:text-text"
              }`}
            >
              Backtesting Studio
            </button>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <RegimePill
              regime={regime}
              circuitBreakerActive={circuitBreakerActive}
            />
            <StatusPill />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-4 py-6">
        <div style={{ display: view === "screener" ? "block" : "none" }}>
          <ScreenerGrid
            onRegimeChange={(r, cb) => {
              setRegime(r);
              setCircuitBreakerActive(cb);
            }}
          />
        </div>
        {view === "backtest" && <BacktestStudio />}
      </main>
    </div>
  );
}

export default App;
