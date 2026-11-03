# Java 执行内核可行性核查

核查日期：2026-11-03。研究笔记，不变更提案或决策状态。

结论：不必使用 Python。Java 可以实现通用云端 Agent 工作台的编排内核；选择 Java 会替换先前 Python + LangGraph 的实现组合，项目、任务、云端工作空间、插件、人工确认点等业务模块仍成立。语言会影响框架选择和状态恢复的实现工作量，不能说完全没有影响。

## 官方证据与边界

- Spring AI 提供工具调用及自动工具执行循环；MCP 同时支持客户端和服务端。工具循环能够驱动 Agent，但这些能力本身不等于具备整个任务的持久暂停、进程重启恢复与副作用去重。[工具调用](https://docs.spring.io/spring-ai/reference/api/tools.html)、[MCP](https://docs.spring.io/spring-ai/reference/api/mcp/mcp-overview.html)。持久状态机和人工确认点仍须在应用层设计；不能把聊天历史持久化等同于运行检查点。
- LangChain4j 的 agentic 模块提供循环和多种编排模式，含 HumanInTheLoop、可恢复执行以及 AgenticScopeStore 持久化 SPI；官方明确整个模块仍为 experimental。持久化适配器、恢复位置、工具副作用与幂等仍需按具体版本验证，不能仅凭存在 SPI 就承诺端到端恰好一次执行。[Agents and Agentic AI](https://docs.langchain4j.dev/tutorials/agents/)。
- LangGraph4j 仓库位于独立的 langgraph4j 组织，说明可集成 LangChain4j 与 Spring AI。应按独立社区 Java 项目评估，不能因名称把它当成 LangChain 官方 LangGraph 的 Java 发行版，或假定 API、检查点行为完全对等。[项目仓库](https://github.com/langgraph4j/langgraph4j)。

## 可行路线

1. Java 编排服务 + Spring AI 或 LangChain4j 负责模型/工具接口；应用自己的任务状态机和数据库负责人工确认点、恢复、重试、取消及事件流。
2. Java 编排服务采用 LangChain4j agentic，或单独评估 LangGraph4j 的状态图能力；先验证暂停后重启恢复和工具幂等，再决定是否承担核心运行时职责。

以上均为候选路线，本笔记不锁定框架。与此前 Python + LangGraph 相比，业务边界无需改写，但运行时实现和恢复机制须重新验证，不能只替换语言名称。

编排语言与沙箱执行语言相互独立：Java 内核可以通过容器、进程或远程工具在云端工作空间执行 Python/Node 脚本；反过来 Python 编排也可以运行 Java 工具。用户的数据分析与脚本执行需求不要求平台内核使用 Python。
