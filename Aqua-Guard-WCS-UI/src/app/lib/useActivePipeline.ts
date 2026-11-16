import { usePolling } from "./usePolling";
import { v2, type PipelineListItem, type PipelineStatus } from "./v2";

/** The first pipeline with an active version and its live zone analysis. `status` is null while there is none. */
export function useActivePipeline(intervalMs = 3000) {
  const list = usePolling(() => v2.pipelines(), 10000);
  const active: PipelineListItem | null = list.data?.pipelines.find((p) => p.activeVersion) ?? null;
  const status = usePolling<PipelineStatus | null>(() => (active ? v2.pipelineStatus(active.id) : Promise.resolve(null)), intervalMs, [active?.id, active?.activeVersion]);
  return { list, active, status, loading: list.loading || (!!active && status.loading), error: list.error || status.error, stale: list.stale || status.stale, refresh: () => { list.refresh(); status.refresh(); } };
}
