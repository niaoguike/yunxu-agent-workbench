import React, { useEffect, useState } from 'react';
type Row = Record<string, any>;
export function OrchestrationPanel({ projectId, api }: { projectId: string; api: (path: string, method?: string, body?: any) => Promise<any> }) {
  const [sync, setSync] = useState<Row>({});
  const [runs, setRuns] = useState<Row[]>([]), [sessions, setSessions] = useState<Row[]>([]), [goal, setGoal] = useState(''), [error, setError] = useState('');
  async function refresh() {
    const [r, s, m] = await Promise.all([api(`/projects/${projectId}/orchestrations`), api(`/projects/${projectId}/native-sessions`), api(`/projects/${projectId}/memory-sync`)]);
    setRuns(r); setSessions(s); setSync(m);
  }
  useEffect(() => { let active = true; const load = () => { if (active) refresh().catch(e => setError(e.message)); }; load(); const timer = setInterval(load, 2500); return () => { active = false; clearInterval(timer); }; }, [projectId]);
  async function act(fn: () => Promise<any>) { try { setError(''); await fn(); await refresh(); } catch (e: any) { setError(e.message); } }
  return <div>
    <h3>平台自主编排</h3><p>平台拆解目标、调度执行器会话、整合结果并验收。执行器自行管理内部 worker。</p>
    {error && <p role="alert">{error}</p>}
    <textarea aria-label="任务目标" value={goal} onChange={e => setGoal(e.target.value)} />
    <button disabled={!goal.trim()} onClick={() => act(() => api(`/projects/${projectId}/orchestrations`, 'POST', { content: goal }))}>提交目标</button>
    {runs.map(r => <article key={r.id}><h4>{r.data.goal}</h4><p>{r.state} {r.data.error}</p>
      {r.data.nodes.map((n: Row) => <p key={n.id}>{n.id} · {n.engine} · {n.status} · 尝试 {n.attempt}：{n.goal}<br />{n.reason} {n.error}</p>)}
      {['paused','conflict'].includes(r.state) && <button onClick={() => act(() => api(`/orchestrations/${r.id}/resume`, 'POST'))}>已检查工作区，恢复</button>}
      {!['delivered','failed','stopped'].includes(r.state) && <button onClick={() => act(() => api(`/orchestrations/${r.id}/stop`, 'POST'))}>停止</button>}
    </article>)}
    <h3>执行器会话</h3>{sessions.map(s => <p key={s.id}>{s.title} · {s.engine} · {s.status}<br />{s.session_id || '尚未创建原生会话'}</p>)}
    <h3>原生记忆自动同步</h3><p>{sync.enabled ? '已启用，每2秒同步；执行前后也会同步' : '未启用，请开启项目记忆'}</p>
    <p>Codex 账户记忆：{sync.codex_account_enabled ? "已接入，仅同步明确属于当前项目的摘要" : "当前执行模式未接入宿主机账户目录"}</p>
    {sync.error && <p role="alert">{sync.error}</p>}
    {(sync.sources || []).map((source: Row) => <p key={source.source}>{source.engine} · {source.source}<br />{new Date(source.updated_at * 1000).toLocaleString()}</p>)}
    <h3>导入共享记忆</h3><p>上传 Claude Code 或 Codex 的 UTF-8 记忆文件。内容按项目保存，两个执行器均可检索；需开启项目记忆。</p>
    {['claude','codex'].map(engine => <label key={engine}>{engine}<input type="file" accept=".md,.txt,.json" onChange={e => { const file = e.target.files?.[0]; if (file) { const form = new FormData(); form.append('file', file); act(() => api(`/projects/${projectId}/memory-import/${engine}`, 'POST', form)); } }} /></label>)}
  </div>;
}
