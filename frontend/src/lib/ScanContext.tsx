import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { getScanStatus, startScan as apiStartScan } from "./api";
import type { ScanJob, ScanJobStatus } from "../types";

const TERMINAL_STATUSES: ScanJobStatus[] = ["completed", "failed"];

function scanQueryKey(jobId: string) {
  return ["scan", jobId] as const;
}

interface ScanContextValue {
  /** All scan jobs currently tracked (any status), most recent first. */
  jobIds: string[];
  /** Jobs whose last known status is pending/running. */
  activeScans: ScanJob[];
  /** Kick off a new background scan; returns the new job id. */
  startScan: (params?: Parameters<typeof apiStartScan>[0]) => Promise<string>;
  /** Look up the current cached status for a job id, if known. */
  scanStatus: (jobId: string) => ScanJob | undefined;
}

const ScanContext = createContext<ScanContextValue | null>(null);

export function ScanProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [jobIds, setJobIds] = useState<string[]>([]);

  // One polling query per tracked job. React Query's cache (keyed by
  // ["scan", jobId]) plus this component's own state both live above the
  // tab-switching <main> in App.tsx, so neither is torn down when the user
  // navigates away from the Dashboard tab - polling keeps running and the
  // results are still there when they come back.
  const queries = useQueries({
    queries: jobIds.map((jobId) => ({
      queryKey: scanQueryKey(jobId),
      queryFn: () => getScanStatus(jobId),
      refetchInterval: (query: { state: { data?: ScanJob } }) => {
        const status = query.state.data?.status;
        return status && TERMINAL_STATUSES.includes(status) ? false : 2000;
      },
    })),
  });

  const activeScans = useMemo(
    () =>
      queries
        .map((q) => q.data)
        .filter(
          (job): job is ScanJob =>
            job !== undefined && !TERMINAL_STATUSES.includes(job.status),
        ),
    [queries],
  );

  const startScan = useCallback(
    async (params?: Parameters<typeof apiStartScan>[0]) => {
      const { job_id } = await apiStartScan(params);
      // Seed the cache immediately so activeScans reflects the new job
      // before the first poll resolves.
      queryClient.setQueryData<ScanJob>(scanQueryKey(job_id), {
        job_id,
        status: "pending",
        created_at: new Date().toISOString(),
        completed_at: null,
        error: null,
        results: null,
      });
      setJobIds((prev) => [job_id, ...prev]);
      return job_id;
    },
    [queryClient],
  );

  const scanStatus = useCallback(
    (jobId: string) => queryClient.getQueryData<ScanJob>(scanQueryKey(jobId)),
    [queryClient],
  );

  const value = useMemo<ScanContextValue>(
    () => ({ jobIds, activeScans, startScan, scanStatus }),
    [jobIds, activeScans, startScan, scanStatus],
  );

  return (
    <ScanContext.Provider value={value}>{children}</ScanContext.Provider>
  );
}

export function useScanContext(): ScanContextValue {
  const ctx = useContext(ScanContext);
  if (!ctx) {
    throw new Error("useScanContext must be used within a ScanProvider");
  }
  return ctx;
}
