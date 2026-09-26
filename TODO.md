# Agent Platform 实施清单

> 状态日期：2026-09-09。P0、P1、P2 已交付，P3 首批查询投影与事件 SSE 已通过容器验收；Artifact、trace/metrics 和管理台尚未实现。P2 真实模型调用已通过，费用为配置估算，供应商账单未接入。
> 技术栈：Java 21、Spring Boot 4、Temporal Java SDK、PostgreSQL。
> 原始完整平台规划保留在 `production-agent-platform-tech-stack-and-core-features.md`；本清单决定实际执行顺序。

## 完成标准与边界

- 每项行为变更先定义可执行验收，再实现最小代码。
- `[x]` 只表示已经实现并验证；设计、模拟和生产能力分别记录。
- 第一阶段演示工具使用固定合成数据，不调用模型、不操作真实服务。
- Temporal 负责工作流执行历史、等待、重试和恢复；业务查询库不另建一套执行调度状态机。
- AgentPermit4j 负责工具执行时的授权、审批绑定、幂等与审计；工作流等待审批不等于已经完成这些安全能力。
- API 与演示服务默认只绑定本机；生产身份认证完成前不得作为生产审批服务开放。
- 不以模型重跑代替 Temporal 历史回放，不宣称外部副作用无条件 exactly-once。

## P0：可运行骨架（本轮交付）

### P0.1 工程与文档

- [x] 明确范围、技术栈和模块边界。
- [x] 初始化 Git `main`、Maven Wrapper、Java 21 构建。
- [x] 建立 `agent-runtime-core`、`durable-execution`、`platform-api` 三个实际使用的模块。
- [x] 加入 README、架构文档、贡献规范、许可证和本地启动说明。
- [x] 配置编码、换行、忽略规则，避免产物、凭据和本机配置进入仓库。
- [x] GitHub Actions 执行 Maven `verify`，校验 Compose 配置。

验收：新环境使用 JDK 21、Python 3.9+ 和网络，先构建锁定的 AgentPermit 源码，再运行 Wrapper 验证；不依赖本机其他项目、用户全局 Maven 缓存或未发布 SNAPSHOT。

### P0.2 首个持久工作流

- [x] 定义不可变 Run 输入、审批决定、状态和查询快照。
- [x] 完成 `证据查询 → 等待审批 → 模拟执行 → 完成` 工作流。
- [x] 每次运行具有稳定 Workflow ID，工具逻辑操作 ID 在 Activity 重试间保持一致。
- [x] 批准后执行；拒绝和审批超时保持零工具执行。
- [x] 第一条有效审批决定生效，后续决定不能改变已经作出的选择。
- [x] Activity 有明确超时和有限重试；永久失败形成可解释终态。
- [x] 支持查询执行状态、请求取消。

验收：工作流测试覆盖批准、拒绝、超时、重复决定、读取重试、永久失败和取消；明确模拟工具不提供跨进程副作用幂等。

### P0.3 API 与本地运行

- [x] 提供创建 Run、查询 Run、提交审批、取消 Run 的 REST API。
- [x] 输入校验、重复 Run ID、未知 Run、无效审批状态返回明确 HTTP 状态。
- [x] 创建请求使用调用方稳定 request ID；重复创建不启动第二个工作流。
- [x] 提供 PostgreSQL 持久存储的 Temporal 本地 Compose 配置及 Temporal UI。
- [x] 提供 API 操作示例和兼容 Windows/Linux 的 Python smoke 脚本。
- [x] 实际验证 API 与工作流连接，区分内存测试与真实 Temporal 联调证据。
- [x] 实际验证 Worker 在等待审批时停止、重启后恢复同一个 Run。

验收：能按 README 启动并提交任务；批准、拒绝、取消的结果能被查询；重启验收使用持久 Temporal 服务，不把内存测试冒充跨进程恢复。

### P0.4 发布

