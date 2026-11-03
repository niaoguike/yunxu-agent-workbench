import test from "node:test";
import assert from "node:assert/strict";
import { executionView } from "../src/execution.ts";

const failed = {
  id: "failed",
  version: 2,
  status: "failed",
  error: "startup-error.txt missing",
  created_at: 100,
};
const completed = {
  id: "completed",
  version: 3,
  status: "completed",
  error: null,
  created_at: 200,
};
const checks = [
  { id: "old", run_id: "failed", name: "browser", status: "unverified" },
  { id: "new", run_id: "completed", name: "files", status: "passed" },
];

test("successful retry shows only its checks and keeps past errors in history", () => {
  const view = executionView(3, [completed, failed], checks);
  assert.equal(view.currentRun.id, "completed");
  assert.equal(view.currentError, null);
  assert.deepEqual(
    view.currentChecks.map((c) => c.id),
    ["new"],
  );
  assert.deepEqual(
    view.historyRuns.map((r) => r.id),
    ["failed"],
  );
  assert.equal(view.historyRuns[0].error, "startup-error.txt missing");
});

test("latest failure remains visible until a new run starts", () => {
  const newest = {
    id: "failed-again",
    version: 3,
    status: "failed",
    error: "current check failed",
    created_at: 300,
  };
  const view = executionView(3, [failed, completed, newest], checks);
  assert.equal(view.currentError, "current check failed");
  const running = {
    id: "retrying",
    version: 3,
    status: "running",
    error: null,
    created_at: 400,
  };
  assert.equal(
    executionView(3, [failed, completed, newest, running], checks).currentError,
    null,
  );
});

test("a new requirement version does not inherit old run errors or checks", () => {
  const view = executionView(4, [failed, completed], checks);
  assert.equal(view.currentRun, undefined);
  assert.equal(view.currentError, null);
  assert.deepEqual(view.currentChecks, []);
  assert.equal(view.historyRuns.length, 2);
});
