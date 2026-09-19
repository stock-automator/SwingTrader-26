import { useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowDown, ArrowUp, ArrowUpDown, Ticket } from "lucide-react";
import { toast } from "sonner";
import { useScanContext } from "../lib/ScanContext";
import { useOpportunityFeed } from "../lib/useOpportunityFeed";
import { fmtNum } from "../lib/format";
import { OrderTicketDrawer } from "../components/OrderTicketDrawer";
import { STRATEGIES, type Direction, type OpportunitySetup, type ScreenerSetup } from "../types";

const STRATEGY_LABELS: Record<string, string> = Object.fromEntries(
  STRATEGIES.map((s) => [s.value, s.label]),
);

const DIRECTION_BADGE: Record<Direction, string> = {
  LONG: "text-long bg-long-dim/40 border-long-dim",
  SHORT: "text-short bg-short-dim/40 border-short-dim",
  EXIT_LONG: "text-orange-400 bg-orange-900/30 border-orange-800",
  FLAT: "text-text-faint bg-transparent border-border-soft",
};

// Cumulative account risk allowed across displayed "active" setups, assuming
// 1% risk per setup unless the data itself ever supplies a different figure.
const RISK_PER_SETUP_PCT = 1;
const MAX_PORTFOLIO_HEAT_PCT = 6;
const MAX_ACTIVE_SETUPS = Math.floor(MAX_PORTFOLIO_HEAT_PCT / RISK_PER_SETUP_PCT);

function setupKey(s: OpportunitySetup): string {
  return `${s.ticker}:${s.strategy}`;
}

/** Rank setups by Setup Quality Score and mark any beyond the portfolio
 * heat cap (top MAX_ACTIVE_SETUPS by score keep their risk "on"; the rest
 * are flagged CAPPED_BY_PORTFOLIO_HEAT but stay visible, just dimmed). */
function applyPortfolioHeatCap(setups: OpportunitySetup[]): OpportunitySetup[] {
  const eligible = setups.filter(
    (s) => s.direction === "LONG" || s.direction === "SHORT",
  );
  const rankedKeys = [...eligible]
    .sort((a, b) => b.setup_quality_score - a.setup_quality_score)
    .map(setupKey);
  const cappedKeys = new Set(rankedKeys.slice(MAX_ACTIVE_SETUPS));

  return setups.map((s) =>
    cappedKeys.has(setupKey(s))
      ? { ...s, portfolio_heat_capped: true, capped_reason: "CAPPED_BY_PORTFOLIO_HEAT" }
      : { ...s, portfolio_heat_capped: false, capped_reason: null },
  );
}

function toScreenerSetup(o: OpportunitySetup): ScreenerSetup {
  return {
    ticker: o.ticker,
    direction: o.direction,
    tradable: o.tradable,
    as_of: o.as_of,
    close: o.close,
    // Regime/ADX/ATR/relative-strength/rank aren't part of the opportunity
    // feed shape yet - OrderTicketDrawer only needs entry/stop/target to
    // price tickets, so these are filled with neutral placeholders.
    regime: "UNKNOWN",
    adx: null,
    atr: null,
    entry_price: o.entry_price,
    stop_loss: o.stop_loss,
    take_profit: o.take_profit,
    shares: o.shares,
    risk_amount: o.risk_amount,
    relative_strength: null,
    rank: null,
    note: o.note,
    reward_risk_ratio: o.reward_risk_ratio,
    notional_value: o.notional_value,
  };
}

function DirectionBadge({ direction }: { direction: Direction }) {
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-xs font-medium ${DIRECTION_BADGE[direction]}`}
    >
      {direction}
    </span>
  );
}

function ScanStatusPill() {
  const { activeScans, startScan } = useScanContext();
  const [triggering, setTriggering] = useState(false);
  const isScanning = activeScans.length > 0;

  async function handleScan() {
    setTriggering(true);
    try {
      await startScan();
      toast.success("Background scan started");
    } catch (err) {
      toast.error(
        `Couldn't start background scan: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setTriggering(false);
    }
  }

  return (
    <button
      onClick={handleScan}
      disabled={triggering}
      data-testid="scan-status-pill"
      className="relative flex items-center gap-2 rounded border border-border bg-panel-alt px-3 py-1.5 text-xs text-text hover:border-accent disabled:opacity-50"
    >
      <span className="relative flex h-2 w-2">
        {isScanning && (
          <motion.span
            className="absolute inline-flex h-full w-full rounded-full bg-accent"
            animate={{ opacity: [0.4, 1, 0.4], scale: [1, 1.8, 1] }}
            transition={{ repeat: Infinity, duration: 1.6 }}
          />
        )}
        <span
          className={`relative inline-flex h-2 w-2 rounded-full ${isScanning ? "bg-accent" : "bg-text-faint"}`}
        />
      </span>
      {isScanning ? `Scanning… (${activeScans.length})` : "Run Background Scan"}
    </button>
  );
}

const columnHelper = createColumnHelper<OpportunitySetup>();

