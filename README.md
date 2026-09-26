# Agent Platform

[![verify](https://github.com/mat973252-coder/agent-platform/actions/workflows/verify.yml/badge.svg)](https://github.com/mat973252-coder/agent-platform/actions/workflows/verify.yml)

Java + Temporal 的持久化 Agent 执行基础项目。

**当前阶段：P3 首批 Run/Step 查询投影、模型尝试查询与 SSE 事件续传，验收记录见 TODO。** 新 Run 执行受步骤、总时间及 token/cost 限额约束的“规划 → 工具 → 核验 → 结论”循环。默认使用离线 fixture，也可显式配置 OpenAI-compatible Chat Completions 服务。重启仍通过真实 AgentPermit4j 管线、持久审批和数据库测试账本；它不重启真实服务。远端 token 来自服务报告，费用按配置单价估算，未接供应商账单、生产业务工具或生产身份系统。

## 技术与结构

- Java 21、Spring Boot 4.0.8、Spring AI 2.0.1、Temporal Java SDK 1.38.0。
- 本地 Temporal Server 1.31.0 使用 PostgreSQL 16 持久存储，Temporal UI 2.49.1。
- `agent-runtime-core`：纯 Java 领域数据。
- `durable-execution`：确定性 Agent 循环、Workflow 与独立规划/工具 Activity 契约。
- `agentpermit-adapter`：可信调用构造、AgentPermit 决策映射及 JDBC 审批存储。
- `platform-api`：Spring Boot API、Worker、Spring AI 模型适配、严格输出解析、离线 fixture 与受控账本工具。
- [详细 TODO](TODO.md)、[架构边界](docs/architecture.md)、[完整平台规划](production-agent-platform-tech-stack-and-core-features.md)。

## 构建与测试

需要 JDK 21、Python 3.9+ 和网络。AgentPermit 尚未发布到 Maven Central，先从固定公开提交构建依赖，再验证主项目：

```powershell
python scripts/bootstrap-agentpermit.py
.\mvnw.cmd -B -ntp verify
```

Linux/macOS：先运行 `python3 scripts/bootstrap-agentpermit.py`，再运行 `./mvnw -B -ntp verify`。

依赖锁定在 [`.mvn/agentpermit.lock.json`](.mvn/agentpermit.lock.json)：公开提交 `c33911c595e5718b144d2bdb939bb3e23e17c909`、版本 `0.2.0`，下载后校验 SHA-256。脚本构建必要模块并运行其测试；主项目使用 `var/maven-repository` 隔离仓库，不依赖其他 checkout 或用户全局 Maven 缓存。锁定更新后重新运行脚本；不自动跟随远端 main 或本机未发布分支。

项目测试覆盖领域校验、真实 AgentPermit 决策、审批绑定/过期/取消竞态、独立数据库连接抢占执行权、下游去重、未知结果及人工关闭、工作流分支、Activity 重试、消息重投、旧历史回放和 HTTP 身份权限。测试中的 Temporal 使用内存服务，业务库使用 H2；真实 PostgreSQL 联调独立验收。依赖自身的 113 项测试单独记录。

P2 新增严格 JSON/工具参数校验、模型重试、步数上限、读失败后重新规划、完成前核验，以及回放不再次调用模型的测试。默认模型是打包 JSON fixture，不发出模型 API 请求。
Spring AI 测试使用本地 HTTP 服务模拟响应，验证真实 SDK 请求、用量结算、500/断连接时无隐藏重试，以及完整 API/审批/账本核验流程。CI 不调用付费模型；显式真实服务验收见下。

## 本地启动

先启动 Docker，设置数据库口令及两个不同的账户口令（账户口令至少 16 个字符），再运行。数据库已有数据卷时必须复用原口令；环境变量不写入仓库：

```powershell
$env:PLATFORM_DATABASE_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host '审批数据库口令' -AsSecureString)).Password
$env:PLATFORM_OPERATOR_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'operator 口令' -AsSecureString)).Password
$env:PLATFORM_APPROVER_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'approver 口令' -AsSecureString)).Password
docker compose up -d --wait --wait-timeout 180
java -jar platform-api/target/platform-api-0.1.0-SNAPSHOT.jar
```

- API：`http://127.0.0.1:9090`；健康检查：`/actuator/health`。
- Temporal UI：`http://127.0.0.1:8233`；gRPC：`127.0.0.1:7233`。
- 审批 PostgreSQL：`127.0.0.1:5434`，独立数据库 `agent_platform`、独立卷 `approval-data`；Flyway 自动迁移业务表，不读写 Temporal 内部表。
- Temporal 的 PostgreSQL 无宿主机端口，其 Compose 固定口令仅供本机合成演示使用。
- `operator` 可创建/查询/取消及核对、关闭未知任务；`approver` 可查询/审批。用户名可通过 `platform.security.operator-username`、`platform.security.approver-username` 覆盖。未配置有效口令时启动失败，不启用默认账户口令。
- `docker compose down` 停止服务并保留命名卷；本项目启动脚本不自动删除数据库数据。
- 覆盖端口示例：`java -jar platform-api/target/platform-api-0.1.0-SNAPSHOT.jar --server.port=9092`。

## 提交并审批一个任务

```powershell
$operatorHeaders = @{
  Authorization = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("operator:$env:PLATFORM_OPERATOR_PASSWORD"))
  'X-Platform-Request' = 'true'
}
$approverHeaders = @{
  Authorization = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("approver:$env:PLATFORM_APPROVER_PASSWORD"))
  'X-Platform-Request' = 'true'
}
$body = @{
  requestId = 'demo-001'
  service = 'orders'
  approvalTimeoutSeconds = 300
} | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:9090/api/runs -Headers $operatorHeaders -ContentType application/json -Body $body
$state = Invoke-RestMethod -Uri "http://127.0.0.1:9090$($run.statusUrl)" -Headers $operatorHeaders
$state
# 等 state 为 WAITING_APPROVAL 后，提交该 Run 返回的 approvalId。
$decision = @{ approvalId = $state.approvalId; decision = 'APPROVE' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:9090$($run.statusUrl)/approval" -Headers $approverHeaders -ContentType application/json -Body $decision
Invoke-RestMethod -Uri "http://127.0.0.1:9090$($run.statusUrl)" -Headers $operatorHeaders
```

改为 `REJECT` 可拒绝；`POST /api/runs/{runId}/cancel` 请求取消。

| API | 结果 |
|---|---|
| `POST /api/runs` | 202；返回稳定 runId 与状态地址 |
| `GET /api/runs?limit=20&pageToken=` | Temporal Visibility 任务列表；返回 `runs`、`nextPageToken`，空 token 表示结束；列表可能有可见性延迟 |
| `GET /api/runs/{runId}` | 当前状态、合成证据、审批 ID、操作 ID、工具结果及 `agent` 规划进度/版本/核验观察/结论 |
| `GET /api/runs/{runId}/history` | 刷新并持久保存历史元数据，返回 Run、Activity Step、模型预算尝试；`refresh=false` 只读已保存的 Run/Step 投影 |
| `GET /api/runs/{runId}/events?after=&limit=100` | 持久事件分页；返回 `events`、`nextCursor`、`highWatermark`、`hasMore`、`closed` |
| `GET /api/runs/{runId}/events/stream` | SSE；支持 `Last-Event-ID` 或 `after`，恢复游标之后的历史元数据事件 |
| `GET /api/runs/{runId}/budget` | 固定限额、截止时间、已用/预留 token 与 cost、模型尝试次数、计量模式；未初始化或旧 Run 无预算时 404 |
| `GET /api/runs/{runId}/approval` | 原始绑定、指纹、过期时间、决定与审批/取消人 |
| `GET /api/runs/pending-approvals` | 最早到期的最多 100 个待审批记录，重复查询不创建新审批单 |
| `POST /api/runs/{runId}/approval` | 202 表示决定与投递意图已持久保存，最终状态通过 GET 确认 |
| `POST /api/runs/{runId}/cancel` | 202 表示取消已请求 |
| `GET /api/runs/{runId}/operation` | 持久执行状态、下游回执及当前测试服务 generation |
| `POST /api/runs/{runId}/reconciliation` | operator 提交 `{"action":"CHECK"}` 只读核对，或 `{"action":"CLOSE","reason":"核验说明"}` 人工关闭未知任务；202 表示已持久保存投递意图 |

非法输入为 400，未认证为 401，角色不允许或缺少写请求头为 403，未知 Run 为 404，重复 requestId、冲突/过期审批或不可取消操作为 409。`requestId` 最长 64 个字符，仅支持字母、数字、下划线和连字符；已完成任务的 ID 也不能复用。相同审批人重复提交同一有效决定返回 202；相反决定不能覆盖先前决定。

所有写请求发送 `X-Platform-Request: true`，服务不开放 CORS；启用浏览器前端时须重新设计 CSRF 边界。本机 Basic 账户只是当前身份集成，生产环境仍需 TLS、正式身份源和资源权限模型。

查询中的 `reasonCode` 表示最近一次工具治理决定的原因。策略拒绝为 `DENIED`，用户拒绝为 `REJECTED`。允许/拒绝/失败分支使用离线 fixture 验证；当前 API 的固定重启策略始终要求审批，不提供调用方切换策略的参数。

## 任务历史与事件续传

`/history` 和 `/events` 在请求时从 Temporal 读取完整历史，使用独立业务表保存可重建的元数据投影；不会执行 Activity、重跑模型或发送工作流消息。Run 创建、审批和工具执行不依赖这些投影表。查询表不可用时观测接口返回 503，原执行链路仍照常运行；这不表示共享业务数据库整体不可用时审批/预算仍可写入。

`run.executionStatus` 来自 Temporal，例如 `RUNNING`、`COMPLETED`；`businessState` 和 `reasonCode` 只从已返回的业务结果提取，运行中为 null，实时业务状态仍通过原 `/api/runs/{runId}` 查询。`COMPLETED` 不能直接当作业务成功。`projectedAt` 表示 Run/Step 最近一次历史投影时间；`refresh=false` 不访问 Temporal，但模型尝试仍从当前预算表读取，可能比缓存的 Run/Step 更新。

Step 对应一次 Temporal Activity 调度，以 `scheduledEventId` 和 `activityId` 关联历史。模型 Activity 另带稳定 `decisionId`。`lastRecordedAttempt` 只表示历史可见的最终尝试序号，未上报时为 null；[Temporal 不记录中间每次重试的独立历史事件](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/application-failures.mdx)。`modelAttempts` 单独来自预算表，`RESERVED/UNKNOWN` 的 null 用量不表示零消费，`meteringMode` 明确区分合成用量与配置费用估算。

事件 ID 为 `<Temporal executionId>:<eventId>`，按单个执行历史递增。断线客户端保存最后消费成功的 SSE `id`，重连时通过 `Last-Event-ID` 发送；重复传输由客户端按 ID 去重。SSE 每秒检查新历史，空闲发送 heartbeat，约 25 秒主动结束供客户端重连，终态且事件发完时提前结束。每个应用最多 32 条流，超出返回 429；它不是模型 token 流。浏览器原生 EventSource 不能自定义 Basic Authorization，请使用可设置请求头的客户端，不把凭据放进 URL。

列表和事件页 `limit` 范围 1–500。游标必须属于当前 execution，超前、格式错误或冲突的游标返回 400；执行绑定变化返回 409，不把新执行拼到旧流中。当前上限为每 Run 10000 个历史事件，超出返回 422，不截断推进游标；Continue-As-New 尚未支持。投影缺失/损坏时重新 GET 可在 Temporal 历史保留期内修复，事件 ID 不变。没有后台全量归档或自动清理，历史过期后不能从 Temporal 重建；已有 Run/Step 投影仍可用 `refresh=false` 读取。正式保留策略、Artifact、trace/metrics 和管理台在后续 P3 实现。

新增投影和 SSE 仅保存时间、状态、受控标识及关联关系，不复制 evidence、runbook、模型原文、异常正文、Worker identity 或凭据。原始 Temporal 历史、预算决策缓存及原审批/操作 API 的保留与权限边界未因此改变。

## 导出 mat-console 项目状态

`scripts/export-status.py` 用本机 operator 账户读取 `GET /api/runs` 与 `GET /api/runs/{runId}`，生成一份 [mat-console.status/1](https://github.com/mat973252/mat-console/blob/main/docs/protocol-v1.md) JSON 到本地文件（默认 `var/mat-console-status.json`，已被 git 忽略）。它只复制允许列出的枚举字段与时间，不导出证据、工具输出、模型原文、异常、身份或凭据，不新增未认证路由。只读导出不改变 Workflow/Activity 执行语义。

```bash
export PLATFORM_OPERATOR_PASSWORD=…   # 不要把口令写进命令行参数或 URL
python3 scripts/export-status.py --base-url http://127.0.0.1:9090
```

`health` 只描述本次可见的本地演示 Run 列表，不是生产就绪声明：只有抽样 Run 全部为来源可证的 `succeeded` 才是 `ok`，出现待处理信号为 `attention`，空列表、仅运行中、业务结果缺失或不可识别、以及混合结果都为 `unknown`；`progress` 与 `milestones` 在没有可信来源时不输出。run 的 `status` 由 Temporal `executionStatus` 与业务 `state` 分别决定，`COMPLETED` 不会未经业务结果就被当作 `succeeded`；`attention` 仅记录等待审批、待核验、人工关闭未知与失败的 Run。脚本对已认证请求禁用重定向跟随（urllib 会把 `Authorization` 复制到跳转后的请求），3xx 按安全失败处理，不打印响应正文或凭据。确定性测试为 `python3 scripts/export-status-test.py`，除本机回环重定向用例外不依赖运行中的服务。浏览器查看时由操作者自行用带 `Access-Control-Allow-Origin: *` 的本地静态服务器承载该文件，再交给 mat-console 的 `?url=` 适配；本文档不发布状态数据。

## 离线 Agent 循环

新 Run 读取合成证据与 `orders-runbook-v1`，由独立 `plan` Activity 返回严格的 [决策契约](platform-api/src/main/resources/agent/decision-v1.schema.json)。模型只能请求 `evidence.read`、`ops.restart`、`ops.verify`，参数必须精确为当前 orders 服务；`FINISH` 和 `NEED_CONTEXT` 不能携带工具。额外字段、重复 JSON 字段、尾随内容、未知工具、授权参数或其他资源均拒绝。

默认 fixture 依次请求一次受审批重启、只读账本核验、结束诊断。工具结果反馈给下一次规划；测试也覆盖补查证据失败、核验读取失败后的重新规划。最多 6 次模型决策，每次 Activity 最多尝试 3 次；重试沿用稳定 decision ID。每个 Run 仍只允许一次逻辑重启，未知写结果阻塞在原核验流程中。

`agent` 返回模型/提示词/工具/runbook 版本、模型步数、最近决策、核验观察、最终结论和固定预算上下文。已上报的模型结果由 Temporal 历史复用；预算数据库已确认的结果也可跨 Worker 复用。响应未落账就中断时仍可能再次调用，未知尝试保留预留额度，重试另行预留。

服务端配置 `platform.budget.max-model-steps`、`max-duration-seconds`、`max-tokens`、`max-cost-microusd` 默认分别为 6、900、100000、1000000；例如启动时追加 `--platform.budget.max-tokens=6000`。创建 Run 时冻结配置，HTTP 请求不能覆盖或提升额度，恢复时不重置。总时限包含审批等待和重试，耗尽后停止新自主步骤；已开始且结果未知的写入仍允许只读核验或人工关闭，不代表回滚。

`/budget` 明确返回 `OFFLINE_SIMULATED`。每个物理模型尝试预留 2560 token / 5000 microUSD，成功 fixture 结算 928 token / 1000 microUSD；完整三步共 2784 / 3000。这是合成测试用量，不能当作真实模型消费。未知请求占用不自动释放，额度不足在请求前以 `TOKEN_BUDGET_EXCEEDED` 或 `COST_BUDGET_EXCEEDED` 拒绝。真实供应商 usage、价格与输出上限在接入模型时单独验收。

写入后必须先通过 `ops.verify` 才允许 `FINISH`。核验只确认绑定的账本回执，不证明真实服务健康；模型宣称成功不能替代核验。任务在后续规划或核验失败时可能为 FAILED，但先前写入仍已完成，可用 `/operation` 查看；失败不等于回滚。

## 启用 Spring AI 模型

默认 `PLATFORM_MODEL_PROVIDER=OFFLINE`。启用远端时需显式配置以下环境变量，再按前面的本地启动步骤运行应用：

| 环境变量 | 含义 |
|---|---|
| `PLATFORM_MODEL_PROVIDER` | `OPENAI_COMPATIBLE` |
| `PLATFORM_MODEL_BASE_URL` | 完整 HTTPS API 根地址，通常以 `/v1` 结尾；不能含凭据或 query |
| `PLATFORM_MODEL_NAME` | 服务支持的模型标识 |
| `PLATFORM_MODEL_API_KEY` | 本机注入的密钥，不写入 Run 或数据库 |
| `PLATFORM_MODEL_PRICING_VERSION` | 本次参考价格的版本标识 |
| `PLATFORM_MODEL_INPUT_MICROUSD_PER_MILLION` | 每百万输入 token 的 microUSD 估算单价，如 3000000 表示 USD 3 |
| `PLATFORM_MODEL_OUTPUT_MICROUSD_PER_MILLION` | 每百万输出 token 的 microUSD 估算单价，如 15000000 表示 USD 15 |

密钥可使用前文同样的 PowerShell `Read-Host -AsSecureString` 方式设置；不得放进命令行参数或提交配置。输入/输出额度通过 `platform.model.max-input-tokens` / `max-output-tokens` 配置，默认 8192 / 512。模型、端点、prompt 与价格随 Run 固定，旧离线 Run 恢复时仍保持离线。更换凭据端点后不能用新端点偷偷接管旧 Run；需保留匹配配置才能继续未完成请求。

`/budget` 会返回 `PROVIDER_USAGE_CONFIGURED_ESTIMATE` 和冻结的模型 profile。usage 缺失、无效输出、模型请求被拒绝均有明确原因；没有实际响应用量时保留预留，不按零成本结算或回退 fixture。费用是配置估算，代理自身收费、缓存折扣和实际账单未接入；输出上限依赖兼容服务遵守 `max_tokens`。

显式真实服务验收可复用已有本地 dotenv 中的 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`，或直接从同名环境变量读取：

```powershell
python scripts/smoke-model.py --pricing-version reference-v1 --input-microusd-per-million 3000000 --output-microusd-per-million 15000000
```

可追加 `--env-file` 指向自己的本机凭据文件，文件不会复制进项目。必须按实际使用的参考价填写参数。该脚本显式发起真实模型请求，最多 4 个规划步骤、每步最多 3 次尝试、总时限 120 秒、估算费用上限 500000 microUSD；日志在忽略的 `var/model-live.log`。只使用合成诊断数据和测试账本，Temporal 为内存服务、业务库为 H2，不能计为 PostgreSQL 跨进程验收。

2026-09-09 已通过自建兼容服务的 `claude-sonnet-4-6` 实际调用验收：3 次模型请求，服务报告 2227 token，按输入 USD 3 / 输出 USD 15 每百万 token 的参考价估算为 9489 microUSD。该参考价来自 [上游模型公开价格](https://platform.claude.com/docs/en/models/sonnet-4-6/overview)，不是自建网关账单，也未独立验证其背后的模型路由。

## 跨进程恢复验收

需要运行中的 Compose、匹配的 `PLATFORM_DATABASE_PASSWORD` 和 Python 3 标准库。脚本使用临时账户口令启动 API（默认需要空闲端口 9091 和 9092），验证等待审批、审批 outbox 与数据库重启恢复，以及拒绝、取消、超时和重复请求。随后在测试账本已提交、Activity 尚未返回时强制结束 Worker，由另一个端口上的新 Worker 恢复，核对回执相同且 generation 只增加一次；最后验证无回执的未知任务跨重启保留，并由 operator 留痕关闭。结束时只停止脚本创建的应用进程。

```powershell
python scripts/smoke.py
# 同时重启本项目的 approval-db 容器，核验审批决定确实留在 PostgreSQL 卷中：
python scripts/smoke.py --restart-approval-db
```

日志写入忽略的 `var/smoke.log`。脚本也检查等待期间已记录的模型决策和预算跨 Worker 重启不变，以及恢复后三个模型步骤的结算。另在模型响应落账前强制终止进程，验证未知预留跨进程保留、新尝试单独计量。此验收与内存工作流测试分开，GitHub Actions 也会运行它。实际执行记录见 TODO。

P3 smoke 增加持久查询投影、真实 Visibility 列表分页和 SSE 游标补齐检查；`--restart-approval-db` 还会仅删除该次 smoke 自建 UUID Run 的观测投影，再从历史重建并比较事件 ID，不删除其审批、预算、执行记录或账本。

2026-09-06 已在 [GitHub CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34025622000) 通过 PostgreSQL + Temporal 的 Worker 强制终止/恢复验收，并验证拒绝、取消、超时和重复请求保护。本机 Windows 的 Docker 引擎启动故障使本地容器联调尚未完成；Windows 本地 Maven 测试已通过。

2026-09-07 的 [P1.1 CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34049953194) 已验证全新环境构建固定 AgentPermit 源码，并再次通过治理接入后的相同恢复验收。

2026-09-08 的 [P1.2 CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34192140667) 已通过 55 项项目测试，并实际验证审批决定保存后中断投递、重启 Worker 与审批 PostgreSQL、自动补投后继续同一 Run。上游 113 项测试也通过。

同日的 [P1.3 CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34238414365) 已通过 72 项项目测试及上游 113 项测试，并实际验证账本提交后终止 Worker、新进程在另一端口恢复同一结果且仅一次写入，以及未知任务跨重启后的人工留痕关闭。当前 P1.3 验收范围为受控数据库账本。

[P2 首批 CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34245518880) 已通过 94 项项目测试与上游 113 项测试，并验证已记录的离线规划决策跨 Worker 恢复、批准后完整完成规划/执行/核验循环，以及账本提交后中断仍只写一次。该次验收尚未包含预算；后续预算验收见 TODO。

2026-09-09 的 [预算 CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34250825483) 已通过 117 项项目测试和全部 PostgreSQL + Temporal smoke。模型响应后进程退出，恢复时原尝试保留预留额度，新尝试单独计量；审批数据库重启不重置预算或 deadline。当前计量仍是离线合成单位。

同日的 [Spring AI CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34293091866) 已通过 128 项项目测试和全部恢复 smoke，包括 Spring AI 实际 SDK 与本地 HTTP mock 的完整审批/核验流程；CI 没有调用付费模型。自建模型真实网络验收单独记录在上方。

同日的 [P3 CI](https://github.com/mat973252-coder/agent-platform/actions/runs/34299312533) 已通过 141 项项目测试与全部 PostgreSQL + Temporal smoke。查询投影跨 Worker/数据库重启保留；SSE 从 event 38 续传至 73，删除投影后重建保持相同事件 ID；真实 Visibility 列表分页和原有副作用/预算恢复检查均通过。

## 当前保证与限制

- Temporal 恢复已记录的执行结果；Activity 在完成结果尚未上报时仍可能重试。
- `operationId` 作为 AgentPermit 的幂等键，贯穿同一逻辑操作的重试；数据库唯一约束选出一个执行者，审批消费与执行权在同一事务提交。已确认结果可跨 Worker 读取；未确认执行不做超时接管。
- 测试账本在独立事务内递增 generation 并按 operation ID 保存唯一回执，返回 `TEST_LEDGER_RESTART:orders`。保证依赖业务数据库持久性和这个受控下游的去重契约，不代表任意外部服务具备 exactly-once。
- 无法确认的执行进入 `RECONCILIATION_REQUIRED`，只读取下游，不重做工具。`CLOSED_UNKNOWN` 保存关闭人及原因，表示人工结束核验，不证明无副作用，也不是失败确认、取消或回滚。迟到结果不能覆盖关闭状态。
- 审批单唯一绑定 Run、Step、操作 ID、工具、资源、规范化调用指纹及固定截止时间；重试不延长有效期。指纹还包含服务端主体、租户、环境及参数。
- 首个有效决定由条件 UPDATE 保存；审批消息只唤醒 Workflow，首次执行仍从数据库验证决定、审批人当前权限、指纹和过期时间。服务端关闭 `platform.policy.restart-enabled` 或更换审批人后，旧批准不能发起新执行；读取已有绑定的结果无需重新取得写权限。
- 审批记录中的决定与 `delivered` 标记构成事务性 outbox，投递为至少一次；失败自动重投、重复消息不增加本流程的逻辑执行。通知入口目前是持久审批列表，无邮件/IM 通知渠道。
- 取消在执行前的数据库 claim 边界之前获胜时撤销审批；执行已经开始时返回冲突，不能承诺回滚。取消另存操作人，保留原审批人记录。过期通过截止时间判定，数据库不靠定时任务把 `PENDING` 改写为 `EXPIRED`。
- `Workflow.getVersion` 保留升级前命令序列，12 份固定旧历史参与回放。未带预算的旧 Run 不插入预算步骤，P1.3 旧 Run 不插入模型步骤，P1.2 旧 Run 不自动获得新的持久副作用保证。P0/P1.1 尚未决定的 Run 没有可信审批记录，应取消并用新 requestId 重建。
- API、Temporal gRPC 与 UI 仅绑定 loopback；当前 Compose 是开发演示配置。
- 取消不会回滚已经完成的外部操作。
- 当前业务库保存审批、执行结果、预算、模型 profile 以及按需更新的历史元数据查询投影；未实现全量 Activity 尝试审计、Artifact 存储或观测平台。RAG、管理前端、Sandbox 和多 Runtime 在 TODO 分阶段列明。
- 原始平台规划描述最终愿景，不是当前能力清单。

## License

Apache-2.0，见 [LICENSE](LICENSE)。
