# 核实云端执行与预览环境的能力

Type: research
Mode: AFK
Labels: wayfinder:research
Status: resolved
Claimed by: /root/sandbox_research
Blocked by: None
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

云端执行代码、扩展进程与开发应用预览，使用专用执行宿主加容器/增强隔离或托管沙箱分别具有什么能力与限制？核实文件持久化、休眠恢复、网络与凭据隔离、进程取消、预览入口鉴权和故障边界；不购买服务、不接凭据、不以 Docker/worktree 等同完整多租户隔离。产出适配本项目的事实与权衡。

## Completion criterion

核心隔离与生命周期假设均有官方来源；明确原型环境与对外服务环境要求，不捏造运行测试或价格。

## Dependencies

无，可直接开展。

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。


## Answer

已核实容器、gVisor 与托管沙箱生命周期及访问边界。普通容器适合受控开发验证；对公网不可信执行需增强隔离或托管沙箱并配套出口、凭据与预览鉴权。文件持久化不等于进程快照；SDK 控制鉴权不等于预览鉴权；最终路线仍待用户综合取舍。

证据：[完整研究](../research/cloud-execution-capabilities.md)、[Docker 安全](https://docs.docker.com/engine/security/)、[gVisor 安全模型](https://gvisor.dev/docs/architecture_guide/security/)、[E2B 持久化](https://docs.e2b.dev/sandbox/persistence)、[预览鉴权](https://docs.e2b.dev/network/restrict-public-access)。

