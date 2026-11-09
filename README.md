# 云序 · Agent 工作台

Python 控制面 + React 工作台，复用 Claude Code / Codex 原生 Agent loop。
项目、对话、事件、审批、阶段与成果持续保留；没有模型认证时明确显示不可运行，不生成演示回复。

目前是可运行的开发版本，尚未完成 PRD 全量验收。功能与验证边界见 [实现状态](docs/implementation-status.md)。

## Agent 核心机制

- 原生执行器承担模型与工具循环，平台承担持久任务、权限、协作和交付核验。
- 任务设置显式开启持久协作：MCP 指派桥、幂等回执、独立会话与工作区、成员并行、报告与冲突检查、全组停止。
- 连续失败触发受限辅助模型复核，结合超时宽限、过期结论失效及动作预算。
- 项目记忆工具、个性化指令与上下文阈值观测；Skill/MCP 扩展保留加载快照。

实现和真实模型验证边界见 [Agent 核心对齐](docs/Agent核心对齐.md)。

## 验收与对照评测

提供独立判分的 CSV 清洗、计费修复、资料核验任务集，以及循环治理、幂等指派和文件冲突故障对照。真实模型结果和离线故障注入分别统计，失败与超时保留在分母中。

复现命令见 [评测说明](docs/evaluations/README.md)，本次实测见 [验收报告](docs/evaluations/2026-11-06/report.md) 与 [方法及限制](docs/evaluations/2026-11-06/methodology.md)。

## 本地启动

需要 Python 3.12+、Node 22+。在项目根目录执行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[test]'
npm.cmd ci --prefix frontend
```

两个终端分别运行：

```powershell
./scripts/dev.ps1 api
```

```powershell
./scripts/dev.ps1 web
```

打开 http://localhost:5173，注册账户。默认关闭代码执行，项目、任务、文件、记忆、扩展配置仍可使用。
登录密码至少 10 位。若改用 `http://127.0.0.1:5173`，将 `WORKBENCH_ORIGIN` 设置成这个精确地址。

也可只运行构建后的页面与 API：

```powershell
npm.cmd run build --prefix frontend
$env:WORKBENCH_ORIGIN='http://127.0.0.1:8000'
.venv/Scripts/python.exe -m uvicorn workbench.main:app --host 127.0.0.1 --port 8000
```

Linux/macOS 使用 `.venv/bin/python` 和 `npm`。`scripts/dev.ps1 api` 会加载项目根目录已有的 `.env`；直接使用 Uvicorn 时需传入 `--env-file .env`，或直接设置服务进程环境变量。

## 接入执行器

推荐隔离运行：先启动 Docker Desktop / Docker Engine，再构建执行环境。

```powershell
docker build -f Dockerfile.runner -t agent-workbench-runner:local .
$env:WORKBENCH_RUNNER='docker'
./scripts/dev.ps1 api
```

执行镜像使用 Codex CLI `0.166.0` 和 Claude Agent SDK `0.2.177`。
运行时只挂载当前工作区，不挂 Docker socket 或宿主个人认证目录。管理员需通过部署流程为工作区保留的 `.native` 目录配置对应执行器认证；文件 API 不允许访问该目录。Linux 工作区须可被镜像 uid 1000 写入。
镜像就绪与认证就绪分别处理，认证错误由原生执行器报告。当前环境 Docker 服务没有启动，镜像构建及带凭据模型执行尚未验证。

只对可信个人开发环境，可**显式选择**本机执行：

```powershell
$env:WORKBENCH_RUNNER='trusted-local'
$env:CODEX_COMMAND='codex' # 或 codex.exe 的完整路径
./scripts/dev.ps1 api
```

该模式会使用宿主执行权限及原生执行器已有身份，仅适合本人可信项目，不提供多租户隔离。它不会因 Docker 不可用而自动启用。

`docker compose up --build` 可启动默认禁用执行的完整平台，页面在 http://localhost:8000。
不要直接给公开注册用户开放 Docker 执行；当前 Docker 配置没有证明是恶意代码的多租户安全边界。

## 已实现的工作流

- 统一任务入口：明确目标后动态执行，按需使用方法；交付清单检查通过后交付，可勾选人工验收。
- 工具权限默认 Auto：Codex 复用原生 `auto_review`，Claude 复用 SDK 原生 Auto；也可在任务设置切换 Ask。工具授权与阶段验收互不替代，自动审核结果可在对话中的记录区和活动中查看。
- 不再新建七阶段开发任务；旧任务历史保留，可在停止后显式转换为统一任务。
- 目标、模式、执行器或工作区变化使旧确认失效，历史成果快照仍可下载或按文件恢复。
- 同项目任务串行，跨项目可并行。执行中补充持久排队，可编辑或撤回；停止后队列暂停，手动恢复。
- Skill 和无密钥 stdio MCP 项目配置、加载版本快照；停用/修改会停止当前执行并重建后续会话。
- 账户长期指令、项目约定、分类记忆 CRUD/搜索；自主记忆默认关闭，启用后导入 Agent 的结构化项目事实。
- 持久协作者有独立工作区和原生会话；成员默认不继承项目 MCP；回写逐文件检查基线冲突。
- 文件上传、受限 ZIP / 公开 GitHub 导入、文本编辑、沙箱静态预览、私有下载、项目回收与恢复。

文件预览目前是禁脚本的静态预览，尚不包含启动任意应用服务的端口代理。

## 交付与检查契约

统一任务交付要求实际成果和 `delivery.json`：

```json
{
  "deliverables": ["report.md"],
  "checks": [
    {"name": "来源覆盖", "status": "passed", "evidence": "report.md 的参考资料节列出实际访问来源"}
  ]
}
```

