import React, { useEffect, useState } from "react";
type Row = Record<string, any>;
type Props = { taskId: string; api: (path: string, method?: string, body?: unknown) => Promise<any>; changed: () => Promise<void> };

function ChartCard({ chart, api, reload, changed }: { chart: Row; api: Props["api"]; reload: () => Promise<void>; changed: Props["changed"] }) {
  const latest = chart.versions[chart.versions.length - 1];
  const [version, setVersion] = useState(latest.version);
  const [config, setConfig] = useState<Row>(latest.config);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setVersion(latest.version); setConfig(latest.config); }, [latest.version]);
  const data = chart.data;
  const rows = data.rows.filter((r: string[]) => !config.filter_column || r[data.columns.indexOf(config.filter_column)] === config.filter_value || config.filter_value === "");
  const points = rows.slice(0, 100).map((r: string[]) => ({ label: r[data.columns.indexOf(config.x)], value: Number(r[data.columns.indexOf(config.y)]) }));
  const low = Math.min(0, ...points.map((p: Row) => p.value));
  const high = Math.max(0, ...points.map((p: Row) => p.value));
  const scale = Math.max(Math.abs(low), Math.abs(high), 1);
  const bottom = low / scale, top = high / scale;
  const y = (value: number) => 20 + (top - value / scale) / (top - bottom || 1) * 180;
  const x = (index: number) => 50 + (index + .5) * 600 / Math.max(points.length, 1);
  const patch = (key: string, value: string) => setConfig({ ...config, [key]: value });
  return <article className="chart-card" aria-label="CSV 图表">
    <header><b>{config.title}</b><label>历史版本 <select aria-label="图表历史版本" value={version} onChange={e => {
      const v = chart.versions.find((v: Row) => v.version === Number(e.target.value));
      setVersion(v.version); setConfig(v.config);
    }}>{chart.versions.map((v: Row) => <option key={v.version} value={v.version}>v{v.version}</option>)}</select></label></header>
    <small>本地 CSV 工具 · 来源 {chart.source} · 当前预览基于 v{version}</small>
    <div className="chart-controls">
      <label>图表标题<input value={config.title} maxLength={160} onChange={e => patch("title", e.target.value)} /></label>
      <label>图表类型<select value={config.kind} onChange={e => patch("kind", e.target.value)}><option value="bar">柱状图</option><option value="line">折线图</option></select></label>
      <label>分类字段<select value={config.x} onChange={e => patch("x", e.target.value)}>{data.columns.map((c: string) => <option key={c}>{c}</option>)}</select></label>
      <label>数值字段<select value={config.y} onChange={e => patch("y", e.target.value)}>{data.numeric.map((c: string) => <option key={c}>{c}</option>)}</select></label>
      <label>筛选字段<select value={config.filter_column} onChange={e => setConfig({ ...config, filter_column: e.target.value, filter_value: "" })}><option value="">不筛选</option>{data.columns.map((c: string) => <option key={c}>{c}</option>)}</select></label>
      <label>筛选值<select disabled={!config.filter_column} value={config.filter_value} onChange={e => patch("filter_value", e.target.value)}><option value="">全部</option>{Array.from(new Set<string>(data.rows.map((r: string[]) => r[data.columns.indexOf(config.filter_column)]))).filter(Boolean).map(c => <option key={c}>{c}</option>)}</select></label>
    </div>
    <svg viewBox="0 0 700 255" role="img" aria-label={`${config.title}，${config.kind === "bar" ? "柱状图" : "折线图"}，${rows.length} 行`}>
      <line x1="50" x2="650" y1={y(0)} y2={y(0)} stroke="currentColor" />
      <text x="0" y="18" fontSize="11">{high.toLocaleString()}</text><text x="0" y="205" fontSize="11">{low.toLocaleString()}</text>
      {config.kind === "line" && <polyline fill="none" stroke="#5263d5" strokeWidth="2" points={points.map((p: Row, i: number) => `${x(i)},${y(p.value)}`).join(" ")} />}
      {points.map((p: Row, i: number) => <g key={i}>
        {config.kind === "bar" ? <rect x={x(i) - 220 / Math.max(points.length, 1)} y={Math.min(y(0), y(p.value))} width={440 / Math.max(points.length, 1)} height={Math.max(Math.abs(y(p.value) - y(0)), 1)} fill="#5263d5"><title>{p.label}: {p.value}</title></rect> : <circle cx={x(i)} cy={y(p.value)} r="3" fill="#5263d5"><title>{p.label}: {p.value}</title></circle>}
        {i % Math.max(1, Math.ceil(points.length / 8)) === 0 && <text x={x(i)} y="230" textAnchor="middle" fontSize="11">{p.label.slice(0, 12)}</text>}
      </g>)}
    </svg>
    <p role="status">筛选后 {rows.length} 行。图表按 CSV 原顺序展示前 100 行，不聚合；下表包含全部筛选数据。</p>
    <details><summary>查看数据</summary><div className="chart-data"><table><thead><tr>{data.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead><tbody>{rows.map((r: string[], i: number) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table></div></details>
    <button disabled={busy || !config.title.trim()} onClick={async () => {
      setBusy(true); setError("");
      try { await api(`/charts/${chart.id}/versions`, "POST", { ...config, base_version: latest.version }); await reload(); await changed(); }
      catch (e) { setError((e as Error).message); } finally { setBusy(false); }
    }}>保存修改为新版本</button>
    <button disabled={busy} onClick={async () => {
      setBusy(true); setError("");
      try {
        await api(`/tasks/${chart.task_id}/messages`, "POST", { content: `请分析项目文件 ${chart.source} 中的数据。图表 ${chart.id}，已保存版本 v${version}，当前预览条件 ${JSON.stringify(config)}。请读取 CSV 后按当前筛选分析，说明发现与依据。` });
        await changed();
      } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
    }}>请 Agent 分析当前数据</button>
    {error && <p role="alert">{error}</p>}
  </article>;
}

export function Charts({ taskId, api, changed }: Props) {
  const [charts, setCharts] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const reload = async () => setCharts(await api(`/tasks/${taskId}/charts`));
  useEffect(() => { let active = true; api(`/tasks/${taskId}/charts`).then(c => { if (active) setCharts(c); }).catch(e => { if (active) setError(e.message); }); const timer = setInterval(() => { api(`/tasks/${taskId}/charts`).then(c => { if (active) setCharts(c); }).catch(e => { if (active) setError(e.message); }); }, 5000); return () => { active = false; clearInterval(timer); }; }, [taskId]);
  return <section className="charts" aria-label="交互图表成果">
    <label>上传 CSV 生成图表<input type="file" accept=".csv,text/csv" disabled={busy} onChange={async e => {
      const file = e.target.files?.[0]; if (!file) return;
      e.target.value = ""; setBusy(true); setError("");
      try { const form = new FormData(); form.append("file", file); await api(`/tasks/${taskId}/charts`, "POST", form); await reload(); await changed(); }
      catch (e) { setError((e as Error).message); } finally { setBusy(false); }
    }} /></label>
    <small>UTF-8 CSV，含表头，最多 2 MB / 1000 行；修改后点击保存以便重开恢复。</small>
    {busy && <p role="status">正在生成图表…</p>}
    {error && <p role="alert">{error}</p>}
    {charts.map(chart => <ChartCard key={chart.id} chart={chart} api={api} reload={reload} changed={changed} />)}
  </section>;
}
