import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";

export interface CandleBar {
  time: string; // ISO date, e.g. "2023-06-15"
  open: number;
  high: number;
  low: number;
  close: number;
}

interface CandlestickChartProps {
  bars: CandleBar[];
  /** Rendered over the chart area when `bars` is empty - e.g. no price
   * history source wired up yet for the current view. */
  emptyLabel?: string;
}

// Minimal lightweight-charts v5 candlestick integration, following the same
// useEffect-create-on-mount / cleanup-on-unmount pattern as
// ../components/EquityChart.tsx (the only other lightweight-charts
// consumer in this codebase at the time this was written).
export function CandlestickChart({ bars, emptyLabel }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

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

    seriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: "#2ee6a0",
      downColor: "#ff5c72",
      borderVisible: false,
      wickUpColor: "#2ee6a0",
      wickDownColor: "#ff5c72",
    });

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const data: CandlestickData<Time>[] = bars.map((b) => ({
      time: b.time as Time,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    }));
    series.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [bars]);

  return (
    <div className="relative rounded border border-border bg-panel p-3">
      <div
        ref={containerRef}
        data-testid="candlestick-chart"
        className="h-80 w-full"
      />
      {bars.length === 0 && emptyLabel && (
        <div className="pointer-events-none absolute inset-3 flex items-center justify-center rounded bg-panel/80 text-center text-xs text-text-faint">
          {emptyLabel}
        </div>
      )}
    </div>
  );
}