- [x] 本地 Maven `verify`、`git diff --check`、Compose 配置检查通过。
- [x] 检查待提交内容，无凭据、私有业务数据、构建产物或本机绝对路径。
- [x] 创建 GitHub `agent-platform` 仓库，推送 `main`。
- [x] 核对远端提交与本地提交一致。
- [x] 核对首个 GitHub Actions 结果并修复本轮问题。
- [x] 更新本清单和 README 的实际能力及验证记录。

## P1：AgentPermit4j 正式接入与工具副作用

### P1.1 依赖和适配边界

- [x] 确定可复现的 AgentPermit4j 依赖来源：固定公开提交、归档 SHA-256 校验、项目隔离 Maven 仓库，禁止隐式依赖某台机器的 Maven 缓存。
- [x] 新增专用适配模块，Runtime core 保持不依赖 AgentPermit、Temporal 或 Spring。
- [x] 重启 Tool Activity 的执行全部通过 AgentPermit 管线，包括旧历史的兼容 Activity；当前工具仍为模拟操作。
- [x] 将 ALLOW、DENY、REQUIRE_APPROVAL、FAILED 映射为明确工作流分支。
- [x] 主体、租户、环境、工具定义和资源范围由服务端固定演示上下文构造，不接收调用方声明的授权上下文。

验收：用同一工具的允许、拒绝、待审批三种 fixture 证明调用没有绕过 AgentPermit；拒绝路径副作用为零。

P1.1 历史边界：接入真实 AgentPermit 管线和指纹校验，使用内存模拟审批。P1.2 为新 Run 替换持久审批装配；`LocalDemoGovernance` 只保留在旧历史兼容路径。旧版结果幂等仅在进程内生效，P1.3 新 Run 使用持久执行记录。

### P1.2 审批契约

- [x] 审批记录绑定具体 Run、Step、工具、规范化参数指纹、资源及过期时间。
- [x] 审批请求创建和通知入口可安全重试，不产生重复审批单；当前通知入口为持久待审批列表。
- [x] 审批 API 校验审批人身份与权限；工作流消息不能替代真实授权。本轮使用显式配置的本机 Basic 账户角色。
- [x] 处理拒绝、过期、重复批准、批准与取消同时发生的竞态；条件 UPDATE 确立取消与执行前 claim 的顺序。
- [x] 恢复后重新验证授权、策略和固定演示资源白名单；不能复用已经失效的审批。真实资源健康条件随真实工具接入。
- [x] 审批决定与待投递标记原子保存；Temporal 消息至少一次投递、失败可重投，旧通知不能确认更新后的取消状态。

验收：变更已批准参数、资源或身份后零执行；重复/乱序消息不会增加副作用。

P1.2 历史边界：审批 PostgreSQL 独立于 Temporal 内部库，Flyway 管理业务表；生产 SSO/多租户资源授权仍在 P5。P0/P1.1 等待 Run 没有可信审批记录，需取消后用新 ID 重建。P1.2 的旧执行路径不提供跨 Worker 结果复用，P1.3 通过版本标记为新 Run 引入新路径。

### P1.3 幂等与未知结果

- [x] 定义逻辑 operation ID；同一操作所有 Activity Attempt 使用相同 ID。
- [x] 验证跨 Worker 的结果复用与审批消费，不只检查单进程计数器。
- [x] 使用真实测试账本或支持幂等的可控下游，记录可核对的业务结果。
- [x] 注入“副作用已完成但结果未上报”的故障，验证不会换 ID 盲目重做。
- [x] 未知结果进入待核验状态，并支持查询下游、确认结果或人工结束任务。
- [x] 定义未来补偿的独立 ID、前置条件和失败/未知状态契约；当前 restart 不可逆，不实现或宣称回滚。

验收：并发重试、Worker 丢失及响应丢失都能说明副作用次数与最终状态；公开说明保证依赖和失效条件。

范围：受控 PostgreSQL 测试账本具有真实写入及唯一回执，但不重启真实服务。未知执行不接管、不盲目重试；人工关闭不证明无副作用。当前 restart 为 IRREVERSIBLE，无补偿执行器；独立补偿 ID、前置条件及失败/未知状态契约见架构文档，不能计为已实现可回滚工具。

