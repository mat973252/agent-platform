# AgentPermit 0.5 兼容升级

2026-10-04，Asia/Shanghai。候选分支 `mat/p4-permit05-compatibility`，基线 `664e2886184773d49f952a682d166a658651f071`。

升级锁定依赖、Maven group 和 Java imports；平台领域数据、Workflow、数据库 schema 与操作 ID 不变。仍按固定公开源码构建，不把本地安装进缓存称为公共 Maven 安装。

## 可复现检查

```powershell
python scripts/bootstrap-agentpermit.py
.\mvnw.cmd -B -ntp verify
git diff --check
```

- 旧版 commit `c33911c595e5718b144d2bdb939bb3e23e17c909` 的 0.2.0 指纹先被实际提取，再固定为 `AgentPermitCompatibilityTest.LEGACY_FINGERPRINT`；3 项测试在升级前通过。
- 新版 v0.5.0 tag 对应 `9e100cd2bef02c46d18b1ed430e240d751c9b1ac`；下载 archive SHA-256 为 `b971e90aa3320450a9e57c932e48a1e43b230fb2c78457a75affe60e862a0b4e`，与锁定值一致。
- 升级后完整 Maven verify 通过：144 项主项目测试和 113 项依赖测试。覆盖相同旧指纹、按旧指纹重建的审批执行、变参拒绝、按旧指纹重建的回执核对与原有 12 份固定 Temporal 历史回放。测试中的账本 generation 保持 1。
- 原始日志位于忽略目录 `var/p4-fingerprint-capture.log`、`var/p4-02-compatibility.log`、`var/p4-bootstrap05.log`、`var/p4-05-verify.log`。capture 中有意使用占位预期值获取旧版实际指纹，不能称为发现产品缺陷。

上述单元测试是旧版固定调用形状的指纹兼容和内存 Temporal 回放证据；H2 审批/回执由当前代码配合固定旧指纹重建。它不证明任意调用形状兼容，也不是生产升级认证。

## 隔离跨版本进程恢复

同日新增 `scripts/upgrade-smoke.py`。从基线 commit `664e2886184773d49f952a682d166a658651f071` 执行 git archive，解包到忽略目录 `var/compat-old-source` 并以 `-DskipTests package` 构建旧应用。旧包仅做打包，不把这次命令写成完整测试。新包来自前述 verify。脚本确认两份 JAR 内分别有 core 0.2.0 / 0.5.0，并记录 JAR SHA-256；这不构成可复现构建或完整供应链认证。

使用随机 Compose project 与新建卷启动 PostgreSQL 16、Temporal 1.31.0。凭据只通过临时环境传入。固定本地端口 5434/7233、应用 9095/9096 必须空闲；不覆盖既有数据库。验收进程结束后移除本次项目的容器、卷与网络。

验证路径：

1. 旧应用创建并持久化待审批 Run，强杀旧进程；新应用读取同一 Run，完整比较审批与预算，批准后再次重启并完成，重复审批不增加账本 generation。
2. 旧应用在演示账本提交后暂停，确认 receipt 已存在且 execution 仍 IN_PROGRESS；强杀并切换新应用，恢复后 receipt 保持相同、最终输出相符、generation 只增加一次，重复审批仍不重复写入。

证据为 `var/p4-old-package.log`、`var/p4-upgrade-check.log`、`var/p4-upgrade-application.log`、`var/p4-upgrade-stack.log` 和 `var/p4-upgrade-result.json`。报告记录本次 attempt、时间、脚本 hash、Compose project、JAR hash 与各 case 结果。脚本启动即覆盖旧结果为 RUNNING，失败改为 FAILED；强杀可能留下 RUNNING，不能当 PASS。独立审查发现旧报告陈旧成功风险后已修复，并以不存在的 JAR 验证旧 PASS 被失败记录替换。只有本次命令 exit 0 且本次 JSON 为 PASS 才接受结果；stack 尚未启动脚本时不能依赖上次 JSON。

第一次 schema 初始化 exit 1（`var/p4-upgrade-stack-attempt1.log`），重建后成功，首次根因未取证，不能宣称启动偶发失败已修复。

该检查使用真实 PostgreSQL/Temporal 和旧进程留下的数据，但模型仍 OFFLINE、操作仍为演示账本。仅覆盖上述两种跨版本状态，未覆盖其他状态组合、并发滚动升级、数据库故障或真实外部副作用；不代表生产升级认证。

## PostgreSQL TCP 就绪检查

后续受控实验 `python scripts/check-postgres-readiness.py` 在全新 PostgreSQL 16 容器中延长初始化脚本阶段15秒，实际观测临时服务器的 socket `pg_isready` 返回0，但 `-h 127.0.0.1` TCP探测返回2；初始化完成后两者都返回0。由此确认旧socket检查与下游TCP连接就绪语义不一致。

`compose.yml` 中 approval-db 与 postgresql 两处健康检查改为显式 `-h 127.0.0.1`，并指定对应数据库。独立审查核对了实验与配置差异；修改后新建完整栈，两场景跨版本恢复再次通过。实验不直接复现首次schema退出1的具体错误，不能将该次历史失败归因定案。

实验/复核日志 `var/p4-readiness-check.log`、`var/p4-readiness-result.json`、`var/p4-upgrade-check.log`。初始化实验使用无网络容器、未暴露端口，结束后清理自身容器与匿名卷。

## CI 回归接入（2026-10-04）

`.github/workflows/verify.yml` 在既有 smoke 后签出固定旧提交到 `var/compat-old-source`，使用其锁文件和独立 Maven 仓库构建 0.2 应用，再运行现有两场景升级检查。结果 JSON 和应用日志通过 always artifact 步骤保留，失败仍阻止工作流通过；job 时限扩至30分钟。未改线上部署、模型模式或工作流领域行为。

本地 YAML 解析与差异检查通过；重新用隔离 PostgreSQL/Temporal 执行相同升级脚本，attempt `1884ce29-fce7-4a5c-99b2-2f0970640cec` 于 2026-10-04 04:26:00～04:26:57 UTC 两场景 PASS，进程退出0，隔离容器已清理。旧/新JAR的SHA记在该次报告。只读独立审查确认嵌套checkout、锁定版本、路径和失败语义无阻断。

工作流尚未推送或在 GitHub Actions 执行；本地同脚本成功不等于远端 CI 已通过。后续在候选提交上取得 Actions 结果后，才能将此门槛记为远端验收。
