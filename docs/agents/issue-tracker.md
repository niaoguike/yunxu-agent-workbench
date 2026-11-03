# Issue tracker: Local Markdown

任务和规格存放在 `.scratch/`。

## 文件约定

- 每个功能一个目录：`.scratch/<feature-slug>/`。
- 规格：`spec.md`。
- 每张任务单独存放：`issues/<NN>-<slug>.md`，从 01 编号。
- 分诊状态写在任务文件顶部的 `Status:` 行，取值见 `triage-labels.md`。
- 讨论追加到文件末尾的 `## Comments` 下。

## 技能操作

发布任务：创建对应的 Markdown 文件。
读取任务：读取用户指定路径或编号对应的文件。

## Wayfinder

- 工作地图：`.scratch/<effort>/map.md`，记录 Notes、Decisions-so-far、Fog。
- 子任务：`issues/NN-<slug>.md`。
- Type: research / prototype / grilling / task。
- 探索任务使用独立生命周期：Status: open / claimed / resolved。
- 依赖写为 `Blocked by: NN, NN`；所有依赖 resolved 后解除阻塞。
- 优先领取编号最小、未领取且无阻塞的 open 任务。
- 开工前保存 claimed；结束后追加 `## Answer`，保存 resolved，
  并向地图的 Decisions-so-far 追加摘要及链接。
