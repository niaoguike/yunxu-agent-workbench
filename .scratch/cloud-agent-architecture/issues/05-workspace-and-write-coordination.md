# 确定工作空间与并行修改的归属

Type: grilling
Mode: HITL
Labels: wayfinder:grilling
Status: open
Blocked by: 02, 03
Parent: [云端 Agent 工作台技术决策地图](../map.md)

## Question

长期项目、临时计算、任务执行与成员修改区如何映射？项目级排队怎样与成员并行共存，输入基线、写入权、成果应用与冲突如何处理？选择执行环境路线，定义文件持久化、休眠、恢复、清理与访问隔离。

## Completion criterion

需明确执行器进程、原生工具、用户代码和模型认证分别位于何处；验证现成执行器的 shell 与配置目录不会泄露平台凭据。文件持久化不等于原生会话和在途进程都能恢复。

归属、生命周期、写入冲突和回收后果可解释；独立修改区与应用步骤明确；区分文件恢复与进程恢复。

## Dependencies

- [核实云端执行与预览环境的能力](02-cloud-execution-capabilities.md)
- [选择执行内核与适配边界](03-execution-kernel.md)

## Comments

地图建立阶段创建；未提供 Answer，不代表已有架构决定。

