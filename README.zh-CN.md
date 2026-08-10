# Codex Governance Infra V19

简体中文 · [English](README.md)

这是一套只面向 Codex 的轻量 Infra，服务既做研究又做开发、以结果为导向的科研工程人员。目标是更快完成正确实现，同时留下可信证据、可收敛审查、干净代码和可复用知识，而不是把每个任务都变成发布仪式。

## 它做什么

```text
目标 → 复用扫描 → Luna 执行（可选有界 Terra bridge）→ affected 证据
     → Sol 一次审查 → 已配置 author/reviewer PR 留痕 → 知识沉淀
```

- **个人级渐进式 Infra：** 有预算的常驻 kernel、按需 Skill、单一职责 Subagent、
  精简 Hook、命令 Rules 和显式 strict profile。
- **自适应档位：** `QUICK`、`STANDARD`、显式启用的 `STRICT`。
- **代码健康：** 项目规范优先，Google 官方规范作为默认基线；新增重要抽象前做 `REUSE|EXTEND|NEW` 决策。
- **按意图用工具：** Semble 找未知语义和相似实现，CodeGraph 查已知结构和影响，`rg` 查精确事实，`rtk` 展示 shell context。
- **大代码证据契约：** `code-mission-tool-index-policy.v1` 绑定精确的仓库、
  worktree、revision 身份和匹配的 Semble / CodeGraph 健康证据；开发前先做
  Semble，`CANDIDATE_READY` 前必须有 CodeGraph，只有纯非代码或精确机械任务
  能以说明标记 `N/A`，且不设置每轮或调用次数配额。
- **单调收敛审查：** 一个独立 reviewer；普通修复只做 delta closure。
- **安全安装：** 对个人 `.codex` 与 `.agents` 两个根执行 manifest 白名单、dry-run、
  原子安装、备份、哈希验证和 rollback。

本包只安装个人级配置。详见[个人 Infra 与上下文预算](docs/personal-infra.md)。

本仓库不宣称兼容 Claude Code、Kimi Code、Zcode 或其他 Agent runtime。

## 三个档位

| 档位 | 场景 | 证据和审查 | Hook |
|---|---|---|---|
| `QUICK` | 解释、盘点、文档、可逆机械修改 | targeted；正式 review 可选 | advisory |
| `STANDARD` | 普通研究工程和开发 | affected-first；一次独立 review | advisory |
| `STRICT` | 安全/隐私、精确数学、公共合同、不可逆变更、生产发布 | 保留的 V16 FAST/CANDIDATE/FINAL | fail-closed integrity |

默认是 adaptive。需要严格 Hook 时，在启动对应 Codex surface 前设置：

```bash
export CODEX_GOVERNANCE_MODE=strict
```

严格模式是显式选择，不再自动惩罚所有仓库任务。
V19 是公共 adaptive 产品规范；`codex/v16` 只作为向后兼容的严格兼容引擎保留。
普通、可逆的 installer、hook 和模型路由修复都走 `STANDARD`。

## 模型分工

- Luna 默认负责生命周期控制、执行、恢复、Git/CI 和证据。`R0`/`R1` 稳定任务
  留在 Luna 执行环，不强制插入 Sol 内循环。
- `R2`/`R3` 数学、数值、公共 API 和新算法先经过一次简短 Sol 合同门，再交回
  Luna 执行。`R4` 研究解释在解释有实质影响时由 Sol 主导。
- Sol 做 fresh、只读的最终审查。高风险审查必须看源码、合同和测试，并构造
  一个源代码推导的反例；稳定修复回到同一 reviewer 做 delta-only，最多两轮。
- Spark 仍保留在模型目录中，供旧合同或显式选择使用；当前角色策略将其禁用，默认
  流程绝不路由到 Spark。
- Terra：仅通过显式、短生命周期的 `TERRA_REPLAN`/`TERRA_TRIAGE` bridge 做有界
  R0/R1 advisory 综合/分流，并直接把控制权交回 Luna；Luna 不可用时才走独立的
  continuity fallback。Bridge 不能 review、merge、spawn、长监听、retry 或给出最终 verdict。

