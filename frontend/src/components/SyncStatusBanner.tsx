import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { RefreshCw, DatabaseZap, CircleAlert } from "lucide-react";
import { toast } from "sonner";
import { getDataSyncStatus, startDataSync } from "../lib/api";
import type { DataSyncStatusResponse } from "../types";

//: Fast poll while a sync is actively running, so progress feels live;
//: slow poll otherwise - there's no reason to hammer the endpoint when
//: nothing is changing.
const ACTIVE_POLL_MS = 3000;
const IDLE_POLL_MS = 30000;

export function SyncStatusBanner() {
  const [status, setStatus] = useState<DataSyncStatusResponse | null>(null);
  const [queuedCount, setQueuedCount] = useState<number | null>(null);
  const [triggering, setTriggering] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const next = await getDataSyncStatus();
      setStatus(next);
    } catch {
      // Sync status is best-effort UI chrome - a failed poll just tries
      // again next tick rather than surfacing an error toast every 3s.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      await poll();
      if (cancelled) return;
      timerRef.current = setTimeout(
        tick,
        status?.in_progress ? ACTIVE_POLL_MS : IDLE_POLL_MS,
      );
    }
    tick();

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.in_progress]);

  async function handleSyncNow() {
    setTriggering(true);
    try {
      const res = await startDataSync();
      setQueuedCount(res.tickers.length);
      toast.success(`Sync started for ${res.tickers.length} ticker(s)`);
      await poll();
    } catch (err) {
      toast.error(
        `Failed to start sync: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setTriggering(false);
    }
  }

  const resultCount = status?.results.length ?? 0;
  const latest = status?.results[status.results.length - 1] ?? null;
  const neverSynced = !status?.in_progress && resultCount === 0 && !status?.started_at;

  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-panel-alt/60 px-3 py-2 text-xs backdrop-blur">
      <AnimatePresence mode="wait">
        {status?.in_progress ? (
          <motion.div
            key="active"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex items-center gap-2"
          >
            <motion.span
              animate={{ opacity: [0.4, 1, 0.4] }}
              transition={{ duration: 1.4, repeat: Infinity }}
            >
              <DatabaseZap size={14} className="text-accent" />
            </motion.span>
            <span className="text-text">
              Syncing… {resultCount}
              {queuedCount ? ` / ${queuedCount}` : ""} tickers done
            </span>
            {latest && (
              <span className="text-text-faint">
                (last: {latest.ticker} · {latest.status})
              </span>
            )}
          </motion.div>
        ) : neverSynced ? (
          <motion.div
            key="never"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex items-center gap-2 text-text-faint"
          >
            <CircleAlert size={14} className="text-amber" />
            Parquet cache never synced this session
          </motion.div>
        ) : (
          <motion.div
            key="idle"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex items-center gap-2 text-text-dim"
          >
            <DatabaseZap size={14} />
            {resultCount} ticker(s) synced
            {status?.started_at && (
              <span className="text-text-faint">
                · last run {new Date(status.started_at).toLocaleTimeString()}
              </span>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <button
        onClick={handleSyncNow}
        disabled={triggering || !!status?.in_progress}
        data-testid="sync-now-button"
        className="ml-auto flex items-center gap-1.5 rounded border border-border bg-panel px-2 py-1 text-text hover:border-accent disabled:cursor-not-allowed disabled:opacity-50"
      >
        <RefreshCw
          size={12}
          className={triggering || status?.in_progress ? "animate-spin" : ""}
        />
        Sync Now
      </button>
    </div>
  );
}