平台检查文件真实存在、非空以及清单有效并保留哈希、运行与版本。
检查记录区分 `platform` 文件核验与 `engine_reported` 执行器证据，后者不冒充平台独立复跑。
检查失败最多自动补修一次，失败记录保留。阶段要求的文档必须在本轮新建或更新，旧文件不能直接充当新阶段结果。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q
npm.cmd test --prefix frontend
npm.cmd run build --prefix frontend
```

前端状态回归测试使用 Node 内置 TypeScript 支持，需要 Node 22.18+ 或 24+。

可选浏览器验证（已安装 Chrome/Edge）：

```powershell
.venv/Scripts/python.exe -m pip install playwright
# 使用独立测试数据启动生产构建后的页面
$env:WORKBENCH_DATA='.data/browser-check'
$env:WORKBENCH_ORIGIN='http://127.0.0.1:8765'
.venv/Scripts/python.exe -m uvicorn workbench.main:app --host 127.0.0.1 --port 8765
# 另一终端
.venv/Scripts/python.exe tests/browser_smoke.py
```

`scripts/probe_codex.py --command <codex路径>` 使用独立临时配置验证真实原生握手与线程创建，不调用模型或读取个人认证。
自动测试中的执行器夹具只验证平台/协议行为，不计作真实模型任务成功。

## 代码入口与运行边界

`backend/workbench/api.py` HTTP、身份和项目作用域；`workflow.py` 外层阶段/队列；`store.py` SQLite 事务；`runtime.py` 进程管理；`runner.py` 原生 SDK/协议；`frontend/src/main.tsx` 工作台。

`.data` 保存 SQLite、工作区和成果快照，备份需要同时保留三者；其中可能包含原生认证材料，不要提交仓库。
当前只支持单 API 进程，**不要启用多个 Uvicorn workers**。服务崩溃时执行中任务标记结果不明，同项目未派发任务暂停，须人工检查后继续，不自动重放工具操作。
每次运行有 30 分钟期限，单任务最多 60 轮，待执行补充最多 20 条；Docker 运行有内存/CPU/进程数限制。账户计费配额、应用预览代理、插件凭据保险箱与完整评测集尚未完成。

产品范围见 [PRD](docs/PRD.md)，架构见 [ADR 0002](docs/adr/0002-reuse-agent-harnesses.md)、[ADR 0003](docs/adr/0003-single-process-platform.md)。

## 本地持久化与重新打开

当前使用范围为本机运行，暂缓云端部署和跨设备验收。账号、项目、对话、执行事件、任务状态和最近打开的任务保存在服务端 SQLite；项目文件和成果保存在数据目录。关闭浏览器后重新打开并登录同一账户，会回到上次打开的任务并读取已有记录。

使用前述本地启动命令，保持相同的项目目录和 `WORKBENCH_DATA` 配置。默认数据目录为项目根目录的 `.data`；若 `.env` 指定了其他 `WORKBENCH_DATA`，以其指向的目录为准。不要删除或清空数据目录。清除浏览器缓存或登录过期后需要重新登录，服务端记录仍保留。

只关闭页面时，后端仍可继续执行任务；关闭后端或关机后，任务不会继续运行。重新启动后保留历史记录，原执行中任务按现有规则标记中断或暂停，检查工作区后手动继续，避免重复执行操作。未提交的输入和未保存的编辑内容不在持久化范围内。

备份时先停止后端，再完整复制实际数据目录，数据库、工作区和成果快照需要一起保留。已有的账户级恢复位置能力继续保留，未来部署时可用于同一服务的跨设备恢复。

## CSV 交互图表

打开项目的主任务，在对话区使用“上传 CSV 生成图表”。支持带表头的 UTF-8 CSV（2 MB、1000 行、40 列以内），至少一列全部为有限数值。生成由本地 CSV 工具完成，不需要模型认证。

图表卡片可切换分类和数值字段、柱状图或折线图，以及按列等值筛选。悬停查看数值，“查看数据”展开全部筛选结果；图表按原顺序展示前 100 行，不自动聚合。修改先实时预览，点击“保存修改为新版本”才会持久保存。历史版本可查看，也可在其基础上保存为新版本；关闭重开恢复最近保存版本及筛选条件。数据快照和版本保存在 SQLite，CSV 同时保存到项目 `charts/` 目录。

点击“请 Agent 分析当前数据”会把当前图表与筛选条件提交给真实执行器。执行器已配置时，也可以在对话中要求“把图表改成折线图”“标题改为第二季度收入”等；Agent 使用当前任务的 `chart_list` / `chart_revise` 工具保存新版本，页面自动刷新。没有可用执行器时会明确报错，不生成假回复。任意 HTML、浏览器和外部应用操作不属于此版本。

本地验收：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_charts.py tests/test_bridge.py tests/test_api.py -q
# 启动生产构建后的独立测试服务后（默认 http://127.0.0.1:8767）
.venv/Scripts/python.exe tests/browser_charts.py
```

## 自动化、聊天渠道与插件包

项目右侧“自动化”可设置每日/每周任务、立即执行、暂停/恢复和查看历史。“渠道”提供 Slack Socket Mode / Telegram 长轮询配置、指定聊天与用户限制、任务交接和结果回传。默认停用，真实接入需要在本机环境变量配置此连接专属机器人凭据。“扩展”支持包含 Skill / stdio MCP 的插件 JSON 包，导入后默认停用，可查看内容并整体启停。

使用步骤、权限、停机及发送恢复规则、插件示例和验证边界见 [自动化与渠道指南](docs/automations-and-channels.md)。
