import React, { useEffect, useState } from "react";
type Row = Record<string, any>;
type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Props = {
  projectId: string;
  taskId?: string;
  api: Api;
  openTask: (id: string) => Promise<void>;
  mode: "routines" | "connections";
};
const states: Row = {
  dispatched: "已派发",
  queued: "排队中",
  dispatching: "派发中",
  failed: "失败",
  skipped: "已跳过",
  missed: "错过计划",
  unknown: "结果不明，需检查",
  pending: "待发送",
  sending: "发送中",
  sent: "已发送",
  accepted: "已接收",
  disabled: "已停用",
  missing_credentials: "缺少密钥",
  connected: "已连接",
  connecting: "连接中",
  error: "连接失败",
  delivered: "已交付",
  running: "执行中",
  interrupted: "已中断",
  paused: "已暂停",
};
const emptyRoutine = {
  name: "",
  prompt: "",
  frequency: "daily",
  time: "09:00",
  timezone: "Asia/Shanghai",
  weekday: 0,
  engine: "codex",
  permission_mode: "ask",
  enabled: false,
  connection_id: "",
};
const emptyConnection = {
  name: "",
  provider: "telegram",
  chat_id: "",
  user_id: "",
  engine: "codex",
  permission_mode: "ask",
  receive: false,
  send: false,
  enabled: false,
};
const when = (value: number) =>
  new Date(value * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
export function AutomationPanel({
  projectId,
  taskId,
  api,
  openTask,
  mode,
}: Props) {
  const [routines, setRoutines] = useState<Row[]>([]),
    [connections, setConnections] = useState<Row[]>([]);
  const [draft, setDraft] = useState<Row>(
    mode === "routines" ? emptyRoutine : emptyConnection,
  );
  const [editing, setEditing] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  async function reload() {
    const [r, c] = await Promise.all([
      api(`/projects/${projectId}/routines`),
      api(`/projects/${projectId}/connections`),
    ]);
    setRoutines(r);
    setConnections(c);
  }
  useEffect(() => {
    let active = true;
    const tick = async () => {
      try {
        const [r, c, health] = await Promise.all([
          api(`/projects/${projectId}/routines`),
          api(`/projects/${projectId}/connections`),
          api("/health"),
        ]);
        if (active && health.automation_error)
          setError(health.automation_error);
        if (active) {
          setRoutines(r);
          setConnections(c);
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    };
    void tick();
    const timer = setInterval(tick, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [projectId]);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const patch = (key: string, value: any) =>
    setDraft({ ...draft, [key]: value });
  const options = (
    key: string,
    label: string,
    values: string[],
    labels?: string[],
  ) => (
    <label>
      {label}
      <select value={draft[key]} onChange={(e) => patch(key, e.target.value)}>
        {values.map((v, i) => (
          <option key={v} value={v}>
            {labels?.[i] || v}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <section
      className="automation-panel"
      aria-label={mode === "routines" ? "定时自动化" : "聊天渠道"}
    >
      <h3>{mode === "routines" ? "定时自动化" : "Slack / Telegram"}</h3>
      <p>
        {mode === "routines"
          ? "本地后端运行时按计划创建任务；关闭期间错过的计划留记录，不自动补跑。上次任务未结束时跳过。"
          : "只接收指定聊天中指定用户的文本消息。回复机器人消息可继续原任务；发送权限允许交接任务和回传结果。"}
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <form
        className="automation-form"
        onSubmit={(e) => {
          e.preventDefault();
          void act(async () => {
            const body =
              mode === "routines"
                ? { ...draft, connection_id: draft.connection_id || null }
                : draft;
            await api(
              editing
                ? `/${mode === "routines" ? "routines" : "connections"}/${editing}`
                : `/projects/${projectId}/${mode}`,
              editing ? "PUT" : "POST",
              body,
            );
            setEditing("");
            setDraft(mode === "routines" ? emptyRoutine : emptyConnection);
            setNotice("已保存");
          });
        }}
      >
        <label>
          名称
          <input
            required
            maxLength={120}
            value={draft.name}
            onChange={(e) => patch("name", e.target.value)}
          />
        </label>
        {mode === "routines" ? (
          <>
            <label>
              执行内容
              <textarea
                required
                maxLength={16000}
                value={draft.prompt}
                onChange={(e) => patch("prompt", e.target.value)}
                placeholder="例如：分析项目中的 sales.csv，更新报告并写明检查结果"
              />
            </label>
            {options(
              "frequency",
              "执行周期",
              ["daily", "weekly"],
              ["每天", "每周"],
            )}
            {draft.frequency === "weekly" && (
              <label>
                星期
                <select
                  aria-label="星期"
                  value={draft.weekday}
                  onChange={(e) => patch("weekday", Number(e.target.value))}
                >
                  {["一", "二", "三", "四", "五", "六", "日"].map((day, i) => (
                    <option key={i} value={i}>
                      星期{day}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              执行时间
              <input
                required
                type="time"
                value={draft.time}
                onChange={(e) => patch("time", e.target.value)}
              />
            </label>
            {options(
              "timezone",
              "计划时区",
              ["Asia/Shanghai", "UTC"],
              ["北京时间", "UTC"],
            )}
            <label>
              结果回传渠道
              <select
                value={draft.connection_id || ""}
                onChange={(e) => patch("connection_id", e.target.value)}
              >
                <option value="">仅在工作台查看</option>
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {!c.enabled || !c.send ? "（未启用发送）" : ""}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : (
          <>
            <label>
              平台
              <select
                disabled={!!editing}
                value={draft.provider}
                onChange={(e) => patch("provider", e.target.value)}
              >
                <option value="telegram">Telegram</option>
                <option value="slack">Slack</option>
              </select>
            </label>
            <label>
              聊天 ID
              <input
                disabled={!!editing}
                required
                value={draft.chat_id}
                onChange={(e) => patch("chat_id", e.target.value)}
              />
            </label>
            <label>
              允许的用户 ID
              <input
                disabled={!!editing}
                required
                value={draft.user_id}
                onChange={(e) => patch("user_id", e.target.value)}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={!!draft.receive}
                onChange={(e) => patch("receive", e.target.checked)}
              />
              允许此用户的消息触发任务
            </label>
            <label>
              <input
                type="checkbox"
                checked={!!draft.send}
                onChange={(e) => patch("send", e.target.checked)}
              />
              允许向此聊天发送任务和结果
            </label>
          </>
        )}
        {options("engine", "执行器", ["codex", "claude"])}
        {options(
          "permission_mode",
          "工具授权",
          ["ask", "auto"],
          ["Ask · 人工审批", "Auto · 原生审核"],
        )}
        <label>
          <input
            type="checkbox"
            checked={!!draft.enabled}
            onChange={(e) => patch("enabled", e.target.checked)}
          />
          启用{mode === "routines" ? "计划" : "连接"}
        </label>
        <button disabled={busy} type="submit">
          {editing
            ? "保存修改"
            : mode === "routines"
              ? "创建自动化"
              : "保存渠道连接"}
        </button>
        {editing && (
          <button
            type="button"
            onClick={() => {
              setEditing("");
              setDraft(mode === "routines" ? emptyRoutine : emptyConnection);
            }}
          >
            取消编辑
          </button>
        )}
      </form>
      {mode === "routines"
        ? routines.map((r) => (
            <article className="automation-card" key={r.id}>
              <b>
                {r.config.name} · {r.enabled ? "已启用" : "已暂停"}
              </b>
              <p>{r.config.prompt}</p>
              <small>下次计划：{when(r.next_due)}（北京时间显示）</small>
              <button
                disabled={busy}
                onClick={() => {
                  setEditing(r.id);
                  setDraft(r.config);
                }}
              >
                编辑
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await api(`/routines/${r.id}`, "PUT", {
                      ...r.config,
                      enabled: !r.enabled,
                    });
                  })
                }
              >
                {r.enabled ? "暂停" : "恢复"}
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const result = await api(`/routines/${r.id}/run`, "POST");
                    setNotice(
                      `立即执行：${states[result.status] || result.status}${result.error ? "；" + result.error : ""}`,
                    );
                  })
                }
              >
                立即执行一次
              </button>
              <details>
                <summary>执行记录（{r.runs.length}）</summary>
                {r.runs.map((run: Row) => (
                  <div className="automation-record" key={run.id}>
                    {when(run.created_at)} ·{" "}
                    {states[run.task_status] ||
                      states[run.status] ||
                      run.task_status ||
                      run.status}
                    <p>{run.error}</p>
                    {run.task_id && (
                      <button onClick={() => void openTask(run.task_id)}>
                        打开任务
                      </button>
                    )}
                  </div>
                ))}
              </details>
            </article>
          ))
        : connections.map((c) => (
            <article className="automation-card" key={c.id}>
              <b>
                {c.name} · {c.provider}
              </b>
              <p>
                {c.enabled ? states[c.status] || c.status : "已停用"} ·{" "}
                {c.configured ? "密钥已配置" : "密钥未配置"}
              </p>
              <p>{c.error}</p>
              <small>
                聊天 {c.chat_id}；允许用户 {c.user_id}
              </small>
              <details>
                <summary>连接配置说明</summary>
                <p>
                  在本机 .env
                  配置以下变量并重启后端。这里仅展示变量名，不接收密钥。
                </p>
                <code>{c.token_env}</code>
                {c.provider === "slack" && (
                  <>
                    <p>Socket Mode 还需要 App Token：</p>
                    <code>{c.app_token_env}</code>
                    <p>
                      开启 Socket Mode、订阅 message.channels（私聊使用
                      message.im）、授予对应事件权限和
                      chat:write，将机器人加入目标聊天。
                    </p>
                  </>
                )}
                <p>
                  {c.provider === "telegram"
                    ? "通过 BotFather 创建机器人，先向机器人发消息。使用 getUpdates 长轮询；同一机器人不能同时使用 webhook 或其他轮询程序。"
                    : "Bot Token 用于发送，App Token 需要 connections:write。"}
                </p>
              </details>
              <button
                disabled={busy}
                onClick={() => {
                  setEditing(c.id);
                  setDraft({
                    name: c.name,
                    provider: c.provider,
                    chat_id: c.chat_id,
                    user_id: c.user_id,
                    engine: c.engine,
                    permission_mode: c.permission_mode,
                    receive: !!c.receive,
                    send: !!c.send,
                    enabled: !!c.enabled,
                  });
                }}
              >
                编辑连接
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const result = await api(
                      `/connections/${c.id}/check`,
                      "POST",
                    );
                    setNotice(result.detail);
                  })
                }
              >
                验证机器人身份
              </button>
              {taskId && (
                <button
                  disabled={busy || !c.enabled || !c.send || !c.configured}
                  onClick={() =>
                    void act(async () => {
                      const result = await api(
                        `/tasks/${taskId}/handoff/${c.id}`,
                        "POST",
                      );
                      setNotice(result.detail);
                    })
                  }
                >
                  交接当前任务到此渠道
                </button>
              )}
              <details>
                <summary>收发记录</summary>
                {c.inbox.map((item: Row) => (
                  <div className="automation-record" key={item.event_id}>
                    接收 · {states[item.status] || item.status}
                    <p>{item.content}</p>
                    <p>{item.error}</p>
                    {item.task_id && (
                      <button onClick={() => void openTask(item.task_id)}>
                        打开任务
                      </button>
                    )}
                  </div>
                ))}
                {c.outbox.map((item: Row) => (
                  <div className="automation-record" key={item.id}>
                    发送 · {states[item.status] || item.status}
                    <p>{item.error}</p>
                    {item.status === "failed" && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void act(async () => {
                            await api(`/outbox/${item.id}/retry`, "POST");
                          })
                        }
                      >
                        重试明确失败的发送
                      </button>
                    )}
                  </div>
                ))}
              </details>
            </article>
          ))}
    </section>
  );
}

const example = JSON.stringify(
  {
    name: "csv-helper",
    version: "1.0",
    source: "本地方法包",
    extensions: [
      {
        name: "analysis",
        kind: "skill",
        content:
          "分析 CSV 时先确认字段和缺失值，图表说明筛选条件，报告列出验证结果。",
      },
    ],
  },
  null,
  2,
);
export function PluginPanel({
  projectId,
  api,
  changed,
}: {
  projectId: string;
  api: Api;
  changed: () => Promise<void>;
}) {
  const [items, setItems] = useState<Row[]>([]),
    [manifest, setManifest] = useState(example),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const reload = async () =>
    setItems(await api(`/projects/${projectId}/plugins`));
  useEffect(() => {
    let active = true;
    api(`/projects/${projectId}/plugins`)
      .then((r) => {
        if (active) setItems(r);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [projectId]);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await reload();
      await changed();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="automation-panel" aria-label="插件包管理">
      <h3>插件包</h3>
      <p>
        导入包含 Skill / stdio MCP 的 JSON
        包。默认停用，查看内容后可整体启用；来源由导入者声明，未进行安全审核。MCP
        仅在既有执行环境中运行。
      </p>
      <label>
        插件包 JSON
        <textarea
          rows={8}
          value={manifest}
          onChange={(e) => setManifest(e.target.value)}
        />
      </label>
      <button
        disabled={busy}
        onClick={() =>
          void act(async () => {
            await api(
              `/projects/${projectId}/plugins`,
              "POST",
              JSON.parse(manifest),
            );
          })
        }
      >
        导入插件包
      </button>
      {error && <p role="alert">{error}</p>}
      {items.map((item) => (
        <article className="automation-card" key={item.id}>
          <b>
            {item.name} · {item.version}
          </b>
          <p>来源：{item.source} · 未审核</p>
          <details>
            <summary>查看内容与完整性摘要</summary>
            <code>{item.digest}</code>
            <pre>{JSON.stringify(JSON.parse(item.manifest), null, 2)}</pre>
          </details>
          <p>
            {item.extensions.filter((e: Row) => e.enabled).length} /{" "}
            {item.extensions.length} 项已启用
          </p>
          <button
            disabled={busy}
            onClick={() =>
              void act(async () => {
                await api(`/plugins/${item.id}/enabled`, "PUT", {
                  enabled: !item.extensions.some((e: Row) => e.enabled),
                });
              })
            }
          >
            {item.extensions.some((e: Row) => e.enabled)
              ? "停用整个插件"
              : "启用整个插件"}
          </button>
        </article>
      ))}
    </section>
  );
}
