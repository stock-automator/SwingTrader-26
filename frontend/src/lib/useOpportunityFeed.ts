import { useCallback, useEffect, useState } from "react";
import { getSignalMatrix } from "./api";
import type { OpportunitySetup, SignalMatrixRow } from "../types";

const REFRESH_MS = 30000;

function adaptRow(row: SignalMatrixRow): OpportunitySetup {
  const rMultiple = row.reward_risk_ratio ?? 0;
  const winProb = row.win_probability ?? 0;
  // Setup Quality Score: weighted combo of win probability and R-multiple.
  // rs_vs_spy / regime-confluence fields aren't on SignalMatrixRow yet, so
  // only the two available signals are weighted (0.6 win prob / 0.4 R).
  const setupQualityScore =
    0.6 * Math.min(Math.max(winProb, 0), 1) +
    0.4 * Math.min(Math.max(rMultiple / 3, 0), 1);

  return {
    ticker: row.ticker,
    strategy: row.strategy,
    direction: row.direction,
    tradable: row.tradable,
    as_of: row.as_of,
    close: row.close,
    entry_price: row.entry_price,
    stop_loss: row.stop_loss,
    take_profit: row.take_profit,
    shares: row.shares,
    risk_amount: row.risk_amount,
    reward_risk_ratio: row.reward_risk_ratio,
    notional_value: row.notional_value,
    note: row.note,
    win_probability: row.win_probability,
    win_probability_method: row.win_probability_method,
    win_probability_sample_size: row.win_probability_sample_size,
    win_probability_confidence_low: row.win_probability_confidence_low,
    win_probability_confidence_high: row.win_probability_confidence_high,
    win_probability_note: row.win_probability_note,
    r_multiple: rMultiple,
    setup_quality_score: setupQualityScore,
    // Portfolio-heat capping is applied downstream (Dashboard.tsx) once
    // setups are ranked, not here in the raw feed adapter.
    portfolio_heat_capped: false,
    capped_reason: null,
  };
}

interface OpportunityFeedState {
  setups: OpportunitySetup[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

// Today this is backed by the existing /api/v1/signals/live-today endpoint
// (getSignalMatrix). Once the backend's /api/v1/scans job feed is ready,
// swap the body of this hook to read from ScanContext's activeScans /
// scanStatus results instead - callers of useOpportunityFeed() don't change.
export function useOpportunityFeed(): OpportunityFeedState {
  const [setups, setSetups] = useState<OpportunitySetup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    getSignalMatrix()
      .then((res) => {
        setSetups(res.rows.map(adaptRow));
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return { setups, loading, error, refresh };
}
