# 云端执行能力研究

日期：2026-11-03。仅官方文档静态核查与本地文件快照；未部署、购买、配置凭据或 benchmark。建议不是已批准架构。

## 来源事实

- Docker 以 namespaces 隔离、cgroups 管理资源；daemon、挂载和配置仍是攻击面，只应让可信主体控制 daemon。专用宿主降低影响范围，不能据此证明多租户安全。[官方安全文档](https://docs.docker.com/engine/security/)
- volume 独立于容器生命周期，保存文件；不等于保存内存进程，也不是宿主故障备份。[官方 volumes](https://docs.docker.com/engine/storage/volumes/)
- Docker pause 使用 freezer cgroup 挂起进程，不能当作释放宿主后的持久休眠；stop 默认 SIGTERM 后超时 SIGKILL。[pause](https://docs.docker.com/reference/cli/docker/container/pause/)、[stop](https://docs.docker.com/reference/cli/docker/container/stop/)
- gVisor 是应用内核，通过 Sentry 减少宿主系统接口暴露；不是完整 VM，不替代安全架构，映射文件和网络仍可访问。[安全模型](https://gvisor.dev/docs/architecture_guide/security/)、[架构](https://gvisor.dev/docs/architecture_guide/intro/)
- E2B pause 默认保存文件与内存进程，filesystem-only 模式恢复时重启；kill 为终态，暂停状态不自动过期。这不构成任意宿主故障均无损恢复的承诺。[持久化](https://docs.e2b.dev/sandbox/persistence)
- E2B 后台 command handle 支持 kill，区别于删除整个 sandbox；整棵子进程与远程副作用的取消保证仍需测试。[官方运行服务示例](https://docs.e2b.dev/network/public-url)
- SDK/controller secure access 与应用端口 traffic token 是不同鉴权通道。新项目自 2026-10-30 默认私有，旧项目保留旧默认，应显式设 allowPublicTraffic=false；私有端口缺少 e2b-traffic-access-token 返回 403。[控制面](https://docs.e2b.dev/sandbox/secured-access)、[应用端口](https://docs.e2b.dev/network/restrict-public-access)
- E2B 默认允许公网出口，可关闭或按 IP/CIDR/域名放行；域名放行要求拒绝其他出口，私有与 link-local 范围在 guest 外部阻断。[出口控制](https://docs.e2b.dev/network/internet-access)、[外部规则](https://docs.e2b.dev/network/restrict-public-access)
- E2B 支持全局及单次命令 env，但官方明确命令 env 在 OS 中不是私密数据；不能视为同沙箱扩展间 secret 隔离。[环境变量](https://docs.e2b.dev/sandbox/environment-variables)

## 建议与权衡（推论，待选择）

1. 受控开发验证可用专用宿主加普通容器验证协议与交付；对公网不可信代码比较 gVisor 等增强隔离与托管沙箱，不以 worktree 或目录当 sandbox。依据：[Docker](https://docs.docker.com/engine/security/)、[gVisor](https://gvisor.dev/docs/architecture_guide/security/)。
2. 自管 gVisor 路线掌握网络、存储与调度，但需承担修补、资源治理、快照与回收；托管路线减少生命周期设施工作，但依赖供应商接口、区域及故障处理。当前证据不足以替用户定案。依据：[gVisor 集成](https://gvisor.dev/docs/)、[E2B 生命周期](https://docs.e2b.dev/sandbox/persistence)。
3. 长期项目资料独立保存并导出成果，进程快照作恢复加速；分开定义停止任务、暂停环境和删除环境，记录取消意图后确认终止，不宣称撤销已发生副作用。依据：[文件生命周期](https://docs.docker.com/engine/storage/volumes/)、[快照生命周期](https://docs.e2b.dev/sandbox/persistence)。
4. 预览通过平台网关核对用户/项目归属，再注入后端 traffic token，只放行指定端口；使用独立来源，不向浏览器暴露控制 token。依据：[预览访问](https://docs.e2b.dev/network/restrict-public-access)、[controller access](https://docs.e2b.dev/sandbox/secured-access)。
5. 可信凭据代理与不可信扩展分开；不把平台密钥及全账户凭据放进项目代码 sandbox，按连接/项目发短期最小能力，出口默认拒绝后按需开放。依据：[env 不私密](https://docs.e2b.dev/sandbox/environment-variables)、[出口策略](https://docs.e2b.dev/network/internet-access)。

## 未知与后续验证

- gVisor 下浏览器、科学计算、包安装和原生扩展兼容性未测；支持容器生态不代表支持全部系统调用。[FAQ](https://gvisor.dev/docs/user_guide/faq/)
- 暂停时连接重建、快照中 secret 撤销、宕机数据损失窗口、文件导出一致性、取消竞态均未测；不能承诺恢复 SLA。[persistence](https://docs.e2b.dev/sandbox/persistence)
- 区域、配额、保留政策、审计、成本与故障切换未确定；正式选型固定 SDK/模板版本，验证双租户、过期授权、资源耗尽、子进程、WebSocket 和预览令牌泄露。本次无此运行证明。[版本及默认值差异](https://docs.e2b.dev/network/restrict-public-access)、[安全边界](https://gvisor.dev/docs/architecture_guide/security/)
