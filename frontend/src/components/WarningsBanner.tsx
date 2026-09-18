import { useState } from "react";

interface WarningsBannerProps {
  warnings: string[];
}

export function WarningsBanner({ warnings }: WarningsBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed || warnings.length === 0) return null;

  return (
    <div className="flex items-start justify-between gap-4 rounded border border-amber-dim bg-amber-dim/20 px-4 py-2 text-sm text-amber">
      <ul className="list-inside list-disc space-y-0.5">
        {warnings.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-text-dim hover:text-text"
        aria-label="Dismiss warnings"
      >
        ✕
      </button>
    </div>
  );
}