P1.3 本地验证（2026-09-08）：Maven `verify` 72 项通过（core 8、adapter 28、Workflow 18、旧历史 6、HTTP/投递 12）。新增测试覆盖独立连接执行权争抢与下游去重、响应丢失、未知结果禁止重做、过期后读取确认结果、人工关闭保留审计、迟到结果和旧投递确认。Temporal 为内存服务、业务库为 H2；Python smoke 语法、Compose 配置和 `git diff --check` 通过。本机 Docker 引擎管道仍不可用，真实容器证据来自 CI。

P1.3 真实联调（2026-09-08）：[GitHub CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34238414365) 对功能提交 `8f02c442e02598b50ae20e08ecfafdc86b3d9e6d` 的上游 113 项、项目 72 项测试及全部 smoke 验收通过。账本操作 `run-2addec7b-1358-4b6b-aec6-96181ae0794c` 提交后强制终止 Worker，PID `6065 → 6309`、端口 `9091 → 9092` 恢复同一 Run；回执不变，generation 只增加一次。无回执的未知任务在再次重启后保持待核验，operator 留痕关闭且无新增账本写入。原有审批保存、PostgreSQL 重启、outbox 自动补投及拒绝/取消/超时检查也全部通过。

## P2：真实 Agent 执行循环

- [x] 用独立模型 Activity 调用 Spring AI，Workflow 内不直接执行模型或网络请求；默认离线，显式启用 OpenAI-compatible 服务。
- [x] 模型输出使用明确 schema，工具名与参数必须经过服务端校验；离线 fixture 和远端响应共用严格解析器。
- [x] 区分模型错误、工具读取错误、策略拒绝、上下文不足、核验未确认和步数耗尽；写结果未知继续走 P1.3 核验。
- [x] 实现有限步骤的 plan/execute/verify 循环，固定模型/提示词/工具/runbook 版本；支持默认离线与显式远端模式。
- [x] 增加最大步骤数、总时间、token/cost 预算；重试计入预算。明确区分离线模拟与服务报告 usage / 配置费用估算。
- [x] 已上报的模型 Activity 结果随 Temporal 历史保存，回放使用既有结果；未上报请求仍可能再次调用。
- [x] 模型规划、证据读取、写工具和只读核验分属独立 Activity，不把循环整体重试。
- [x] 检索/上下文先使用固定 runbook；向量库和 compaction 暂不接入。

验收：离线模型 fixture 覆盖成功、非法工具、超预算和失败后重新规划；真实模型联调单独记录，CI 不依赖付费 API。

P2 范围：模型可补查证据、申请一次受审批的 orders 重启、核验账本或结束诊断；未知写阻塞后续规划，不允许第二次写或跳过核验宣称成功。默认 fixture 不调用真实 LLM；Spring AI 模型模式由服务端显式启用。

Spring AI 实现（2026-09-09）：使用 2.0.1 与官方 SDK 4.49.0，关闭 SDK 和连接层隐藏重试，每个物理请求独立预留。冻结模型/端点/prompt/价格/额度到 Run 和预算业务表；旧输入、旧预算行保持 offline。严格校验 usage，缺失时保留预留并失败，不降级假数据；费用标记为 `PROVIDER_USAGE_CONFIGURED_ESTIMATE`，不等同供应商账单。API Key 只在本机环境中使用，不持久化。系统指令与上下文分离，模型不能绕过原工具审批和治理。

真实模型验收（2026-09-09）：`scripts/smoke-model.py` 复用已有本机配置，对自建 OpenAI-compatible 服务的 `claude-sonnet-4-6` 完成一次完整规划/审批/测试账本写入/核验/结论流程；Run `run-ffeff979-0405-464b-aeb8-42336348955d`，3 次实际模型请求，服务报告 2227 token，参考费用 9489 microUSD，零剩余预留，并回放历史。参考单价为每百万输入 USD 3、输出 USD 15，版本 `sonnet-reference-2026-09-09`；这是估算，不是网关账单，也不独立证明上游模型路由。此项为真实网络请求 + 内存 Temporal/H2，生产服务工具和 PostgreSQL 跨进程真实模型恢复未计为验收。

