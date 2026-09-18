import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtCurrency, fmtPct } from "../../lib/format";
import type { JournalDecay, JournalSummary } from "../../types";

const ACCENT = "#3ea6ff";
const AMBER = "#f5b942";

interface DecayPanelProps {
  decay: JournalDecay;
  summary: JournalSummary;
}

interface WindowDatum {
  window: string;
  trade_count: number;
  win_rate: number | null;
  expectancy: number | null;
}

function windowsToData(decay: JournalDecay): WindowDatum[] {
  return Object.entries(decay.windows)
    .sort((a, b) => Number(a[0]) - Number(b[0]))
    .map(([window, w]) => ({
      window: `${window}d`,
      trade_count: w.trade_count,
      win_rate: w.win_rate === null ? null : w.win_rate * 100,
      expectancy: w.expectancy,
    }));
}

function WinRateTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: WindowDatum }[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded border border-border bg-panel-alt px-3 py-2 text-xs text-text shadow-xl">
      <div className="font-semibold">{d.window} window</div>
      <div className="text-text-dim">{d.trade_count} trade(s)</div>
      <div className="text-text-dim">
        Win rate: {d.win_rate === null ? "—" : `${d.win_rate.toFixed(1)}%`}
      </div>
    </div>
  );
}

function ExpectancyTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: WindowDatum }[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded border border-border bg-panel-alt px-3 py-2 text-xs text-text shadow-xl">
      <div className="font-semibold">{d.window} window</div>
      <div className="text-text-dim">{d.trade_count} trade(s)</div>
      <div className="text-text-dim">
        Expectancy: {d.expectancy === null ? "—" : fmtCurrency(d.expectancy, 2)}
      </div>
    </div>
  );
}

export function DecayPanel({ decay, summary }: DecayPanelProps) {
  const data = windowsToData(decay);
  const baselineWinRate =
    summary.win_rate === null ? null : summary.win_rate * 100;
  const baselineExpectancy = summary.avg_pnl;

  if (summary.error) {
    return (
      <div
        data-testid="decay-empty"
        className="flex h-40 items-center justify-center rounded border border-border bg-panel text-sm text-text-faint"
      >
        No completed trades yet — decay tracking starts after your first
        exit.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <div className="rounded border border-border bg-panel p-3">
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="font-semibold uppercase tracking-wider text-text-faint">
            Rolling Win Rate vs. All-Time Baseline
          </span>
          <span className="text-text-dim">
            Baseline: {baselineWinRate === null ? "—" : fmtPct(baselineWinRate, 1)}
          </span>
        </div>
        <div data-testid="decay-win-rate-chart" className="h-52 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#171b24" vertical={false} />
              <XAxis
                dataKey="window"
                stroke="#565f70"
                tick={{ fill: "#8b93a3", fontSize: 11 }}
              />
              <YAxis
                stroke="#565f70"
                tick={{ fill: "#8b93a3", fontSize: 11 }}
                tickFormatter={(v: number) => `${v}%`}
              />
              <Tooltip content={<WinRateTooltip />} cursor={{ fill: "#171b24" }} />
              {baselineWinRate !== null && (
                <ReferenceLine
                  y={baselineWinRate}
                  stroke={AMBER}
                  strokeDasharray="4 4"
                  strokeWidth={1.5}
                />
              )}
              <Bar dataKey="win_rate" fill={ACCENT} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="rounded border border-border bg-panel p-3">
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="font-semibold uppercase tracking-wider text-text-faint">
            Rolling Expectancy vs. All-Time Baseline
          </span>
          <span className="text-text-dim">
            Baseline:{" "}
            {baselineExpectancy === null ? "—" : fmtCurrency(baselineExpectancy, 2)}
          </span>
        </div>
        <div data-testid="decay-expectancy-chart" className="h-52 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#171b24" vertical={false} />
              <XAxis
                dataKey="window"
                stroke="#565f70"
                tick={{ fill: "#8b93a3", fontSize: 11 }}
              />
              <YAxis stroke="#565f70" tick={{ fill: "#8b93a3", fontSize: 11 }} />
              <Tooltip content={<ExpectancyTooltip />} cursor={{ fill: "#171b24" }} />
              {baselineExpectancy !== null && (
                <ReferenceLine
                  y={baselineExpectancy}
                  stroke={AMBER}
                  strokeDasharray="4 4"
                  strokeWidth={1.5}
                />
              )}
              <Bar dataKey="expectancy" fill={ACCENT} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