export function Dashboard() {
  const { setups, loading, error, refresh } = useOpportunityFeed();
  const [sorting, setSorting] = useState<SortingState>([
    { id: "setup_quality_score", desc: true },
  ]);
  const [ticketSetup, setTicketSetup] = useState<OpportunitySetup | null>(null);

  const rankedSetups = useMemo(() => applyPortfolioHeatCap(setups), [setups]);

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
      columnHelper.accessor("entry_price", {
        header: "Entry",
        cell: (info) => fmtNum(info.getValue()),
      }),
      columnHelper.accessor("stop_loss", {
        header: "Stop",
        cell: (info) => <span className="text-short">{fmtNum(info.getValue())}</span>,
      }),
      columnHelper.accessor("take_profit", {
        header: "Target",
        cell: (info) => <span className="text-long">{fmtNum(info.getValue())}</span>,
      }),
      columnHelper.accessor("note", {
        header: "Trigger Reason",
        cell: (info) => {
          const v = info.getValue();
          return v ? (
            <span className="inline-block max-w-[220px] truncate rounded border border-border-soft bg-panel-alt px-1.5 py-0.5 text-xs text-text-dim" title={v}>
              {v}
            </span>
          ) : (
            <span className="text-text-faint">—</span>
          );
        },
      }),
      columnHelper.accessor("win_probability", {
        header: "Win Prob.",
        cell: (info) => {
          const v = info.getValue();
          return v === null ? (
            <span className="text-text-faint">—</span>
          ) : (
            <span>{(v * 100).toFixed(0)}%</span>
          );
        },
      }),
      columnHelper.accessor("r_multiple", {
        header: "R-Multiple",
        cell: (info) => `${info.getValue().toFixed(1)}R`,
      }),
      columnHelper.accessor("setup_quality_score", {
        header: "Quality",
        cell: (info) => (info.getValue() * 100).toFixed(0),
      }),
      columnHelper.display({
        id: "heat",
        header: "",
        cell: (info) => {
          const row = info.row.original;
          return row.portfolio_heat_capped ? (
            <span
              data-testid="portfolio-heat-capped-badge"
              className="rounded border border-amber-dim bg-amber-dim/30 px-1.5 py-0.5 text-[10px] font-semibold text-amber"
            >
              {row.capped_reason ?? "CAPPED_BY_PORTFOLIO_HEAT"}
            </span>
          ) : null;
        },
      }),
      columnHelper.display({
        id: "action",
        header: "",
        cell: (info) => {
          const row = info.row.original;
          return (
            <button
              onClick={() => setTicketSetup(row)}
              data-testid="dashboard-trade-button"
              disabled={row.entry_price === null || row.stop_loss === null || row.take_profit === null}
              className="flex items-center gap-1 rounded border border-border bg-panel-alt px-2 py-1 text-xs text-text hover:border-accent disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Ticket size={12} />
              Trade
            </button>
          );
        },
      }),
    ],
    [],
  );

  const table = useReactTable({
    data: rankedSetups,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    // Row identity is ticker+strategy (not array index) so a re-sort or a
    // refetch that reorders existing setups never looks like a new row -
    // only a setup that is genuinely absent from the previous feed mounts
    // fresh, which is what drives the arrival animation below via
    // AnimatePresence's normal enter transition.
    getRowId: (row) => setupKey(row),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 rounded border border-border bg-panel p-3">
        <div className="text-sm font-semibold text-text">
          Live Opportunity Dashboard
        </div>
        <div className="text-xs text-text-dim">
          {rankedSetups.length} setups · portfolio heat cap {MAX_ACTIVE_SETUPS} setups ({MAX_PORTFOLIO_HEAT_PCT}% @ {RISK_PER_SETUP_PCT}%/setup)
        </div>
        <div className="ml-auto flex items-center gap-3">
          <ScanStatusPill />
          <button
            onClick={refresh}
            disabled={loading}
            className="rounded border border-border bg-panel-alt px-3 py-1.5 text-xs text-text hover:border-accent disabled:opacity-50"
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
          {error}
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
                      {flexRender(header.column.columnDef.header, header.getContext())}
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
              {table.getRowModel().rows.map((row) => {
                const setup = row.original;
                const isLong = setup.direction === "LONG";
                const targetOpacity = setup.portfolio_heat_capped ? 0.45 : 1;
                // initial only fires on first mount of this key (a genuinely
                // new ticker+strategy setup, per getRowId above) - re-renders
                // of an already-mounted row (e.g. a 30s data refresh) skip
                // straight to `animate`'s resting state, so the colored
                // flash never replays for existing rows.
                return (
                  <motion.tr
                    key={row.id}
                    layout
                    data-testid="dashboard-setup-row"
                    initial={{
                      opacity: 0,
                      scale: 0.92,
                      backgroundColor: isLong
                        ? "rgba(46,230,160,0.35)"
                        : "rgba(255,92,114,0.35)",
                    }}
                    animate={{
                      opacity: targetOpacity,
                      scale: 1,
                      backgroundColor: "rgba(0,0,0,0)",
                    }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.9 }}
                    className="border-b border-border-soft last:border-0 hover:bg-panel-alt"
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-3 py-2">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </motion.tr>
                );
              })}
            </AnimatePresence>
            {rankedSetups.length === 0 && !loading && (
              <tr>
                <td colSpan={columns.length} className="px-3 py-8 text-center text-text-faint">
                  No actionable setups right now.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <OrderTicketDrawer
        setup={ticketSetup ? toScreenerSetup(ticketSetup) : null}
        onClose={() => setTicketSetup(null)}
        accountTiers={[1000, 5000, 10000]}
      />
    </div>
  );
}
