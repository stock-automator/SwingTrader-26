import { fmtCurrency, fmtNum, signClass } from "../../lib/format";
import type { JournalTrade } from "../../types";

interface JournalTradesTableProps {
  trades: JournalTrade[];
}

const STATUS_BADGE: Record<string, string> = {
  TAKEN: "text-accent border-accent/40",
  PENDING: "text-amber border-amber-dim",
  SKIPPED: "text-text-faint border-border-soft",
};

export function JournalTradesTable({ trades }: JournalTradesTableProps) {
  if (trades.length === 0) {
    return (
      <div
        data-testid="journal-trades-empty"
        className="rounded border border-border bg-panel px-3 py-8 text-center text-sm text-text-faint"
      >
        No trades logged yet.
      </div>
    );
  }

  const sorted = [...trades].sort((a, b) => b.id - a.id);

  return (
    <div
      data-testid="journal-trades-table"
      className="max-h-[420px] overflow-auto rounded border border-border bg-panel"
    >
      <table className="w-full min-w-[900px] border-collapse text-sm">
        <thead className="sticky top-0 bg-panel">
          <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-faint">
            <th className="px-3 py-2">Ticker</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Entry</th>
            <th className="px-3 py-2">Exit</th>
            <th className="px-3 py-2 text-right">Entry Px</th>
            <th className="px-3 py-2 text-right">Exit Px</th>
            <th className="px-3 py-2">Exit Reason</th>
            <th className="px-3 py-2 text-right">P&amp;L</th>
            <th className="px-3 py-2 text-right">R</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((t) => (
            <tr
              key={t.id}
              data-testid="journal-trade-row"
              className="border-b border-border-soft last:border-0 hover:bg-panel-alt"
            >
              <td className="px-3 py-2 font-semibold">{t.ticker}</td>
              <td className="px-3 py-2">
                <span
                  className={`rounded border px-1.5 py-0.5 text-[11px] ${
                    STATUS_BADGE[t.entry_status ?? ""] ??
                    "text-text-faint border-border-soft"
                  }`}
                >
                  {t.entry_status ?? "—"}
                </span>
              </td>
              <td className="px-3 py-2 text-text-dim">
                {(t.actual_entry_date ?? t.entry_date ?? "—").slice(0, 10)}
              </td>
              <td className="px-3 py-2 text-text-dim">
                {t.exit_date ? t.exit_date.slice(0, 10) : "—"}
              </td>
              <td className="px-3 py-2 text-right">
                {fmtNum(t.actual_entry_price ?? t.entry_price)}
              </td>
              <td className="px-3 py-2 text-right">{fmtNum(t.exit_price)}</td>
              <td className="px-3 py-2 text-text-dim">{t.exit_reason ?? "—"}</td>
              <td className={`px-3 py-2 text-right ${signClass(t.pnl)}`}>
                {t.pnl === null ? "—" : fmtCurrency(t.pnl, 2)}
              </td>
              <td className={`px-3 py-2 text-right ${signClass(t.r_multiple)}`}>
                {t.r_multiple === null ? "—" : `${t.r_multiple.toFixed(2)}R`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
