import { useEffect, useState } from "react";
import { ApiError, getOrderTickets } from "../lib/api";
import { fmtCurrency, fmtNum } from "../lib/format";
import type { OrderTicket, ScreenerSetup } from "../types";

interface OrderTicketDrawerProps {
  setup: ScreenerSetup | null;
  onClose: () => void;
  /** Account sizes to price the ticket at. Defaults to $1k/$5k/$10k. */
  accountTiers?: number[];
}

const DEFAULT_ACCOUNT_TIERS = [1000, 5000, 10000];

function ticketText(ticket: OrderTicket): string {
  return [
    `${ticket.ticker} - ${ticket.order_type}`,
    `Account: ${fmtCurrency(ticket.account_equity)}`,
    `Entry: ${fmtNum(ticket.entry_price, 2)}`,
    `Stop Loss: ${fmtNum(ticket.stop_loss, 2)}`,
    `Take Profit: ${fmtNum(ticket.take_profit, 2)}`,
    `Quantity: ${ticket.quantity}`,
    `Notional: ${fmtCurrency(ticket.notional_value)}`,
    `R-Multiple: ${fmtNum(ticket.reward_risk_ratio, 1)}R`,
  ].join("\n");
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="rounded border border-border bg-panel-alt px-2 py-1 text-xs text-text hover:border-accent"
    >
      {copied ? "Copied ✓" : "Copy"}
    </button>
  );
}

function TicketCard({ ticket }: { ticket: OrderTicket }) {
  return (
    <div className="rounded border border-border bg-panel p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-sm font-semibold text-text">
          {fmtCurrency(ticket.account_equity, 0)} account
        </div>
        <CopyButton text={ticketText(ticket)} />
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-text-faint">Order Type</dt>
        <dd className="text-right">{ticket.order_type}</dd>
        <dt className="text-text-faint">Entry</dt>
        <dd className="text-right">{fmtNum(ticket.entry_price, 2)}</dd>
        <dt className="text-text-faint">Stop Loss</dt>
        <dd className="text-right text-short">{fmtNum(ticket.stop_loss, 2)}</dd>
        <dt className="text-text-faint">Take Profit</dt>
        <dd className="text-right text-long">
          {fmtNum(ticket.take_profit, 2)}
        </dd>
        <dt className="text-text-faint">Quantity</dt>
        <dd className="text-right">{ticket.quantity}</dd>
        <dt className="text-text-faint">Notional Value</dt>
        <dd className="text-right">{fmtCurrency(ticket.notional_value)}</dd>
        <dt className="text-text-faint">Risk Amount</dt>
        <dd className="text-right">{fmtCurrency(ticket.risk_amount)}</dd>
        <dt className="text-text-faint">R-Multiple</dt>
        <dd className="text-right">{fmtNum(ticket.reward_risk_ratio, 1)}R</dd>
      </dl>
      {!ticket.tradable && (
        <div className="mt-2 rounded border border-amber-dim bg-amber-dim/20 px-2 py-1 text-xs text-amber">
          {ticket.note ?? "Not tradable at this account size."}
        </div>
      )}
    </div>
  );
}

export function OrderTicketDrawer({
  setup,
  onClose,
  accountTiers = DEFAULT_ACCOUNT_TIERS,
}: OrderTicketDrawerProps) {
  const [tickets, setTickets] = useState<OrderTicket[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (
      !setup ||
      setup.entry_price == null ||
      setup.stop_loss == null ||
      setup.take_profit == null
    ) {
      return;
    }
    const slValue = Math.abs(setup.entry_price - setup.stop_loss);
    const tpValue = Math.abs(setup.take_profit - setup.entry_price);

    setLoading(true);
    setError(null);
    getOrderTickets({
      ticker: setup.ticker,
      entry_price: setup.entry_price,
      sl_type: "FIXED",
      sl_value: slValue,
      tp_type: "FIXED",
      tp_value: tpValue,
      direction: 1,
      account_tiers: accountTiers,
    })
      .then((res) => setTickets(res.tickets))
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [setup, accountTiers]);

  const isOpen = setup !== null;

  return (
    <>
      <div
        onClick={onClose}
        className={`fixed inset-0 z-20 bg-black/50 transition-opacity ${
          isOpen ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      <aside
        className={`fixed right-0 top-0 z-30 flex h-full w-full max-w-sm flex-col border-l border-border bg-bg shadow-2xl transition-transform duration-200 ${
          isOpen ? "translate-x-0" : "translate-x-full"
        }`}
        data-testid="order-ticket-drawer"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="text-sm font-bold tracking-wide text-text">
            {setup ? `${setup.ticker} Order Ticket` : "Order Ticket"}
          </div>
          <button
            onClick={onClose}
            className="rounded px-2 py-1 text-sm text-text-dim hover:text-text"
          >
            ✕
          </button>
        </div>

        <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
          {loading && <div className="text-sm text-text-dim">Loading…</div>}
          {error && (
            <div className="rounded border border-short-dim bg-short-dim/20 px-3 py-2 text-sm text-short">
              {error}
            </div>
          )}
          {!loading &&
            !error &&
            tickets.map((t) => (
              <TicketCard key={t.account_equity} ticket={t} />
            ))}
        </div>
      </aside>
    </>
  );
}
