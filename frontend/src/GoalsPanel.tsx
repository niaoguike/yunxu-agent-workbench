import { personalStatus } from "./personalStatus";
import React, { useEffect, useState } from "react";
export function GoalsPanel({
  projectId,
  api,
  all = false,
}: {
  projectId: string;
  all?: boolean;
  api: any;
}) {
  const [goals, setGoals] = useState<any[]>([]),
    [error, setError] = useState("");
  const load = async () =>
    setGoals(
      (await api("/goals")).filter(
        (g: any) => all || g.project_id === projectId,
      ),
    );
  useEffect(() => {
    load().catch((e) => setError(e.message));
    const t = setInterval(() => load().catch((e) => setError(e.message)), 5000);
    return () => clearInterval(t);
  }, [projectId, all]);
  const act = async (fn: () => Promise<any>) => {
    try {
      await fn();
      await load();
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  };
  return (
    <section>
      <h2>我的目标</h2>
      {error && <p role="alert">{error}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = e.currentTarget;
          const d = new FormData(f);
          act(async () => {
            await api("/goals", "POST", {
              project_id: projectId,
              title: d.get("title"),
              desired_outcome: d.get("outcome"),
              success_criteria: String(d.get("criteria"))
                .split("\n")
                .filter(Boolean),
              kind: d.get("kind"),
              permission_mode: d.get("permission_mode"),
            });
            f.reset();
          });
        }}
      >
        <label>
          目标名称
          <input name="title" required maxLength={240} />
        </label>
        <label>
          预期结果
          <textarea name="outcome" required />
        </label>
        <label>
          成功标准（每行一项）
          <textarea name="criteria" required />
        </label>
        <label>
          类型
          <select name="kind">
            <option value="goal">有终点的目标</option>
            <option value="responsibility">持续责任</option>
            <option value="watch">条件关注</option>
          </select>
        </label>
        <label>
          工具授权
          <select name="permission_mode">
            <option value="ask">每次需要时由我授权</option>
            <option value="auto">使用原生自动风险审核</option>
          </select>
        </label>
        <button>创建目标</button>
      </form>
      {goals.map((g) => (
        <article key={g.id}>
          <h3>{g.config.title}</h3>

          <p>{g.config.desired_outcome}</p>
          <p>
            {personalStatus(g.status)} · {g.reason || "等待推进"}
          </p>
          <ul>
            {g.config.success_criteria.map((c: string, i: number) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
          {!["completed", "cancelled"].includes(g.status) && (
            <>
              <button
                onClick={() =>
                  act(() =>
                    api(`/goals/${g.id}/transition`, "POST", {
                      revision: g.revision,
                      status: "active",
                    }),
                  )
                }
              >
                启动 / 恢复
              </button>
              <button
                onClick={() =>
                  act(() =>
                    api(`/goals/${g.id}/transition`, "POST", {
                      revision: g.revision,
                      status: "paused",
                    }),
                  )
                }
              >
                暂停
              </button>
              <button
                onClick={() =>
                  act(() =>
                    api(`/goals/${g.id}/transition`, "POST", {
                      revision: g.revision,
                      status: "cancelled",
                    }),
                  )
                }
              >
                取消
              </button>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const d = new FormData(e.currentTarget);
                  const evidence = Object.fromEntries(
                    g.config.success_criteria.map((_: string, i: number) => [
                      String(i),
                      d.get("evidence" + i),
                    ]),
                  );
                  act(() =>
                    api(`/goals/${g.id}/transition`, "POST", {
                      revision: g.revision,
                      status: "completed",
                      evidence,
                    }),
                  );
                }}
              >
                {g.config.success_criteria.map((c: string, i: number) => (
                  <label key={i}>
                    {c} 的完成证据
                    <input name={"evidence" + i} required />
                  </label>
                ))}
                <button>确认目标完成</button>
              </form>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = e.currentTarget;
                  const content = new FormData(f).get("content");
                  act(async () => {
                    await api(`/goals/${g.id}/inputs`, "POST", { content });
                    f.reset();
                  });
                }}
              >
                <label>
                  补充要求
                  <input name="content" required />
                </label>
                <button>保存补充</button>
              </form>
            </>
          )}
          <details>
            <summary>推进记录</summary>
            {g.events.map((v: any) => (
              <p key={v.id}>
                {v.kind} · {personalStatus(v.status)} · {v.content}
                {v.status === "unknown" && (
                  <button
                    onClick={() => {
                      const content = window.prompt(
                        "检查关联任务的实际结果后填写核查结论。继续执行可能涉及重复操作，请明确剩余工作。",
                      );
                      if (content?.trim())
                        act(() =>
                          api(
                            `/goals/${g.id}/events/${v.id}/acknowledge`,
                            "POST",
                            { content },
                          ),
                        );
                    }}
                  >
                    已核查中断结果
                  </button>
                )}
                {v.task_id && ` · 任务 ${v.task_id}`}
              </p>
            ))}
          </details>
        </article>
      ))}
    </section>
  );
}
