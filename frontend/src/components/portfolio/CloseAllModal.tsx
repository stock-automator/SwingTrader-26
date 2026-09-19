import { useState } from "react";
import { TriangleAlert } from "lucide-react";

interface CloseAllModalProps {
  open: boolean;
  positionCount: number;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}

export function CloseAllModal({
  open,
  positionCount,
  onCancel,
  onConfirm,
}: CloseAllModalProps) {
  const [submitting, setSubmitting] = useState(false);

  if (!open) return null;

  async function handleConfirm() {
    setSubmitting(true);
    try {
      await onConfirm();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 px-4">
      <div
        data-testid="close-all-modal"
        role="alertdialog"
        aria-modal="true"
        className="w-full max-w-sm rounded-lg border border-short-dim bg-panel p-5 shadow-2xl"
      >
        <div className="flex items-center gap-2 text-short">
          <TriangleAlert size={18} />
          <div className="text-sm font-bold tracking-wide">
            Close All Positions
          </div>
        </div>
        <p className="mt-3 text-sm text-text-dim">
          This immediately liquidates all {positionCount} open paper
          position{positionCount === 1 ? "" : "s"} and cancels every open
          order. This cannot be undone.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={submitting}
            data-testid="close-all-cancel"
            className="rounded border border-border bg-panel-alt px-3 py-1.5 text-sm text-text hover:border-text-faint disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={submitting}
            data-testid="close-all-confirm"
            className="rounded border border-short bg-short-dim/40 px-3 py-1.5 text-sm font-semibold text-short hover:bg-short-dim/60 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "Closing…" : "Close All Positions"}
          </button>
        </div>
      </div>
    </div>
  );
}
