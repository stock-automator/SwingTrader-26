import { lazy, Suspense, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Toaster } from "sonner";
import { getHealth } from "./lib/api";
import { ScreenerGrid } from "./components/ScreenerGrid";
import { SignalMatrixGrid } from "./components/SignalMatrixGrid";
import { SyncStatusBanner } from "./components/SyncStatusBanner";
import { AlertSettings } from "./components/alerts/AlertSettings";
import { LiveFeedListener } from "./components/common/LiveFeedListener";
import { ScanProvider } from "./lib/ScanContext";
import type { MacroRegime } from "./types";

// Code-split: each pulls in a charting/table library (lightweight-charts,
// recharts, @tanstack/react-table) only the tab that uses it needs -
// keeps those bytes out of the bundle every visitor downloads for the
// always-shown Live Screener tab.
const BacktestStudio = lazy(() =>
  import("./components/BacktestStudio").then((m) => ({
    default: m.BacktestStudio,
  })),
);
const PortfolioDashboard = lazy(() =>
  import("./components/portfolio/PortfolioDashboard").then((m) => ({
    default: m.PortfolioDashboard,
  })),
);
const JournalDashboard = lazy(() =>
  import("./components/journal/JournalDashboard").then((m) => ({
    default: m.JournalDashboard,
  })),
);
const Simulator = lazy(() => import("./views/Simulator"));
const Dashboard = lazy(() =>
  import("./views/Dashboard").then((m) => ({ default: m.Dashboard })),
);

function TabLoading() {
  return <div className="text-sm text-text-dim">Loading…</div>;
}

type View =
  | "dashboard"
  | "screener"
  | "backtest"
  | "signals"
  | "portfolio"
  | "journal"
  | "alerts"
  | "simulator";

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
  { view: "dashboard", label: "Dashboard", testId: "nav-dashboard" },
  { view: "screener", label: "Live Screener", testId: "nav-live-screener" },
  { view: "signals", label: "Signal Matrix", testId: "nav-signal-matrix" },
  { view: "backtest", label: "Backtesting Studio", testId: "nav-backtest-studio" },
  { view: "portfolio", label: "Portfolio", testId: "nav-portfolio" },
  { view: "journal", label: "Trade Journal", testId: "nav-journal" },
  { view: "alerts", label: "Alerts", testId: "nav-alerts" },
  { view: "simulator", label: "Historical Simulator", testId: "nav-simulator" },
];

function App() {
  const [view, setView] = useState<View>("dashboard");
  const [regime, setRegime] = useState<MacroRegime>("UNKNOWN");
  const [circuitBreakerActive, setCircuitBreakerActive] = useState(false);

  return (
    <ScanProvider>
    <div className="min-h-screen bg-bg text-text">
      <LiveFeedListener />
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
          {view === "dashboard" && (
            <motion.div
              key="dashboard"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
            >
              <Suspense fallback={<TabLoading />}>
                <Dashboard />
              </Suspense>
            </motion.div>
          )}
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
              <Suspense fallback={<TabLoading />}>
                <BacktestStudio />
              </Suspense>
            </motion.div>
          )}
          {view === "portfolio" && (
            <motion.div
              key="portfolio"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
            >
              <Suspense fallback={<TabLoading />}>
                <PortfolioDashboard />
              </Suspense>
            </motion.div>
          )}
          {view === "journal" && (
            <motion.div
              key="journal"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
            >
              <Suspense fallback={<TabLoading />}>
                <JournalDashboard />
              </Suspense>
            </motion.div>
          )}
          {view === "alerts" && (
            <motion.div
              key="alerts"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
            >
              <AlertSettings />
            </motion.div>
          )}
          {view === "simulator" && (
            <motion.div
              key="simulator"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18 }}
            >
              <Suspense fallback={<TabLoading />}>
                <Simulator />
              </Suspense>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
    </ScanProvider>
  );
}

export default App;
