import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowDown, ArrowUp, ArrowUpDown, RefreshCw, Zap } from "lucide-react";
import { toast } from "sonner";
import { ApiError, getSignalMatrix, submitOrder } from "../lib/api";
import { fmtInt, fmtNum } from "../lib/format";
import { STRATEGIES, type Direction, type SignalMatrixRow } from "../types";

const STRATEGY_LABELS: Record<string, string> = Object.fromEntries(
  STRATEGIES.map((s) => [s.value, s.label]),
);

const DIRECTION_BADGE: Record<Direction, string> = {
  LONG: "text-long bg-long-dim/40 border-long-dim",
  SHORT: "text-short bg-short-dim/40 border-short-dim",
  EXIT_LONG: "text-orange-400 bg-orange-900/30 border-orange-800",
  FLAT: "text-text-faint bg-transparent border-border-soft",
};

const REFRESH_MS = 30000;

function DirectionBadge({ direction }: { direction: Direction }) {
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-xs font-medium ${DIRECTION_BADGE[direction]}`}
    >
      {direction}
    </span>
  );
}

const columnHelper = createColumnHelper<SignalMatrixRow>();

export function SignalMatrixGrid() {
  const [rows, setRows] = useState<SignalMatrixRow[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sorting, setSorting] = useState<SortingState>([
    { id: "reward_risk_ratio", desc: true },
  ]);
  const [tickerFilter, setTickerFilter] = useState("");
  const [strategyFilter, setStrategyFilter] = useState<Set<string>>(new Set());
  const [directionFilter, setDirectionFilter] = useState<Set<Direction>>(
    new Set(["LONG", "SHORT"]),
  );
  const [submittingKey, setSubmittingKey] = useState<string | null>(null);

  const fetchMatrix = useCallback(() => {
    setLoading(true);
    getSignalMatrix()
      .then((res) => {
        setRows(res.rows);
        setWarnings(res.warnings);
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchMatrix();
    const id = setInterval(fetchMatrix, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchMatrix]);

  const availableStrategies = useMemo(
    () => Array.from(new Set(rows.map((r) => r.strategy))).sort(),
    [rows],
  );

  const filteredRows = useMemo(() => {
    return rows.filter((r) => {
      if (tickerFilter && !r.ticker.includes(tickerFilter.toUpperCase())) {
        return false;
      }
      if (strategyFilter.size > 0 && !strategyFilter.has(r.strategy)) {
        return false;
      }
      if (!directionFilter.has(r.direction)) return false;
      return true;
    });
  }, [rows, tickerFilter, strategyFilter, directionFilter]);

  async function handlePaperTrade(row: SignalMatrixRow) {
    const key = `${row.ticker}:${row.strategy}`;
    setSubmittingKey(key);
    try {
      const res = await submitOrder({
        ticker: row.ticker,
        side: "buy",
        qty: row.shares ?? 0,
        order_type: "MARKET",
      });
      toast.success(`Paper order placed: ${res.symbol} (${res.status ?? "submitted"})`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        toast.error(
          "Alpaca isn't configured on this server — set ALPACA_API_KEY / ALPACA_API_SECRET to enable paper trading.",
        );
      } else {
        toast.error(
          `Paper trade failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    } finally {
      setSubmittingKey(null);
    }
  }

  const columns = useMemo(
    () => [
      columnHelper.accessor("ticker", { header: "Ticker" }),
      columnHelper.accessor("strategy", {
        header: "Strategy",
        cell: (info) => STRATEGY_LABELS[info.getValue()] ?? info.getValue(),
      }),
      columnHelper.accessor("direction", {
        header: "Direction",
        cell: (info) => <DirectionBadge direction={info.getValue()} />,
      }),
      columnHelper.accessor("close", {
        header: "Market Price",
        cell: (info) => fmtNum(info.getValue()),
      }),
      columnHelper.accessor("entry_price", {
        header: "Target Entry",
        cell: (info) => fmtNum(info.getValue()),
      }),
      columnHelper.accessor("stop_loss", {
        header: "SL",
        cell: (info) => (
          <span className="text-short">{fmtNum(info.getValue())}</span>
        ),
      }),
      columnHelper.accessor("take_profit", {
        header: "TP",
        cell: (info) => (
          <span className="text-long">{fmtNum(info.getValue())}</span>
        ),
      }),
      columnHelper.accessor("reward_risk_ratio", {
        header: "R:R",
        cell: (info) => {
          const v = info.getValue();
          return v === null ? "—" : `${v.toFixed(1)}R`;
        },
      }),
      columnHelper.accessor("win_probability", {
        header: "Win Prob.",
        cell: (info) => {
          const row = info.row.original;
          const v = info.getValue();
          if (v === null) {
            return (
              <span
                className="text-text-faint"
                title={row.win_probability_note ?? "No model-backed estimate available"}
              >
                —
              </span>
            );
          }
          const isBootstrap = row.win_probability_method === "block_bootstrap";
          const band =
            isBootstrap &&
            row.win_probability_confidence_low !== null &&
            row.win_probability_confidence_high !== null
              ? ` (${(row.win_probability_confidence_low * 100).toFixed(0)}-${(row.win_probability_confidence_high * 100).toFixed(0)}%)`
              : "";
          return (
            <span
              title={`${row.win_probability_note ?? ""} · n=${row.win_probability_sample_size ?? "?"}`}
            >
              {(v * 100).toFixed(0)}%{band}
              {isBootstrap && (
                <span className="ml-1 text-text-faint" title="Block-bootstrap estimate">
                  ~
                </span>
              )}
            </span>
          );
        },
      }),
      columnHelper.accessor("shares", {
        header: "Shares",
        cell: (info) => fmtInt(info.getValue()),
      }),
      columnHelper.display({
        id: "action",
        header: "",
        cell: (info) => {
          const row = info.row.original;
          const key = `${row.ticker}:${row.strategy}`;
          const enabled = row.tradable && row.direction === "LONG";
          return (
            <button
              onClick={() => handlePaperTrade(row)}
              disabled={!enabled || submittingKey === key}
              data-testid="paper-trade-button"
              className="flex items-center gap-1 rounded border border-border bg-panel-alt px-2 py-1 text-xs text-text hover:border-accent disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Zap size={12} />
              {submittingKey === key ? "…" : "Paper Trade"}
            </button>
          );
        },
      }),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [submittingKey],
  );

  const table = useReactTable({
    data: filteredRows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  function toggleStrategy(value: string) {
    setStrategyFilter((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  function toggleDirection(value: Direction) {
    setDirectionFilter((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-4 rounded border border-border bg-panel p-3">
        <label className="flex flex-col gap-1 text-xs text-text-dim">
          Ticker
          <input
            type="text"
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
            placeholder="Filter…"
            className="w-28 rounded border border-border bg-panel-alt px-2 py-1 text-sm text-text"
          />
        </label>

        <div className="flex flex-col gap-1 text-xs text-text-dim">
          Direction
          <div className="flex gap-1">
            {(["LONG", "SHORT"] as Direction[]).map((d) => (
              <button
                key={d}
                onClick={() => toggleDirection(d)}
                className={`rounded border px-2 py-1 text-xs ${
                  directionFilter.has(d)
                    ? DIRECTION_BADGE[d]
                    : "border-border-soft text-text-faint"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>

        {availableStrategies.length > 0 && (
          <div className="flex flex-col gap-1 text-xs text-text-dim">
            Strategy
            <div className="flex max-w-md flex-wrap gap-1">
              {availableStrategies.map((s) => (
                <button
                  key={s}
                  onClick={() => toggleStrategy(s)}
                  className={`rounded border px-2 py-1 text-xs ${
                    strategyFilter.size === 0 || strategyFilter.has(s)
                      ? "border-accent/60 text-accent"
                      : "border-border-soft text-text-faint"
                  }`}
                >
                  {STRATEGY_LABELS[s] ?? s}
                </button>
              ))}
            </div>
          </div>
        )}

        <button
          onClick={fetchMatrix}
          disabled={loading}
          className="ml-auto flex items-center gap-1.5 rounded border border-border bg-panel-alt px-3 py-1.5 text-sm text-text hover:border-accent disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          {loading ? "Scanning…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
          {error}
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded border border-amber-dim bg-amber-dim/20 px-3 py-2 text-xs text-amber">
          {warnings.join(" · ")}
        </div>
      )}

      <div className="overflow-x-auto rounded border border-border bg-panel">
        <table className="w-full min-w-[1100px] border-collapse text-sm">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr
                key={headerGroup.id}
                className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-faint"
              >
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="cursor-pointer select-none px-3 py-2"
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    <span className="inline-flex items-center gap-1">
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                      {header.column.getCanSort() &&
                        (header.column.getIsSorted() === "asc" ? (
                          <ArrowUp size={11} />
                        ) : header.column.getIsSorted() === "desc" ? (
                          <ArrowDown size={11} />
                        ) : (
                          <ArrowUpDown size={11} className="opacity-40" />
                        ))}
                    </span>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            <AnimatePresence initial={false}>
              {table.getRowModel().rows.map((row) => (
                <motion.tr
                  key={row.id}
                  layout
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  data-testid="signal-matrix-row"
                  className="border-b border-border-soft last:border-0 hover:bg-panel-alt"
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-2">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </motion.tr>
              ))}
            </AnimatePresence>
            {filteredRows.length === 0 && !loading && (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-3 py-8 text-center text-text-faint"
                >
                  No actionable setups right now.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
