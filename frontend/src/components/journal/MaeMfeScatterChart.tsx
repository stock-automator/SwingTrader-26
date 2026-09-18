import {
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import type { MaeMfeDistributionPoint } from "../../types";

const COLOR_WIN = "#2ee6a0";
const COLOR_LOSS = "#ff5c72";

interface MaeMfeScatterChartProps {
  points: MaeMfeDistributionPoint[];
}

function TooltipContent({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: MaeMfeDistributionPoint }[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded border border-border bg-panel-alt px-3 py-2 text-xs text-text shadow-xl">
      <div className="font-semibold">{p.ticker}</div>
      <div className="text-text-dim">
        MAE {(p.mae_pct * 100).toFixed(2)}% · MFE {(p.mfe_pct * 100).toFixed(2)}%
      </div>
      <div className="text-text-dim">
        P&amp;L {p.pnl === null ? "—" : `$${p.pnl.toFixed(2)}`} · R{" "}
        {p.r_multiple === null ? "—" : p.r_multiple.toFixed(2)}
      </div>
      {p.exit_reason && <div className="text-text-faint">{p.exit_reason}</div>}
    </div>
  );
}

export function MaeMfeScatterChart({ points }: MaeMfeScatterChartProps) {
  if (points.length === 0) {
    return (
      <div
        data-testid="mae-mfe-empty"
        className="flex h-64 items-center justify-center rounded border border-border bg-panel text-sm text-text-faint"
      >
        No completed trades with price history yet.
      </div>
    );
  }

  const winners = points.filter((p) => (p.pnl ?? 0) > 0);
  const losers = points.filter((p) => (p.pnl ?? 0) <= 0);

  return (
    <div
      data-testid="mae-mfe-scatter"
      className="h-80 w-full rounded border border-border bg-panel p-3"
    >
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="#171b24" />
          <XAxis
            type="number"
            dataKey="mae_pct"
            name="MAE"
            unit="%"
            tickFormatter={(v: number) => (v * 100).toFixed(1)}
            stroke="#565f70"
            tick={{ fill: "#8b93a3", fontSize: 11 }}
            label={{
              value: "Max Adverse Excursion (%)",
              position: "insideBottom",
              offset: -4,
              fill: "#8b93a3",
              fontSize: 11,
            }}
          />
          <YAxis
            type="number"
            dataKey="mfe_pct"
            name="MFE"
            unit="%"
            tickFormatter={(v: number) => (v * 100).toFixed(1)}
            stroke="#565f70"
            tick={{ fill: "#8b93a3", fontSize: 11 }}
            label={{
              value: "Max Favorable Excursion (%)",
              angle: -90,
              position: "insideLeft",
              fill: "#8b93a3",
              fontSize: 11,
            }}
          />
          <ZAxis range={[64, 64]} />
          <Tooltip content={<TooltipContent />} cursor={{ stroke: "#1f2530" }} />
          <Legend
            wrapperStyle={{ fontSize: 11, color: "#8b93a3" }}
            formatter={(value: string) => (
              <span className="text-text-dim">{value}</span>
            )}
          />
          <Scatter name="Winners" data={winners} fill={COLOR_WIN} />
          <Scatter name="Losers" data={losers} fill={COLOR_LOSS} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
