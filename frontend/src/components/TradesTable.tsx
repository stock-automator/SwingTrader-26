import { fmtCurrency, fmtNum, fmtPct, signClass } from "../lib/format";
import type { Trade } from "../types";

interface TradesTableProps {
  trades: Trade[];
}

export function TradesTable({ trades }: TradesTableProps) {
  if (trades.length === 0) {
    return (
      <div className="rounded border border-border bg-panel px-3 py-8 text-center text-sm text-text-faint">
        No trades in this run.
      </div>
    );
  }

  return (
    <div className="max-h-[420px] overflow-auto rounded border border-border bg-panel">
      <table className="w-full min-w-[820px] border-collapse text-sm">
        <thead className="sticky top-0 bg-panel">
          <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-text-faint">
            <th className="px-3 py-2">Ticker</th>
            <th className="px-3 py-2">Entry</th>
            <th className="px-3 py-2">Exit</th>
            <th className="px-3 py-2 text-right">Entry Px</th>
            <th className="px-3 py-2 text-right">Exit Px</th>
            <th className="px-3 py-2 text-right">Size</th>
            <th className="px-3 py-2 text-right">P&amp;L</th>
            <th className="px-3 py-2 text-right">Return</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr
              key={i}
              className="border-b border-border-soft last:border-0 hover:bg-panel-alt"
            >
              <td className="px-3 py-2 font-semibold">{t.ticker}</td>
              <td className="px-3 py-2 text-text-dim">{t.entry_time}</td>
              <td className="px-3 py-2 text-text-dim">{t.exit_time}</td>
              <td className="px-3 py-2 text-right">{fmtNum(t.entry_price)}</td>
              <td className="px-3 py-2 text-right">{fmtNum(t.exit_price)}</td>
              <td className="px-3 py-2 text-right">{fmtNum(t.size)}</td>
              <td className={`px-3 py-2 text-right ${signClass(t.pnl)}`}>
                {fmtCurrency(t.pnl, 2)}
              </td>
              <td className={`px-3 py-2 text-right ${signClass(t.return_pct)}`}>
                {fmtPct(t.return_pct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
