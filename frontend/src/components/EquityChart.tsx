import { useEffect, useRef } from "react";
import {
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from "lightweight-charts";
import type { EquityCurvePoint } from "../types";

interface EquityChartProps {
  data: EquityCurvePoint[];
}

const SERIES_CONFIG: {
  key: keyof Pick<EquityCurvePoint, "strategy" | "buy_and_hold" | "spy">;
  title: string;
  color: string;
}[] = [
  { key: "strategy", title: "Strategy", color: "#2ee6a0" },
  { key: "buy_and_hold", title: "Buy & Hold", color: "#3ea6ff" },
  { key: "spy", title: "SPY", color: "#f5b942" },
];

export function EquityChart({ data }: EquityChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Record<string, ISeriesApi<"Line">>>({});

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { color: "transparent" },
        textColor: "#8b93a3",
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#171b24" },
        horzLines: { color: "#171b24" },
      },
      rightPriceScale: { borderColor: "#1f2530" },
      timeScale: { borderColor: "#1f2530" },
      autoSize: true,
    });
    chartRef.current = chart;

    const series: Record<string, ISeriesApi<"Line">> = {};
    for (const cfg of SERIES_CONFIG) {
      series[cfg.key] = chart.addSeries(LineSeries, {
        color: cfg.color,
        lineWidth: 2,
        title: cfg.title,
        priceLineVisible: false,
        lastValueVisible: true,
      });
    }
    seriesRef.current = series;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = {};
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    for (const cfg of SERIES_CONFIG) {
      const s = series[cfg.key];
      if (!s) continue;
      const points: LineData<Time>[] = data
        .filter((row) => row[cfg.key] !== undefined && row[cfg.key] !== null)
        .map((row) => ({
          time: row.date as Time,
          value: row[cfg.key] as number,
        }));
      s.setData(points);
    }
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return (
    <div className="rounded border border-border bg-panel p-3">
      <div className="mb-2 flex items-center gap-4 text-xs">
        {SERIES_CONFIG.map((cfg) => (
          <div key={cfg.key} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: cfg.color }}
            />
            <span className="text-text-dim">{cfg.title}</span>
          </div>
        ))}
      </div>
      <div
        ref={containerRef}
        data-testid="equity-chart"
        className="h-80 w-full"
      />
    </div>
  );
}
