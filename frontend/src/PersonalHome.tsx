import { personalStatus } from "./personalStatus";
import { VoiceInput } from "./VoiceInput";
import { PersonalTools } from "./PersonalTools";
import React, { useEffect, useState } from "react";
import { GoalsPanel } from "./GoalsPanel";
export function PersonalHome({
  api,
  openTask,
}: {
  api: any;
  openTask: (id: string) => void;
}) {
  const [home, H] = useState<any>(null),
    [error, E] = useState(""),
    [draft, D] = useState("");
  const load = async () => H(await api("/personal"));
  useEffect(() => {
    let alive = true;
    const refresh = () => {
      if (alive) load().catch((e) => E(e.message));
    };
    refresh();
    const t = setInterval(refresh, 4000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  const act = async (f: () => Promise<any>) => {
    try {
      await f();
      await load();
      E("");
    } catch (e: any) {
      E(e.message);
    }
  };
  return (
    <section className="personal-home">
      <h1>你的个人助手 · 云序</h1>
      <p>交办工作，补充要求，查看进展与需要你决定的事项。</p>
      {error && <p role="alert">{error}</p>}
      {home && (
        <>
          <p>当前状态：{personalStatus(home.task.status)}</p>
          <button onClick={() => openTask(home.task_id)}>
            查看执行、授权与协作者
          </button>
          <div aria-live="polite">
            {home.messages.map((m: any) => (
              <article key={m.id}>
                <strong>{m.role === "user" ? "你" : "云序"}</strong>
                <p style={{ whiteSpace: "pre-wrap" }}>{m.content}</p>
              </article>
            ))}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = e.currentTarget;
              const content = new FormData(f).get("content");
              act(async () => {
                await api("/personal/messages", "POST", { content });
                f.reset();
                D("");
              });
            }}
          >
            <label>
              交给云序的工作
              <textarea
                name="content"
                required
                maxLength={100000}
                value={draft}
                onChange={(e) => D(e.target.value)}
              />
            </label>
            <VoiceInput onTranscript={(text) => D(text)} />
            <button>发送</button>
          </form>
          <GoalsPanel projectId={home.project_id} api={api} all />
          <section>
            <h2>协作者成果</h2>
            {home.reports?.length ? (
              home.reports.map((r: any) => (
                <article key={r.task_id}>
                  <p>{personalStatus(r.status)}</p>
                  <p>{r.content}</p>
                  <button onClick={() => openTask(r.task_id)}>
                    查看成果任务
                  </button>
                </article>
              ))
            ) : (
              <p>委派工作的成果会汇总到这里。</p>
            )}
          </section>
          <PersonalTools api={api} />
        </>
      )}
    </section>
  );
}
