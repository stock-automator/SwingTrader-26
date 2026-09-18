import { useEffect, useRef } from "react";
import {
  createChart,
  AreaSeries,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from "lightweight-charts";
import type { PortfolioHistory } from "../../types";

interface PortfolioEquityChartProps {
  history: PortfolioHistory;
}

export function PortfolioEquityChart({ history }: PortfolioEquityChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  const periodPnl =
    history.equity.length > 0
      ? history.equity[history.equity.length - 1] - history.equity[0]
      : 0;
  const color = periodPnl >= 0 ? "#2ee6a0" : "#ff5c72";

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
    seriesRef.current = chart.addSeries(AreaSeries, {
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
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
    series.applyOptions({
      lineColor: color,
      topColor: `${color}33`,
      bottomColor: `${color}03`,
    });
    const points: LineData<Time>[] = history.timestamp.map((ts, i) => ({
      time: (Math.floor(new Date(ts).getTime() / 1000)) as Time,
      value: history.equity[i],
    }));
    series.setData(points);
    chartRef.current?.timeScale().fitContent();
  }, [history, color]);

  return (
    <div
      ref={containerRef}
      data-testid="portfolio-equity-chart"
      className="h-64 w-full"
    />
  );
}
