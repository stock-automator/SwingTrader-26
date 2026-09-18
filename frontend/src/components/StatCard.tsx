interface StatCardProps {
  label: string;
  value: string;
  valueClassName?: string;
  sub?: string;
}

export function StatCard({ label, value, valueClassName, sub }: StatCardProps) {
  return (
    <div className="rounded border border-border bg-panel px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-text-faint">
        {label}
      </div>
      <div
        className={`mt-1 text-xl font-semibold ${valueClassName ?? "text-text"}`}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-text-dim">{sub}</div>}
    </div>
  );
}
