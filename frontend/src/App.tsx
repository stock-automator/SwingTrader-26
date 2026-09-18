import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Toaster } from "sonner";
import { getHealth } from "./lib/api";
import { ScreenerGrid } from "./components/ScreenerGrid";
import { BacktestStudio } from "./components/BacktestStudio";
import { SignalMatrixGrid } from "./components/SignalMatrixGrid";
import { SyncStatusBanner } from "./components/SyncStatusBanner";
import type { MacroRegime } from "./types";

type View = "screener" | "backtest" | "signals";

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

const NAV_ITEMS: { view: View; label: string; testId: string }[] = [
  { view: "screener", label: "Live Screener", testId: "nav-live-screener" },
  { view: "signals", label: "Signal Matrix", testId: "nav-signal-matrix" },
  { view: "backtest", label: "Backtesting Studio", testId: "nav-backtest-studio" },
];

function App() {
  const [view, setView] = useState<View>("screener");
  const [regime, setRegime] = useState<MacroRegime>("UNKNOWN");
  const [circuitBreakerActive, setCircuitBreakerActive] = useState(false);

  return (
    <div className="min-h-screen bg-bg text-text">
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "var(--color-panel-alt)",
            border: "1px solid var(--color-border)",
            color: "var(--color-text)",
          },
        }}
      />

      <header className="sticky top-0 z-10 border-b border-border bg-bg/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-4 py-3">
          <div className="text-sm font-bold tracking-widest text-text">
            SWING<span className="text-accent">TRADER</span>
          </div>
          <nav className="flex gap-1">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.view}
                data-testid={item.testId}
                onClick={() => setView(item.view)}
                className={`relative rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                  view === item.view
                    ? "text-text"
                    : "text-text-dim hover:text-text"
                }`}
              >
                {view === item.view && (
                  <motion.span
                    layoutId="nav-pill"
                    className="absolute inset-0 rounded bg-panel-alt"
                    transition={{ type: "spring", duration: 0.35, bounce: 0.15 }}
                  />
                )}
                <span className="relative">{item.label}</span>
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <RegimePill
              regime={regime}
              circuitBreakerActive={circuitBreakerActive}
            />
            <StatusPill />
          </div>
        </div>
        <div className="mx-auto max-w-[1400px] px-4 pb-3">
          <SyncStatusBanner />
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-4 py-6">
        {/* Always mounted, toggled via CSS rather than conditional rendering:
            ScreenerGrid owns the WebSocket that feeds the header's live
            regime pill, so it must not unmount when another tab is active -
            an AnimatePresence-driven unmount here would silently freeze that
            indicator the moment you navigate away from this tab. */}
        <div style={{ display: view === "screener" ? "block" : "none" }}>
          <ScreenerGrid
            onRegimeChange={(r, cb) => {
              setRegime(r);
              setCircuitBreakerActive(cb);
            }}
          />
        </div>
        <AnimatePresence mode="wait">
          {view === "signals" && (
            <motion.div
              key="signals"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
            >
              <SignalMatrixGrid />
            </motion.div>
          )}
          {view === "backtest" && (
            <motion.div
              key="backtest"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
            >
              <BacktestStudio />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}

export default App;