每个 spawned task 的名称都应暴露实际模型族和 role（如
`luna-execution-*`、`spark-audit-*`）。Fallback 名称必须暴露实际 fallback
模型族；Sol/Terra fallback 绝不能保留 `luna-` 前缀。Receipt 和报告记录
`requested_model`、`actual_model`、`role`、`fallback_reason`；除非故意伪造模型
身份，否则命名/telemetry 只产生 advisory 提示。

Sol 审计恢复证据。这些是路由默认值，不是能力封禁。

只有确有价值时才嵌套子代理：Sol 可以让 Luna 做有界的机械提取/构建/测试/日志
工作；Luna 可以就一个狭窄的数学、符号、shape 或无法解释的数值问题咨询 Sol。
子任务 scope 只能缩小，控制器以下最多两层；禁止 Luna↔Sol 来回乒乓和重复咨询
同一 uncertainty。作者链中的 Sol consultant 不能担任最终 reviewer。可执行策略见
`codex/hooks/model_roles.py`；`QUICK`/`STANDARD` 的 hook 默认仍是 advisory，只有
身份、租约、安全和隐私违规才阻塞。

## 自愈式执行

Luna 对必需能力采用彼此不同、能产生证据的恢复策略，直到能力可用，且已由
**真实的依赖任务切片实际使用**。一个稳定的无进展策略会打开 circuit，绝不
重复；恢复任务继续采用实质不同且安全的策略。可选能力失败可以降级无关工作，
但会留下有 owner 的 repair debt。必需能力失败只阻塞依赖它的 claim 或切片。

