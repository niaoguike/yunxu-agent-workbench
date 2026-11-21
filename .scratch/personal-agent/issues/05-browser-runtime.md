Status: ready-for-human
Type: task

# 05 持久浏览器运行验证

代码已提供账户容器、持久卷、截图、点击输入、暂停恢复与逐次审批路径。当前 Docker CLI 无法连接 daemon，标准 Docker Desktop 路径不存在，未构建镜像或真实运行浏览器。

需要启动可用 Docker 服务，按 docs/personal-agent.md 构建 Dockerfile.browser 并启用 WORKBENCH_PERSONAL_COMPUTER=docker。

## Answer
命令、归属及审批边界通过协议夹具验证；真实容器运行和登录保留待验证。生产网络出口和多租户隔离仍未实现。