Spring AI 本地回归（2026-09-09）：Maven `verify` 128 项通过（core 14、adapter 28、Workflow 38、固定旧历史 12、API/模型/预算/投递 36）。新增测试覆盖旧输入与旧预算行保持离线、profile 恢复绑定、实际 HTTP 请求输出上限、500 和断连接无隐藏重试、缺失 usage 不当零、端点漂移不发送凭据、错误正文不进入异常、服务用量结算及缓存重试，以及 HTTP mock 下的完整审批/核验流程。Python 脚本语法与 `git diff --check` 通过。

Spring AI CI（2026-09-09）：[GitHub Actions](https://github.com/mat973252-coder/agent-platform/actions/runs/34293091866) 对功能提交 `ffd51e1c3cc3ec4141446fc6b8833391a54d08e7` 的上游 113 项、项目 128 项测试及全部 PostgreSQL + Temporal smoke 通过。HTTP mock Run 完成三次模型请求并结算 3300 token / 13500 microUSD；等待审批的 `run-d79f1431-02b6-412a-bc05-84d7712d724f` 经 Worker `5753 → 6653` 和审批 PostgreSQL 重启恢复。账本提交后的 `run-b54e656a-06e8-43ed-9f0b-85677650a873` 经 Worker `6996 → 7276` 保持只有一次写入；离线模型响应中断后的 `run-593a7b0b-05a9-476c-a2c9-15064a0e286e` 经 Worker `8255 → 8472` 保留未知预算占用。CI 只使用 fixture 和本地 HTTP mock，不包含付费模型调用；真实模型网络验收见上，二者分别报告。

预算实现（2026-09-09）：服务端冻结 6 步/900 秒/100000 token/1000000 microUSD 的默认限额，审批等待、Activity 重试及退避包含在总时限内。每次模型请求事务预留，成功结算与决策缓存同事务；未知响应保留全额预留，重试必须另有额度。新增只读 `/budget` 查询，原 P2 历史通过版本分支兼容。超时后仅保留未知写的只读核验/人工关闭，不能回滚已开始操作。计量模式固定 `OFFLINE_SIMULATED`，不是供应商账单。

预算本地验证（2026-09-09）：核心、Activity、Workflow 先补测试观察预期失败后实现。Maven `verify` 117 项通过（core 12、adapter 28、Workflow 37、固定旧历史 12、API/预算/fixture/投递 28）。覆盖并发预算争抢、丢响应保留、落账后重试不重复调用、迟到响应各自计量且不覆盖首个结果、无效输出计量、审批总时限、退避提前耗尽、超时后未知写核验，以及 HTTP 伪造预算不能覆盖服务端限额。Temporal 为内存服务、业务库为 H2；Python smoke/历史 JSON 语法及 `git diff --check` 通过。Windows Docker 引擎管道仍不可用，容器证据来自 CI。

预算真实联调（2026-09-09）：[预算 GitHub CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34250825483) 对功能提交 `6e310951311583169699f1b277e7b42695854c6a` 的上游 113 项、项目 117 项测试及全部 smoke 通过。等待审批的 `run-d754d119-f265-4c6f-880d-6c3a9cc82c9e` 经 Worker `5563 → 6397` 和审批 PostgreSQL 重启后保持预算与固定 deadline，完成后结算 2784 token / 3000 microUSD。账本提交后退出的 `run-485e954e-f457-4cb1-bb45-f90c9fbf5d5a` 经 Worker `6702 → 6944` 恢复仍只有一次写入。模型响应后暂停的 `run-1200cf8f-d15e-4bad-96e8-ed5beac8aa2f` 在日志确认响应已返回后强制结束 Worker `7813`，新 Worker `8038` 在另一端口恢复；原尝试保留 2560 token / 5000 microUSD 预留，后续三步合计结算 2784 / 3000，物理尝试总数为 4。原审批补投、拒绝/取消/超时和未知写人工关闭也通过。所有模型用量仍为合成计量，不是实际 LLM 消费。

本地验证（2026-09-08）：Maven `verify` 94 项通过（core 10、adapter 28、Workflow 30、固定旧历史 9、API/fixture/解析/投递 17）。覆盖非法模型输出、稳定决策 ID、模型重试耗尽、证据与核验读取失败后重新规划、步数上限、拒绝第二次写、拒绝未经核验的成功声明、未知结果阻塞、以及回放不调用模型/工具。Temporal 为内存服务、业务库为 H2；Python smoke 语法、打包 JSON 语法和 `git diff --check` 通过。

真实联调（2026-09-08）：[P2 首批 GitHub CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34245518880) 对功能提交 `53b193130e82e17ae871259cbabe081b0df1746e` 的上游 113 项、项目 94 项测试及全部 smoke 通过。等待审批的 `run-b9b1e49e-2710-4d9c-a698-8ba6ec300074` 在 Worker `5215 → 6065` 和审批 PostgreSQL 重启后保持已记录决策；批准后完成三步模型决策及账本核验。账本提交后中断的 `run-0389949b-41fd-401e-b77b-7972c1a0b852` 由 Worker `6380 → 6620`、端口 `9091 → 9092` 恢复，只有一次账本写入，随后完成核验和结论；未知任务人工留痕关闭及原有拒绝/取消/超时检查也通过。此证据仍使用离线模型和测试账本，不是生产模型或真实服务联调。

## P3：平台数据与可观测性

- [x] 建立业务 Run/Step/Attempt 查询视图，明确与 Temporal 历史的映射及修复方式。Step 为 Activity 调度，Attempt 来源边界见下。
- [x] 设计事件 ID 与顺序、幂等写入和断线重连游标；SSE 仅负责传输。
- [x] PostgreSQL 业务 schema 使用版本化迁移，不与 Temporal 内部表混用。V5 保存历史元数据投影。
- [x] 提供 `scripts/export-status.py`：经本机已认证 operator 账户只读导出 `mat-console.status/1` JSON 到本地文件；不新增未认证路由、不发布状态数据，浏览器 `?url=` 联调由操作者本地 CORS 静态托管。
- [ ] 大结果/Artifact 通过引用存储，控制工作流历史和 payload 大小。
- [ ] 关联 Run、Activity、工具调用与审批的 trace ID。
- [ ] 记录成功率、耗时、重试次数、审批等待、工具错误和预算使用。
- [ ] 定义日志、审计、模型输入输出的脱敏及保留规则。
- [ ] 第一版管理台只做任务列表、详情时间线、审批与失败原因。

验收：单次 Run 的状态和证据可追溯；日志不包含秘密；断线后能补齐事件，观测系统故障不改变业务执行决定。

P3 首批范围：通过 public API 按需投影 Temporal 历史，不改变 Workflow/Activity 或原执行依赖。Step 对应 Activity 调度；历史只能提供最终可见 attempt，模型物理请求尝试另从预算表查询，二者不混算。新增任务列表、历史详情、事件分页和 SSE；事件只含元数据，不复制模型/证据/错误正文。SSE 支持 execution 绑定游标、客户端去重、约 25 秒重连和 32 连接上限。投影在历史保留期内可重建，无后台全量归档、Continue-As-New 或自动清理。当前查询视图不计为完整 Activity 尝试审计或完整观测平台。

P3 本地验证（2026-09-09）：先观察缺失投影类型、缺失 HTTP 路由和损坏缓存返回 500 的预期失败，再实现对应行为。Maven `verify` 141 项通过（core 14、adapter 28、Workflow 38、固定旧历史 12、API/模型/投影 49）。新增 13 项覆盖中间重试不伪造、业务失败与 Temporal COMPLETED 区分、元数据脱敏、独立连接竞争、重复/陈旧刷新、缺行和损坏投影重建、execution 绑定、事件分页/SSE 游标、32 连接上限与主动断开释放、观测表故障时原 Run 继续取消和结束。内存 Temporal 的 List RPC 明确返回 UNIMPLEMENTED，列表成功分页由真实容器 smoke 单独验收。Python smoke 语法与 `git diff --check` 通过；本机 Docker 引擎仍不可用，容器验证待 CI。

P3 真实联调（2026-09-09）：[P3 GitHub CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34299312533) 对功能提交 `9328c0eebcbe28f2c5dca77736888382d6938e21` 的上游 113 项、项目 141 项测试及全部 PostgreSQL + Temporal smoke 通过。Run `run-b3c63041-cf11-44ca-b440-22562800f2c5` 的投影跨 Worker `5785 → 6682` 和审批 PostgreSQL 重启保留；SSE 从 execution `01a083c9-7ab9-74aa-bdfe-f80edd673e8b` 的 event 38 续传至 73，删除该 Run 的观测投影后重建仍保持相同事件 ID。真实 Visibility 任务列表分页通过。账本提交后恢复仍只有一次写入；模型响应丢失后查询保留原 RESERVED/未知用量及下一次 COMPLETED 尝试。后续补上 Actions 临时数据库口令的日志掩码；口令仅用于当次 runner 的合成测试容器。P3 后续项未计为完成。

## P4：升级、故障与回归验证

- [ ] 固定历史样本进入 CI，使用 WorkflowReplayer 检查代码兼容性。
- [ ] 区分工作流代码版本、模型版本、提示词版本、工具版本和数据契约版本。
- [ ] 定义长任务跨版本部署策略，明确旧 Worker 的保留/退出条件。
- [ ] 测试 Activity 执行中停止 Worker、服务短暂不可用和数据库重启。
- [ ] 验证取消传播、心跳与资源清理；不能承诺撤销已经完成的外部动作。
- [ ] 定义工作流历史上限与 Continue-As-New 策略，保持业务 Run/operation ID 稳定。
- [ ] 单独建立 Eval 数据集；模型重跑评测不能执行真实写工具。
- [ ] 对重试策略、超时和并发做小规模负载验证后再确定运行参数。

验收：能重现并解释各类故障；CI 同时保护流程兼容性与业务行为，但不把回放通过等同于新模型质量提升。

## P5：生产化与后续平台模块

- [ ] API 身份认证、资源权限、审批权限、访问审计及请求限流。
- [ ] 密钥注入、连接加密、网络隔离与最小权限部署。
- [ ] Temporal/业务库的备份、恢复演练、保留策略和监控告警。
- [ ] Worker 扩缩容与任务队列划分，验证资源用量后再调优。
- [ ] 不可信代码出现后再接 Sandbox，验证文件、网络、CPU/内存及超时边界。
- [ ] 第二种实际运行时出现后再接 Python Adapter，验证平台契约与框架解耦。
- [ ] Agent/Tool Registry、完整 Context Runtime、Gateway 和 K8s Controller 按真实需求推进。

验收：各模块以具体场景和故障验收进入主线，不以空目录或配置清单计为完成。

## P0 验证记录（2026-09-06）

- 本地 `mvnw.cmd -B -ntp verify`：22 项测试通过（领域 8、工作流 9、HTTP API 5）。工作流测试使用内存 Temporal 服务；包含 Activity 重试和执行历史回放。
- `docker compose config --quiet` 与 `scripts/smoke.py` Python 语法检查通过。
- [GitHub 仓库](https://github.com/mat973252-coder/agent-platform) 已创建并推送 `main`；功能与部署修复提交为 `4fb211cd8248b514f33600ae004c1534eb4652b3`，已核对与远端一致。
- [GitHub Actions 验收](https://github.com/mat973252-coder/agent-platform/actions/runs/34025622000)：Ubuntu / JDK 21 下 Maven 22 项测试、Compose 校验、PostgreSQL + Temporal 启动与跨进程 smoke 全部通过。
- 恢复证据：`run-817bb01b-f20b-4af5-9c64-228db432bbd0` 在等待审批期间强制结束 Worker，进程 PID `3818 → 3940` 后查询快照一致；批准后完成模拟执行。拒绝、取消、审批超时和重复 Run 保护检查通过。
- 首轮 CI 暴露 Temporal 镜像要求动态配置文件存在的问题；已补齐文件和只读挂载，并由上述 CI 验证修复。
- 本机 Docker Desktop 引擎启动失败，因此本地容器联调尚未完成；真实容器恢复证据来自 GitHub CI，未将内存测试计为跨进程恢复。

## P1.1 验证记录（2026-09-07）

- 先补适配层与新增工作流分支测试，观察缺少适配类/结果类型/Activity 契约的预期编译失败，再实现对应代码。
- `python scripts/bootstrap-agentpermit.py`：从公开提交 `c33911c595e5718b144d2bdb939bb3e23e17c909` 构建 AgentPermit `0.2.0`，校验归档 SHA-256；上游所需模块的 113 项测试通过。
- `mvnw.cmd -B -ntp verify`：37 项项目测试通过（core 8、adapter 8、workflow 14、旧历史回放 2、HTTP API 5）。
- 旧历史样本取自 P0 提交 `2dc4cc3`，分别覆盖等待审批和已批准完成；保留原 Workflow ID 验证回放，Activity 不被重新执行。
- [P1.1 GitHub CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34049953194) 全部通过，验收提交为 `f71b603e4969a752a2d3fac8f404fff7b2c775e9`。全新 Linux 环境从锁定源码构建依赖，完成上游测试、项目 37 项测试、Compose、PostgreSQL + Temporal 启动，以及带治理原因码的 Worker 强制退出/恢复 smoke；拒绝、取消、超时与重复请求检查通过。
- 本机 Docker 引擎仍不可用，未宣称已完成本地容器联调。该次验收时 P1.2/P1.3 尚未完成；P1.2 后续交付见下。

## P1.2 验证记录（2026-09-08）

- 先验证未认证请求错误地返回 404 而非 401、单独发送 APPROVE Signal 错误地执行工具，以及缺失持久审批类的预期失败，再实现身份校验、数据库记录和新工作流路径。
- 本地 `mvnw.cmd -B -ntp verify`：55 项测试通过（core 8、adapter 18、workflow 15、旧历史回放 4、HTTP API/消息投递 10）。审批存储测试使用 H2，工作流测试使用内存 Temporal，不计为真实 PostgreSQL 恢复。
- 存储测试覆盖原始绑定/截止时间不可变、重复决定、拒绝、过期、审批人权限变化、策略关闭、取消/claim 并发竞争、原审批审计身份保留，以及过期边界上的决定返回语义。HTTP 测试覆盖未认证、operator 越权、写请求头、审批身份记录、重复与相反决定。
- `git diff --check`、Compose 配置校验与 Python smoke 语法检查通过。H2 2.4.240 的 CHECK 跨连接问题通过测试保留 DDL 连接处理；真实 PostgreSQL 使用相同迁移脚本。
- [P1.2 GitHub CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34192140667) 全部通过，验收提交 `3d2c7c6895d8fe5445947503bdd5f2501ddcf65c`。全新 Linux 环境构建锁定依赖，上游 113 项与项目 55 项测试均通过。
- CI 恢复证据：`run-da6d0a32-7700-4e00-8f29-6eb1d8d181a1`，Worker PID `4557 → 5321`；等待中的 Run 和审批记录跨 Worker 重启一致。关闭投递后保存批准，再强制终止应用、重启审批 PostgreSQL，随后应用重启自动补投，保持原审批绑定及审批人并完成模拟执行。
- CI 同时验证 operator 不能批准、同一决定重复提交、拒绝、取消、审批超时和重复 Run 保护。本机 Windows Docker 引擎仍不可用，实际容器联调证据来自 CI。
- P1.2 完成范围为固定演示资源、本机认证账户和独立持久审批；真实模型/工具、生产身份源、跨 Worker 副作用幂等及未知结果核验均未计为完成。
