import React, { useState as S, useEffect as E, useRef as Ref } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
type Row = Record<string, any>;
async function api(path: string, method = "GET", body?: unknown) {
  const r = await fetch("/api" + path, {
    method,
    credentials: "include",
    headers:
      body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw Error(e.detail);
  }
  return r.status === 204 ? null : r.json();
}
const stages = [
  "需求澄清",
  "方案设计",
  "任务拆解",
  "开发实现",
  "测试修复",
  "验收评审",
  "成果交付",
];
const status: Row = {
  idle: "待开始",
  delivered: "已交付",
  review: "待评审",
  stopping: "正在停止",
  waiting_permission: "等待授权",
  waiting_confirmation: "等待阶段确认",
  queued: "排队中",
  running: "执行中",
  completed: "已完成",
  failed: "无法完成",
  interrupted: "已停止",
  waiting_gate: "等待阶段确认",
  waiting_approval: "等待授权",
  paused: "已暂停，检查工作区后补充消息继续",
};
function App() {
  const [user, U] = S<Row | null>(null),
    [ready, R] = S(false),
    [error, Err] = S(""),
    [projects, P] = S<Row[]>([]),
    [trash, TR] = S<Row[]>([]),
    [project, PP] = S<Row | null>(null),
    [tasks, T] = S<Row[]>([]),
    [task, TT] = S<Row | null>(null),
    [engines, EN] = S<Row[]>([]),
    [tab, Tab] = S("文件"),
    [items, I] = S<Row[]>([]),
    [events, EV] = S<Row[]>([]),
    [text, TX] = S(""),
    [preview, PV] = S(false),
    [file, F] = S(""),
    [content, C] = S(""),
    [modal, M] = S(""),
    [busy, B] = S(false),
    [notice, N] = S("");
  const selection = Ref({ project: "", task: "", tab: "" });
  selection.current = { project: project?.id || "", task: task?.id || "", tab };
  async function act(fn: () => Promise<void>) {
    B(true);
    Err("");
    try {
      await fn();
    } catch (e) {
      Err(String((e as Error).message));
    } finally {
      B(false);
    }
  }
  async function loadProjects() {
    P(await api("/projects"));
  }
  async function loadTasks() {
    if (!project) return;
    const list = await api(`/projects/${project.id}/tasks`);
    if (selection.current.project === project.id) T(list);
  }
  async function loadItems() {
    if (!project || tab === "活动") return;
    const selected = { ...selection.current };
    const list = await api(
      tab === "文件" && task?.member_id
        ? `/tasks/${task.id}/files`
        : `/projects/${project.id}/${({ 文件: "files", 记忆: "memories", 扩展: "extensions", 协作: "members" } as Row)[tab]}`,
    );
    if (JSON.stringify(selection.current) === JSON.stringify(selected)) I(list);
  }
  async function refresh() {
    if (task) {
      const result = await api(`/tasks/${task.id}`);
      if (selection.current.task === task.id) TT(result);
    }
    await loadTasks();
  }
  E(() => {
    api("/auth/me")
      .then(U)
      .catch(() => {})
      .finally(() => R(true));
  }, []);
  E(() => {
    if (user)
      act(async () => {
        await loadProjects();
        EN(await api("/engines"));
      });
  }, [user]);
  E(() => {
    TT(null);
    EV([]);
    I([]);
    F("");
    PV(false);
    T([]);
    if (project)
      act(async () => {
        await loadTasks();
        await loadItems();
      });
  }, [project?.id]);
  E(() => {
    I([]);
    F("");
    PV(false);
    if (project) act(loadItems);
  }, [tab, task?.id]);
  E(() => {
    EV([]);
    if (!task) return;
    let active = true,
      polling = false;
    let cursor = 0;
    let lastTerminal = "";
    const tick = async () => {
      if (polling) return;
      polling = true;
      try {
        const [t, fresh] = await Promise.all([
          api(`/tasks/${task.id}`),
          api(`/tasks/${task.id}/events?after=${cursor}`),
        ]);
        if (!active) return;
        TT(t);
        if (fresh.length) {
          cursor = fresh[fresh.length - 1].id;
          EV((old) => {
            const ids = new Set(old.map((e) => e.id));
            return [...old, ...fresh.filter((e: Row) => !ids.has(e.id))].slice(
              -2000,
            );
          });
        }
        const list = await api(`/projects/${t.project_id}/tasks`);
        if (active) T(list);
        const terminal =
          t.runs
            ?.filter(
              (r: Row) => !["queued", "running", "stopping"].includes(r.status),
            )
            .map((r: Row) => r.id + ":" + r.status)
            .join(",") || "";
        if (terminal !== lastTerminal) {
          lastTerminal = terminal;
          if (selection.current.tab === "文件") {
            const files = await api(
              t.member_id
                ? `/tasks/${t.id}/files`
                : `/projects/${t.project_id}/files`,
            );
            if (active && selection.current.tab === "文件") I(files);
          }
        }
      } catch (e) {
        if (active) Err("连接中断：" + (e as Error).message);
      } finally {
        polling = false;
      }
    };
    void tick();
    const timer = setInterval(tick, 2500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [task?.id]);
  function form(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.currentTarget));
    act(async () => {
      if (modal === "项目") {
        const p = await api("/projects", "POST", d);
        await loadProjects();
        PP(p);
      } else if (modal === "任务") {
        const t = await api(`/projects/${project!.id}/tasks`, "POST", {
          ...d,
          require_acceptance: d.require_acceptance === "on",
        });
        await loadTasks();
        TT(t);
      } else if (modal === "任务设置") {
        await api(`/tasks/${task!.id}`, "PATCH", {
          ...d,
          require_acceptance: d.require_acceptance === "on",
        });
        await refresh();
      } else if (modal === "个人指令") {
        await api("/auth/me", "PATCH", d);
        U(await api("/auth/me"));
      } else if (modal === "记忆")
        await api(`/projects/${project!.id}/memories`, "POST", d);
      else if (modal === "扩展")
        await api(`/projects/${project!.id}/extensions`, "POST", {
          ...d,
          enabled: true,
        });
      else if (modal === "协作")
        await api(`/projects/${project!.id}/members`, "POST", d);
      else if (modal === "设置") {
        const p = await api(`/projects/${project!.id}`, "PATCH", {
          ...d,
          memory_enabled: d.memory_enabled === "on",
        });
        PP(p);
        N("已保存；后续执行使用新配置。");
      } else {
        U(await api("/auth/" + modal, "POST", d));
      }
      M("");
      if (project) await loadItems();
    });
  }
  if (!ready) return <div className="splash">正在连接工作空间…</div>;
  return (
    <>
      <aside>
        <div className="brand">
          ◈ <span>云序</span>
          <small>WORKSPACE</small>
        </div>
        <button className="new" onClick={() => M(user ? "项目" : "login")}>
          ＋ 新建项目
        </button>
        <div className="label">我的项目</div>
        {projects.map((p) => (
          <button
            className={project?.id === p.id ? "selected" : ""}
            key={p.id}
            onClick={() => PP(p)}
          >
            ▧ {p.name}
          </button>
        ))}
        <div className="asidebottom">
          {user && (
            <>
              <button onClick={() => M("个人指令")}>个人长期指令</button>
              <button
                onClick={() =>
                  act(async () => {
                    TR(await api("/trash"));
                    M("回收区");
                  })
                }
              >
                回收区
              </button>
            </>
          )}
          <span className="dot" />
          持久云端工作空间
          <br />
          <small>项目资料与任务历史持续保留</small>
          {user && (
            <button
              onClick={() =>
                act(async () => {
                  await api("/auth/logout", "POST");
                  U(null);
                  PP(null);
                  P([]);
                  TT(null);
                })
              }
            >
              {user.email} · 退出
            </button>
          )}
        </div>
      </aside>
      <main>
        <header>
          <div>
            <small>工作空间 / {project?.name || "开始工作"}</small>
            <h2>{task?.title || project?.name || "让想法，成为成果。"}</h2>
          </div>
          <div className="headerActions">
            {project && (
              <>
                <button onClick={() => M("设置")}>项目设置</button>
                {task && (
                  <button onClick={() => M("任务设置")}>任务设置</button>
                )}
                <button
                  onClick={() => {
                    if (confirm("将项目移入回收区？执行会停止，文件仍保留。"))
                      act(async () => {
                        await api(`/projects/${project.id}`, "DELETE");
                        PP(null);
                        TT(null);
                        await loadProjects();
                      });
                  }}
                >
                  移入回收区
                </button>
                <button className="primary" onClick={() => M("任务")}>
                  ＋ 新任务
                </button>
              </>
            )}
          </div>
        </header>
        {error && (
          <div role="alert" className="error">
            {error}
            <button onClick={() => Err("")}>关闭</button>
          </div>
        )}
        {notice && (
          <div className="notice" onClick={() => N("")}>
            {notice}
          </div>
        )}
        {!user ? (
          <section className="welcome">
            <div className="eyebrow">YOUR IDEAS, IN MOTION</div>
            <h1>
              专注目标。
              <br />
              把执行交给 Agent。
            </h1>
            <p>从研究、代码到交付，在一个持续保留上下文的空间里完成工作。</p>
            <button className="primary" onClick={() => M("login")}>
              登录工作台 →
            </button>
            <button onClick={() => M("register")}>创建账户</button>
          </section>
        ) : !project ? (
          <section className="welcome">
            <div className="eyebrow">A SPACE FOR YOUR NEXT IDEA</div>
            <h1>今天，想完成什么？</h1>
            <p>创建一个项目，组织资料、任务与长期协作。</p>
            <button className="primary" onClick={() => M("项目")}>
              ＋ 创建第一个项目
            </button>
            <div className="enginegrid">
              {engines.map((e) => (
                <article key={e.engine}>
                  <b>{e.engine}</b>
                  <p>{e.available ? "● 环境可用，认证待验证" : "○ 未就绪"}</p>
                  <small>{e.reason}</small>
                </article>
              ))}
            </div>
          </section>
        ) : (
          <div className="workbench">
            <nav className="tasknav">
              <div className="label">
                任务 <span>{tasks.length}</span>
              </div>
              {tasks.map((t) => (
                <button
                  key={t.id}
                  className={task?.id === t.id ? "selected" : ""}
                  onClick={() => {
                    TT(t);
                    EV([]);
                  }}
                >
                  <b>{t.title}</b>
                  <small>
                    {t.mode === "development" ? "需求开发" : "自由模式"} ·{" "}
                    {status[t.status] || t.status}
                  </small>
                </button>
              ))}
              {!tasks.length && (
                <p className="muted">还没有任务。创建任务开始工作。</p>
              )}
            </nav>
            <section className="conversation">
              {task ? (
                <>
                  <div className="taskmeta">
                    <span className="badge">
                      {task.permission_mode === "ask"
                        ? "Ask · 原生请求人工确认"
                        : "Auto · 原生风险审核"}
                    </span>
                    <span className="badge">
                      {task.mode === "development" ? "需求开发" : "自由模式"}
                    </span>
                    <span>
                      {task.engine} {task.model}
                    </span>
                    <span className="badge">
                      {status[task.status] || task.status}
                    </span>
                  </div>
                  {task.mode === "development" && (
                    <div className="stages">
                      {stages.map((s, i) => (
                        <span
                          className={i === task.stage ? "current" : ""}
                          key={s}
                        >
                          {i + 1} {s}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="messages">
                    {task.pending_inputs
                      ?.filter((q: Row) =>
                        ["queued", "paused"].includes(q.status),
                      )
                      .map((q: Row) => (
                        <article className="gate" key={q.id}>
                          <b>
                            {q.status === "paused"
                              ? "补充已暂停"
                              : "待执行补充"}
                          </b>
                          <p>{q.content}</p>
                          <button
                            onClick={() => {
                              const content = prompt(
                                "修改尚未执行的消息",
                                q.content,
                              );
                              if (content)
                                act(async () => {
                                  await api(
                                    `/tasks/${task.id}/inputs/${q.id}`,
                                    "PATCH",
                                    { content },
                                  );
                                  await refresh();
                                });
                            }}
                          >
                            编辑补充
                          </button>
                          <button
                            onClick={() =>
                              act(async () => {
                                await api(
                                  `/tasks/${task.id}/inputs/${q.id}`,
                                  "DELETE",
                                );
                                await refresh();
                              })
                            }
                          >
                            撤回补充
                          </button>
                          {q.status === "paused" && (
                            <button
                              onClick={() =>
                                act(async () => {
                                  await api(
                                    `/tasks/${task.id}/resume-queue`,
                                    "POST",
                                  );
                                  await refresh();
                                })
                              }
                            >
                              恢复待执行消息
                            </button>
                          )}
                        </article>
                      ))}
                    {task.artifacts?.map((a: Row) => (
                      <article className="gate" key={a.id}>
                        <b>已保存成果</b>
                        <p>{a.path || a.name}</p>
                        <a href={`/api/artifacts/${a.id}`} download>
                          下载成果 ↓
                        </a>
                        <button
                          onClick={() => {
                            if (
                              confirm(
                                `用此历史文件覆盖 ${a.path}？仅恢复该文件。`,
                              )
                            )
                              act(async () => {
                                await api(
                                  `/artifacts/${a.id}/restore`,
                                  "POST",
                                  { path: a.path },
                                );
                                await refresh();
                                await loadItems();
                              });
                          }}
                        >
                          恢复此文件
                        </button>
                      </article>
                    ))}
                    {events.some((e) => e.kind === "auto_review") && (
                      <details>
                        <summary>工具自动审核记录</summary>
                        {Array.from(
                          new Map(
                            events
                              .filter((e) => e.kind === "auto_review")
                              .map((e) => [e.data.review_id, e]),
                          ).values(),
                        ).map((e) => (
                          <article className="event" key={e.id}>
                            <b>
                              {(
                                {
                                  inProgress: "自动审核中",
                                  approved: "自动审核通过",
                                  denied: "自动审核拒绝",
                                  timedOut: "审核超时，未授权",
                                  aborted: "审核已取消",
                                } as Row
                              )[e.data.review?.status] || "自动审核"}
                            </b>
                            <small>{e.data.review?.riskLevel}</small>
                            <p>{e.data.review?.rationale}</p>
                            {["denied", "timedOut"].includes(
                              e.data.review?.status,
                            ) && (
                              <small>
                                操作未获授权。需要人工处理时，可在任务停止后切换
                                Ask 模式并明确补充要求。
                              </small>
                            )}
                          </article>
                        ))}
                      </details>
                    )}
                    {task.checks?.length > 0 && (
                      <details>
                        <summary>交付检查记录（{task.checks.length}）</summary>
                        {task.checks.map((c: Row) => (
                          <article className="event" key={c.id}>
                            <b>
                              {c.name} · {c.status}
                            </b>
                            <small>
                              {c.source === "platform"
                                ? "平台文件核验"
                                : "执行器报告，未独立复跑"}
                            </small>
                            <pre>{c.evidence}</pre>
                          </article>
                        ))}
                      </details>
                    )}
                    {!task.messages?.length && (
                      <div className="empty">
                        <h2>从一个明确的目标开始</h2>
                        <p>描述你希望完成的工作、预期成果和约束。</p>
                      </div>
                    )}
                    {task.messages?.map((m: Row) => (
                      <article className={"message " + m.role} key={m.id}>
                        <small>{m.role === "user" ? "你" : "◈ Agent"}</small>
                        <div>{m.content}</div>
                      </article>
                    ))}
                    {task.runs
                      ?.filter((r: Row) =>
                        ["running", "stopping"].includes(r.status),
                      )
                      .map((r: Row) => {
                        const stream = events
                          .filter(
                            (e) =>
                              e.kind === "delta" &&
                              (e.run_id || e.data.run_id) === r.id,
                          )
                          .map((e) => e.data.text || "")
                          .join("");
                        return stream ? (
                          <article className="message" key={"live-" + r.id}>
                            <small>◈ Agent · 正在输出</small>
                            <div>{stream}</div>
                          </article>
                        ) : null;
                      })}
                    {task.runs
                      ?.filter((r: Row) => r.error)
                      .map((r: Row) => (
                        <div className="error" key={r.id}>
                          {r.error}
                        </div>
                      ))}
                    {task.gates
                      ?.filter((g: Row) => g.status === "pending")
                      .map((g: Row) => (
                        <article className="gate" key={g.id}>
                          <b>{stages[g.stage]} · 等待确认</b>
                          <p>请查看当前版本 {g.version} 的阶段成果后决定。</p>
                          <button
                            onClick={() =>
                              act(async () => {
                                await api(
                                  `/tasks/${task.id}/gates/${g.id}`,
                                  "POST",
                                  { decision: "approve" },
                                );
                                await refresh();
                              })
                            }
                          >
                            通过阶段
                          </button>
                          <button
                            onClick={() => {
                              const comment = prompt("需要修改什么？");
                              if (comment)
                                act(async () => {
                                  await api(
                                    `/tasks/${task.id}/gates/${g.id}`,
                                    "POST",
                                    { decision: "reject", comment },
                                  );
                                  await refresh();
                                });
                            }}
                          >
                            退回修改
                          </button>
                        </article>
                      ))}
                    {task.approvals
                      ?.filter((a: Row) => a.status === "pending")
                      .map((e: Row) => (
                        <article className="gate" key={e.id}>
                          <b>工具授权 · {e.details.tool}</b>
                          <pre>
                            {JSON.stringify(e.details.details, null, 2)}
                          </pre>
                          {["accept", "decline"].map((d) => (
                            <button
                              key={d}
                              onClick={() =>
                                act(async () => {
                                  await api(
                                    `/tasks/${task.id}/approvals/${e.id}`,
                                    "POST",
                                    { decision: d },
                                  );
                                  await refresh();
                                })
                              }
                            >
                              {d === "accept" ? "批准本次" : "拒绝"}
                            </button>
                          ))}
                        </article>
                      ))}
                  </div>
                  <form
                    className="composer"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (!text.trim()) return;
                      act(async () => {
                        await api(`/tasks/${task.id}/messages`, "POST", {
                          content: text,
                        });
                        TX("");
                        await refresh();
                      });
                    }}
                  >
                    <textarea
                      aria-label="任务消息"
                      value={text}
                      onChange={(e) => TX(e.target.value)}
                      placeholder="描述目标，或补充新的要求…"
                    />
                    <div>
                      <small>消息保存在当前任务 · 执行中补充将排队</small>
                      <button
                        type="button"
                        onClick={() =>
                          act(async () => {
                            await api(`/tasks/${task.id}/stop`, "POST");
                            await refresh();
                          })
                        }
                      >
                        停止
                      </button>
                      <button
                        className="primary"
                        disabled={busy || !text.trim()}
                      >
                        发送 ↑
                      </button>
                    </div>
                  </form>
                </>
              ) : (
                <div className="empty">
                  <div className="orb">◈</div>
                  <h2>一个项目，无限可能</h2>
                  <p>任务拥有独立对话，共享这个项目的资料。</p>
                  <button className="primary" onClick={() => M("任务")}>
                    创建任务 →
                  </button>
                </div>
              )}
            </section>
            <section className="inspector">
              <div className="tabs">
                {["文件", "活动", "记忆", "扩展", "协作"].map((t) => (
                  <button
                    className={tab === t ? "active" : ""}
                    onClick={() => Tab(t)}
                    key={t}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div className="panelbody">
                {tab === "活动" ? (
                  <>
                    {events.map((e) => (
                      <article className="event" key={e.id}>
                        <small>
                          {e.created_at} · {e.kind}
                        </small>
                        <pre>
                          {typeof e.data === "object"
                            ? JSON.stringify(e.data, null, 2)
                            : e.data}
                        </pre>
                      </article>
                    ))}
                    {!events.length && (
                      <p className="muted">
                        执行开始后，真实活动会显示在这里。
                      </p>
                    )}
                  </>
                ) : (
                  <>
                    <div className="paneltitle">
                      <b>{tab === "文件" ? "项目文件" : tab}</b>
                      {tab === "文件" ? (
                        <>
                          <button
                            onClick={() => {
                              const url = prompt("公开 GitHub 仓库 HTTPS 地址");
                              if (url)
                                act(async () => {
                                  await api(
                                    `/projects/${project.id}/import-repository`,
                                    "POST",
                                    { url },
                                  );
                                  await loadItems();
                                  N("仓库已导入");
                                });
                            }}
                          >
                            导入仓库
                          </button>
                          <label className="upload">
                            上传
                            <input
                              type="file"
                              onChange={(e) => {
                                const f = e.target.files?.[0];
                                if (f)
                                  act(async () => {
                                    const d = new FormData();
                                    d.append("file", f);
                                    await api(
                                      `/projects/${project.id}/upload`,
                                      "POST",
                                      d,
                                    );
                                    await loadItems();
                                  });
                              }}
                            />
                          </label>
                        </>
                      ) : (
                        <button onClick={() => M(tab)}>＋ 新增</button>
                      )}
                    </div>
                    {items.map((v, i) => (
                      <article className="item" key={v.id || v.path || i}>
                        {tab === "文件" ? (
                          <button
                            onClick={() =>
                              act(async () => {
                                const r = await fetch(
                                  task?.member_id
                                    ? `/api/tasks/${task.id}/file?path=${encodeURIComponent(v.path)}`
                                    : `/api/projects/${project.id}/file?path=${encodeURIComponent(v.path)}`,
                                );
                                if (!r.ok) throw Error("文件读取失败");
                                C(await r.text());
                                F(v.path);
                              })
                            }
                          >
                            ▤ {v.path} <small>{v.size} B</small>
                          </button>
                        ) : (
                          <>
                            <b>{v.name || v.category}</b>
                            <p>{v.content || v.role}</p>
                            {tab === "扩展" && (
                              <>
                                <small>
                                  {v.kind} ·{" "}
                                  {v.enabled ? "项目已启用" : "已停用"}
                                </small>
                                <button
                                  onClick={() =>
                                    act(async () => {
                                      await api(
                                        `/extensions/${v.id}`,
                                        "PATCH",
                                        { enabled: !v.enabled },
                                      );
                                      await loadItems();
                                    })
                                  }
                                >
                                  切换启用
                                </button>
                              </>
                            )}
                            {tab === "协作" ? (
                              <button
                                onClick={() => {
                                  const content = prompt(
                                    "指派给 " + v.name + " 的目标",
                                  );
                                  if (content)
                                    act(async () => {
                                      const t = await api(
                                        `/members/${v.id}/assign`,
                                        "POST",
                                        { content },
                                      );
                                      await loadTasks();
                                      TT(t);
                                      N("已指派到成员的独立工作空间");
                                    });
                                }}
                              >
                                指派任务
                              </button>
                            ) : (
                              <>
                                <button
                                  onClick={() => {
                                    const content = prompt(
                                      "修改内容",
                                      v.content,
                                    );
                                    if (content !== null)
                                      act(async () => {
                                        await api(
                                          `/${tab === "记忆" ? "memories" : "extensions"}/${v.id}`,
                                          "PATCH",
                                          {
                                            content,
                                            ...(tab === "记忆"
                                              ? { category: v.category }
                                              : {}),
                                          },
                                        );
                                        await loadItems();
                                      });
                                  }}
                                >
                                  编辑
                                </button>
                                <button
                                  onClick={() =>
                                    act(async () => {
                                      await api(
                                        `/${tab === "记忆" ? "memories" : "extensions"}/${v.id}`,
                                        "DELETE",
                                      );
                                      await loadItems();
                                    })
                                  }
                                >
                                  删除
                                </button>
                              </>
                            )}
                          </>
                        )}
                      </article>
                    ))}
                    {!items.length && (
                      <div className="emptypanel">
                        {tab === "文件"
                          ? "上传资料，或等待 Agent 生成成果。"
                          : `还没有${tab}，点击新增开始。`}
                      </div>
                    )}
                    {file && (
                      <div className="editor">
                        <b>{file}</b>
                        {!task?.member_id && (
                          <button onClick={() => PV(!preview)}>
                            {preview ? "关闭预览" : "预览"}
                          </button>
                        )}
                        {preview && !task?.member_id && (
                          <iframe
                            title="文件预览"
                            sandbox=""
                            src={`/api/projects/${project.id}/preview?path=${encodeURIComponent(file)}`}
                            style={{
                              width: "100%",
                              height: 350,
                              border: "1px solid #ddd",
                            }}
                          />
                        )}
                        <textarea
                          aria-label="文件内容"
                          value={content}
                          onChange={(e) => C(e.target.value)}
                        />
                        <button
                          disabled={!!task?.member_id}
                          onClick={() =>
                            act(async () => {
                              await api(
                                `/projects/${project.id}/file?path=${encodeURIComponent(file)}`,
                                "PUT",
                                { content },
                              );
                              N("文件已保存");
                            })
                          }
                        >
                          保存文件
                        </button>
                        <a
                          href={
                            task?.member_id
                              ? `/api/tasks/${task.id}/file?path=${encodeURIComponent(file)}`
                              : `/api/projects/${project.id}/file?path=${encodeURIComponent(file)}`
                          }
                          download
                        >
                          下载
                        </a>
                        {task?.member_id && (
                          <button
                            onClick={() =>
                              act(async () => {
                                await api(
                                  `/projects/${project.id}/integrate/${task.id}`,
                                  "POST",
                                  { path: file },
                                );
                                N("文件已检查基线并应用到项目");
                              })
                            }
                          >
                            应用到项目
                          </button>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            </section>
          </div>
        )}
      </main>
      {modal && (
        <div className="overlay">
          <form className="dialog" onSubmit={form}>
            <button type="button" className="close" onClick={() => M("")}>
              ×
            </button>
            <h2>
              {({ login: "欢迎回来", register: "创建账户" } as Row)[modal] ||
                "新建" + modal}
            </h2>
            {["login", "register"].includes(modal) ? (
              <>
                <label>
                  邮箱
                  <input
                    required
                    type="email"
                    name="email"
                    autoComplete="email"
                  />
                </label>
                <label>
                  密码
                  <input
                    required
                    minLength={10}
                    type="password"
                    name="password"
                    autoComplete={
                      modal === "login" ? "current-password" : "new-password"
                    }
                  />
                </label>
                <button
                  type="button"
                  onClick={() => M(modal === "login" ? "register" : "login")}
                >
                  {modal === "login" ? "还没有账户？注册" : "已有账户？登录"}
                </button>
              </>
            ) : modal === "回收区" ? (
              <>
                {trash.map((p) => (
                  <article className="item" key={p.id}>
                    <b>{p.name}</b>
                    <button
                      type="button"
                      onClick={() =>
                        act(async () => {
                          await api(`/projects/${p.id}/restore`, "POST");
                          TR(await api("/trash"));
                          await loadProjects();
                        })
                      }
                    >
                      恢复项目
                    </button>
                  </article>
                ))}
                {!trash.length && <p>回收区为空</p>}
              </>
            ) : modal === "个人指令" ? (
              <label>
                账户长期指令
                <textarea
                  name="content"
                  maxLength={16000}
                  defaultValue={user?.instructions}
                />
              </label>
            ) : modal === "任务设置" ? (
              <>
                <label>
                  工具授权策略
                  <select
                    name="permission_mode"
                    defaultValue={task?.permission_mode || "auto"}
                  >
                    <option value="auto">Auto · 常规操作自动推进</option>
                    <option value="ask">Ask · 需要授权时询问我</option>
                  </select>
                </label>
                <small>
                  工具授权与阶段验收独立。Auto
                  采用原生风险审核，拒绝或不可用时不会自动放开全部权限。
                </small>
                <p>执行中请先停止。修改后建立新会话，保留历史并重新确认。</p>
                <label>
                  工作模式
                  <select name="mode" defaultValue={task?.mode}>
                    <option value="free">自由模式</option>
                    <option value="development">需求开发</option>
                  </select>
                </label>
                <label>
                  执行器
                  <select name="engine" defaultValue={task?.engine}>
                    <option>codex</option>
                    <option>claude</option>
                  </select>
                </label>
                <label>
                  模型
                  <input name="model" defaultValue={task?.model || ""} />
                </label>
                <label>
                  <input
                    type="checkbox"
                    name="require_acceptance"
                    defaultChecked={!!task?.require_acceptance}
                  />
                  自由模式需要人工验收
                </label>
              </>
            ) : modal === "设置" ? (
              <>
                <label>
                  项目约定
                  <textarea
                    name="instructions"
                    maxLength={8000}
                    defaultValue={project?.instructions}
                  />
                </label>
                <label>
                  <input
                    name="memory_enabled"
                    type="checkbox"
                    defaultChecked={project?.memory_enabled}
                  />{" "}
                  启用自主项目记忆
                </label>
                <small>约定会随后续执行发送给模型。</small>
              </>
            ) : (
              <>
                <label>
                  {modal === "任务"
                    ? "任务标题"
                    : modal === "记忆"
                      ? "分类"
                      : "名称"}
                  <input
                    required
                    name={
                      modal === "任务"
                        ? "title"
                        : modal === "记忆"
                          ? "category"
                          : "name"
                    }
                  />
                </label>
                {modal === "任务" && (
                  <>
                    <label>
                      工具授权策略
                      <select name="permission_mode" defaultValue="auto">
                        <option value="auto">Auto · 常规操作自动推进</option>
                        <option value="ask">Ask · 需要授权时询问我</option>
                      </select>
                    </label>
                    <label>
                      工作模式
                      <select name="mode">
                        <option value="free">
                          自由模式 · 明确目标直接执行
                        </option>
                        <option value="development">
                          需求开发 · 七阶段与人工确认
                        </option>
                      </select>
                    </label>
                    <label>
                      <input name="require_acceptance" type="checkbox" />
                      自由模式额外要求人工验收
                    </label>
                    <label>
                      执行器
                      <select name="engine">
                        {["codex", "claude"].map((x) => (
                          <option key={x} value={x}>
                            {x} ·{" "}
                            {engines.find((e) => e.engine === x)?.available
                              ? "环境可用，认证待验证"
                              : "未就绪"}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      模型（可选）
                      <input name="model" placeholder="使用执行器默认模型" />
                    </label>
                    {engines.map((e) => (
                      <small key={e.engine}>
                        {e.engine}: {e.reason || "已就绪"}
                        <br />
                      </small>
                    ))}
                  </>
                )}
                {["记忆", "扩展"].includes(modal) && (
                  <label>
                    内容
                    <textarea
                      required
                      name="content"
                      placeholder={
                        modal === "扩展"
                          ? "Skill 方法文本，或 MCP JSON 配置"
                          : "可供后续任务复用的信息"
                      }
                    />
                  </label>
                )}
                {modal === "扩展" && (
                  <label>
                    类型
                    <select name="kind">
                      <option value="skill">Skill 方法</option>
                      <option value="mcp">MCP 连接</option>
                    </select>
                  </label>
                )}
                {modal === "协作" && (
                  <>
                    <label>
                      职责
                      <input required name="role" />
                    </label>
                    <label>
                      执行器
                      <select name="engine">
                        <option>codex</option>
                        <option>claude</option>
                      </select>
                    </label>
                    <small>成员保留独立会话，在隔离工作空间执行。</small>
                  </>
                )}
              </>
            )}
            <button className="primary" disabled={busy || modal === "回收区"}>
              {busy ? "正在保存…" : modal === "login" ? "登录" : "保存"}
            </button>
          </form>
        </div>
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
