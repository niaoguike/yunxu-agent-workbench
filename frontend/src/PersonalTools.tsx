import { personalStatus } from "./personalStatus";
import React, { useEffect, useState, useRef } from "react";
export function PersonalTools({ api }: { api: any }) {
  const [connections, C] = useState<any[]>([]),
    [actions, A] = useState<any[]>([]),
    [computerActions, CA] = useState<any[]>([]),
    [image, I] = useState(""),
    [error, E] = useState(""),
    [status, S] = useState("not_created");
  const [goals, G] = useState<any[]>([]);
  const img = useRef<HTMLImageElement>(null);
  const load = async () => {
    C(await api("/personal/connections"));
    G(await api("/goals"));
    A(await api("/personal/actions"));
    CA(await api("/personal/computer/actions"));
    S((await api("/personal/computer")).status);
  };
  const act = async (f: () => Promise<any>) => {
    try {
      await f();
      await load();
      E("");
    } catch (e: any) {
      E(e.message);
    }
  };
  useEffect(() => {
    load().catch((e) => E(e.message));
    const t = setInterval(() => load().catch((e) => E(e.message)), 5000);
    return () => clearInterval(t);
  }, []);
  const control = async (payload: any) =>
    I((await api("/personal/computer/control", "POST", payload)).image);
  return (
    <section>
      <h2>应用、决策与电脑</h2>
      {error && <p role="alert">{error}</p>}
      <div>
        {["gmail", "calendar"].map((p) => (
          <button
            key={p}
            onClick={() =>
              act(async () => {
                const r = await api(
                  `/personal/connections/${p}/authorize`,
                  "POST",
                );
                window.location.assign(r.url);
              })
            }
          >
            连接 {p}
          </button>
        ))}
      </div>
      {connections.map((c) => (
        <article key={c.id}>
          <strong>
            {c.provider} · {personalStatus(c.status)}
          </strong>
          <button
            onClick={() =>
              act(() => api(`/personal/connections/${c.id}/sync`, "POST"))
            }
          >
            同步 / 重试
          </button>
          <button
            onClick={() =>
              act(() => api(`/personal/connections/${c.id}`, "DELETE"))
            }
          >
            撤销连接
          </button>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const goal = new FormData(e.currentTarget).get("goal");
              act(() =>
                api(`/personal/connections/${c.id}/goals/${goal}`, "POST"),
              );
            }}
          >
            <label>
              将变化交给哪个目标处理
              <select name="goal" required>
                <option value="">选择目标或责任</option>
                {goals.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.config.title}
                  </option>
                ))}
              </select>
            </label>
            <button>订阅变化</button>
          </form>
        </article>
      ))}
      <h3>需要你决定</h3>
      {actions
        .filter((a) => a.status === "pending" || a.status === "unknown")
        .map((a) => (
          <article key={a.id}>
            <p>
              {a.provider} · {personalStatus(a.status)}
            </p>
            <pre style={{ whiteSpace: "pre-wrap" }}>
              {JSON.stringify(JSON.parse(a.payload), null, 2)}
            </pre>
            {a.status === "pending" && (
              <>
                <button
                  onClick={() =>
                    act(() => api(`/personal/actions/${a.id}/approve`, "POST"))
                  }
                >
                  批准并执行
                </button>
                <button
                  onClick={() =>
                    act(() => api(`/personal/actions/${a.id}/reject`, "POST"))
                  }
                >
                  拒绝
                </button>
              </>
            )}
          </article>
        ))}
      {computerActions
        .filter((a) => a.status === "pending" || a.status === "unknown")
        .map((a) => (
          <article key={a.id}>
            <p>
              {a.reason} · {personalStatus(a.status)}
            </p>
            <pre>{a.payload}</pre>
            {a.status === "pending" && (
              <>
                <button
                  onClick={() =>
                    act(async () =>
                      I(
                        (
                          await api(
                            `/personal/computer/actions/${a.id}/approve`,
                            "POST",
                          )
                        ).image,
                      ),
                    )
                  }
                >
                  批准电脑操作
                </button>
                <button
                  onClick={() =>
                    act(() =>
                      api(`/personal/computer/actions/${a.id}/reject`, "POST"),
                    )
                  }
                >
                  拒绝
                </button>
              </>
            )}
          </article>
        ))}
      <details>
        <summary>个人云电脑 · {personalStatus(status)}</summary>
        <p>登录和敏感输入请通过接管控件完成。暂停将保留浏览器资料。</p>
        <button
          onClick={() => act(() => api("/personal/computer/start", "POST"))}
        >
          启动 / 恢复
        </button>
        <button
          onClick={() => act(() => api("/personal/computer/stop", "POST"))}
        >
          暂停电脑
        </button>
        <button onClick={() => act(() => control({ kind: "screenshot" }))}>
          刷新屏幕
        </button>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const url = new FormData(e.currentTarget).get("url");
            act(() => control({ kind: "navigate", url }));
          }}
        >
          <label>
            打开网址
            <input name="url" type="url" placeholder="https://" required />
          </label>
          <button>打开</button>
        </form>
        {image && (
          <img
            ref={img}
            src={image}
            alt="个人云浏览器，可点击接管"
            style={{ width: "100%", maxWidth: 1280, cursor: "crosshair" }}
            onClick={(e) => {
              const b = e.currentTarget.getBoundingClientRect();
              act(() =>
                control({
                  kind: "click",
                  x: ((e.clientX - b.left) * 1280) / b.width,
                  y: ((e.clientY - b.top) * 800) / b.height,
                }),
              );
            }}
          />
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const f = e.currentTarget;
            const text = new FormData(f).get("text");
            act(async () => {
              await control({ kind: "type", text });
              f.reset();
            });
          }}
        >
          <label>
            接管输入
            <input type="password" name="text" autoComplete="off" required />
          </label>
          <button>输入到电脑</button>
        </form>
        {["Enter", "Tab", "Backspace", "Escape", "ArrowDown"].map((key) => (
          <button
            key={key}
            onClick={() => act(() => control({ kind: "key", key }))}
          >
            {key}
          </button>
        ))}
      </details>
    </section>
  );
}
