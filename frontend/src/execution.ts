type Run = {
  id: string;
  version: number;
  status: string;
  error?: string | null;
  created_at: number;
};
type Check = {
  id: string;
  run_id: string;
  name: string;
  status: string;
  source?: string;
  evidence?: string;
};

export function executionView(
  version: number,
  runs: Run[] = [],
  checks: Check[] = [],
) {
  const ordered = [...runs].sort((a, b) => a.created_at - b.created_at);
  const currentRun = ordered.filter((run) => run.version === version).at(-1);
  return {
    currentRun,
    currentError:
      currentRun?.status === "failed" ? currentRun.error || null : null,
    currentChecks: checks.filter((check) => check.run_id === currentRun?.id),
    historyRuns: ordered.filter((run) => run.id !== currentRun?.id).reverse(),
  };
}