`HEALTHY`、`RECOVERING`、`DEGRADED`、`EXTERNAL_WAIT`、
`USER_ACTION_REQUIRED` 和 `UNRECOVERABLE` 的语义见
[工具链约定](docs/TOOLCHAIN.md#self-healing-capability-recovery)。
只有整张允许的 recovery graph 已被证据证明穷尽时才能称为 `UNRECOVERABLE`；一个
策略或 controller budget 结束并不够。`EXTERNAL_WAIT` 对真正的外部依赖做
bounded-backoff recheck。
只有科学/产品选择、credential 或 license、不可逆/共享状态操作、未获批准的实质成本、隐私，或真正的
外部不可能性才需要用户介入。check-only/no-mutation 结果不是用户 action；常规机器
修复仍由 Luna 执行。`QUICK`、`STANDARD` 仍为 advisory；V16 receipt 和
fail-closed gate 仍仅在显式 `STRICT` 时启用。

## 十分钟安装

### 1. Clone 并验证

```bash
git clone https://github.com/your-org/codex-governance-infra.git
cd codex-governance-infra

python3 -m pip install --user -r requirements.txt
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

只有 verifier 为 `GREEN` 且测试零失败、零错误时继续。

### 2. 缺少时安装并配置三个首选工具

```bash
npm install -g @colbymchenry/codegraph
uv tool install semble
cargo install --git https://github.com/rtk-ai/rtk

codegraph install --target codex --location global --yes
semble install --agent codex --type mcp --yes
rtk init --codex --global --dry-run
rtk init --codex --global
```

上游项目：[CodeGraph](https://github.com/colbymchenry/codegraph)、[Semble](https://github.com/MinishLab/semble)、[rtk](https://github.com/rtk-ai/rtk)。

### 3. Dry-run managed overlay

```bash
ACTIVE_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --dry-run
```

Overlay 只拥有选定个人 `.codex` 根中 manifest 列出的路径，以及相邻
`.agents/skills` 下的三个 V19 Skill。现有 config、credential、plugin、memory、
session、connection、cache、receipt 和其他无关文件全部保留。

### 4. 配置角色、账号和 Agent surface

公共默认值是占位符。生成 review packet 前配置两个不同的账号标签；它们只写入
metadata，绝不能包含 token：

```bash
export CODEX_GOV_AUTHOR_ACCOUNT="your-developer-account"
export CODEX_GOV_REVIEWER_ACCOUNT="your-reviewer-account"
```

Codex CLI/Desktop 使用本仓库的 hook 文件。若使用其他 Agent runtime，请复用文档中的
策略概念并运行 verifier，但不要直接复制 Codex hook overlay；本包不宣称原生兼容
Claude Code 或其他 Agent。本仓提供的 strict profile 是可移植配置，不包含 provider、
credential 或机器专属设置。

Luna 默认负责执行和恢复；Sol 为 R2/R3 提供简短 contract gate 并做独立 review；Terra
bridge 是显式、有界、R0/R1 的 advisory handoff 并返回 Luna，只有 Luna 确实不可用时
才用 continuity fallback；Spark 默认禁用。未知语义走 Semble，已知结构/影响
走 CodeGraph，精确文本走 `rg`，shell context 走 `rtk`。

### 5. 验证 hook 并运行第一个任务

```bash
python3 scripts/toolchain-doctor.py --repo .
python3 scripts/verify-governance.py --repo .
export CODEX_GOVERNANCE_MODE=adaptive   # 仅在明确需要时使用 strict
```

先用 QUICK 做解释或 STANDARD 做实现；安全/隐私、公共合同、不可逆、生产发布或精确
parity 才使用 STRICT。安装失败时保留 dry-run 输出，修复报告的前置条件后重新验证；
下面的 rollback 只作用于 managed overlay。

### 6. 安装

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME"
```

### 7. 配置原生 multi-agent 的 Luna/Spark 路由

Codex 普通目录可能显示 Luna/Spark，但上游 `multi_agent_version` 元数据仍可能不让它们
进入原生 V2 `spawn_agent`。路由脚本刷新私有模型目录、加入顶层
`model_catalog_json` 设置，并可选地安装 Linux user-systemd `ExecStartPre` drop-in。
脚本不会宣称模型一定可用：必须检查当前目录，再在真实 native spawn 面上执行验证。
刷新器使用隔离的临时 Codex home；POSIX 主机使用 `auth.json` symlink，Windows 因为未授权
symlink 常常不可用而临时复制到该目录。临时目录会被删除，绝不打印认证内容。

配置前分别记录客户端版本和 live catalog。Linux/macOS：

```bash
codex --version
codex debug models
```

Windows PowerShell：

```powershell
$ACTIVE_CODEX_BIN = (Get-Command codex -ErrorAction Stop).Source
& $ACTIVE_CODEX_BIN --version
& $ACTIVE_CODEX_BIN debug models
```

按需模式可跨平台使用，不会创建 launchd、Windows Task Scheduler 或其他启动文件。macOS、
Windows 11、没有 app-server 服务的 Linux，以及希望手动重启客户端时，都省略
`--systemd-user-dir`：

```bash
python3 scripts/configure-model-routing.py \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --codex-bin "$(command -v codex)"
```

Windows PowerShell 先发现已安装的 binary，不假设固定安装目录：

```powershell
$ACTIVE_CODEX_HOME = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$ACTIVE_CODEX_BIN = (Get-Command codex -ErrorAction Stop).Source
python scripts/configure-model-routing.py `
  --codex-home $ACTIVE_CODEX_HOME `
  --codex-bin $ACTIVE_CODEX_BIN
```

Linux user-systemd 是可选项。只有 app-server 确实由 user-systemd 管理时才传目录；脚本
本身不会调用 `systemctl`：

```bash
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
python3 scripts/configure-model-routing.py \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --codex-bin "$(command -v codex)" \
  --systemd-user-dir "$USER_SYSTEMD_DIR"
systemctl --user daemon-reload
systemctl --user restart codex-app-server.service
```

没有 systemd 时，配置完成后完整退出并重启受影响的 Codex CLI/Desktop/app server。脚本
不会重启任何进程。若 app-server 需要网络 wrapper，将 `EXEC_WRAPPER` 设为仓库内的
executable，并增加 `--exec-wrapper "$EXEC_WRAPPER"`（仅 Linux systemd 模式）。

验证分三层：确认当前模型目录暴露 Luna；确认生成并选用了
`model-catalogs/multi-agent-v2.json`；然后使用已安装的 `luna_execution` agent type 执行
真实 native `spawn_agent`。其 role file 已固定 `gpt-5.6-luna`，当前 collaboration schema
使用 `agent_type`、`task_name` 和 `message`：

```text
spawn_agent(
  agent_type="luna_execution",
  task_name="routing_smoke_check",
  message="Run the bounded routing smoke check and return one exact status line."
)
```

当前目录或 native surface 仍可能拒绝 Luna；此时应记录 capability limitation，不能用
Sol 或 Terra 静默替代。
平台 filesystem 测试是在当前主机模拟 Linux、macOS 和 Windows 分支；本次验证没有在原生
Windows 主机执行。
手动重启客户端，或完成 Linux systemd daemon reload 和 service restart 后，再执行同一组
版本及 live-catalog 命令。版本匹配只证明重启后的客户端；`debug models` 变化只证明 live
catalog；只有生成的 overlay 和真实 native spawn 才能证明路由已生效。

模型路由 rollback 必须使用与配置相同的平台模式。按需模式省略
`--systemd-user-dir`；Linux systemd 模式传入同一个目录：

```bash
python3 scripts/configure-model-routing.py \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --codex-bin "$(command -v codex)" \
  --rollback
```

Rollback 会分别 checkpoint config、drop-in 和 catalog，并在三个 target 都验证恢复前保留
state。若本地 filesystem fault 中断 rollback，重新执行相同命令即可安全继续。

确认 `[features] hooks = true`，重启真正受影响的 Codex CLI、Desktop 或 app server，然后在 `/hooks` 信任新的精确 Hook hash。

Rollback：

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --rollback
```

## 日常使用

只需告诉 Codex 你要的结果。全局规范会：

1. 选择任务切片和档位；
2. 新建抽象前先发现已有 owner；
3. 路由执行模型和真正相关的工具；
4. 运行 affected 证据；功能同时覆盖合成与真实域时，加入代表性小样本；
5. 做一次独立审查和 delta-only 闭环；
6. 留下双账号 PR 记录和可复用知识。

工具不是打卡表。依赖 CodeGraph 或 Semble 的答案前先验证其仓库身份。有界 V16
controller 可以只对 exact owning repo 修复一次；该 circuit 只是一个恢复策略，
不是 Luna 证据化恢复任务的终点。可选工具损坏时报告覆盖降级和 repair debt；只有
依赖它的 claim 才会被阻塞。

## 代码规范

仓库架构和 formatter/linter/compiler 配置优先。适用的 Google 官方语言规范作为默认基线。新增重要抽象前必须选择 `REUSE`、`EXTEND` 或 `NEW`。优先组合，避免平行框架、猜测未来的泛化层和 God Class。

详见[代码健康与 Google 基线](docs/code-health.md)。

## GitHub 职责

- your-developer-account 负责开发、push、开 PR，并逐条回应 finding。
- your-reviewer-account 独立评论、审查 exact head、approve，并用 expected-head 保护 merge。

PR 保留目标、证据摘要、finding、disposition、限制和最终 verdict；不保存 prompt、session、credential、私有路径或私有数据。

## 验证

开发中先运行最小 affected tests。Review 前运行：

```bash
python3 scripts/verify-governance.py --repo .
python3 scripts/presubmit.py --repo .
git diff --check
```

Tracked 文件新增、删除或修改后更新 manifest：

```bash
python3 scripts/update-manifest.py
```

## 文档

- [架构](docs/architecture.md)
- [代码健康与 Google 基线](docs/code-health.md)
- [审查流程](docs/review-workflow.md)
- [工具链细节](docs/TOOLCHAIN.md)
- [部署](docs/deployment.md)
- [隐私威胁模型](docs/privacy-threat-model.md)

## 隐私边界

绝不提交 credential、认证状态、Codex session、prompt、transcript、memory、receipt、plugin/connection/model cache、私有路径或私有仓库/数据内容。详见 [SECURITY.md](SECURITY.md) 和 [PRIVACY.md](PRIVACY.md)。
