# Codex Governance Infrastructure v16

简体中文 · [English](README.md)

这是一套只面向 Codex 用户的 starter：在不牺牲正确性、证据和 GitHub 审批边界的前提下，加快
开发与审查。

V16 提供：

- 可持续的 Codex 全局规则与任务 brief；
- affected-first 测试，而不是动不动全量 rebuild；
- CodeGraph、Semble、`rtk` 的强制就绪检查与实际使用证据；
- 单一风险路由的独立 reviewer，以及同 reviewer 的 delta-only 复审；
- 原生 `~/.codex/hooks.json` 生命周期门与 privacy-safe receipts；
- 确定性 package verifier 和隔离试用安装器。

它不声称兼容 Claude Code、Kimi Code、Zcode 或其他 agent runtime。

> **安全边界**
>
> 安装器是 manifest-bound managed overlay：只替换 package 自己管理的路径，保留
> `CODEX_HOME` 中全部无关文件，并把旧 managed files 放到 `.governance-v16-backup` 供 rollback。
> 必须先看 dry-run。仓库不会复制 credential、session、memory、plugin、connection、model cache
> 或其他私人数据。

## 师兄师姐十分钟上手

### 1. Clone 并验证

```bash
git clone https://github.com/Qian9921/codex-governance-infra.git
cd codex-governance-infra

git rev-parse HEAD
git status --short
python3 scripts/verify-governance.py --repo .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tests/v16 -p 'test_*.py'
```

只有 verifier 输出 `"status":"GREEN"`，且测试为零 failure、零 error 时才继续。

### 2. 缺少工具时安装

安装软件前先审阅 upstream 指令：

```bash
# CodeGraph
npm install -g @colbymchenry/codegraph

# Semble
uv tool install semble

# rtk
cargo install --git https://github.com/rtk-ai/rtk
```

Upstream：

- [CodeGraph](https://github.com/colbymchenry/codegraph)
- [Semble](https://github.com/MinishLab/semble)
- [rtk](https://github.com/rtk-ai/rtk)

### 3. 配置 Codex

```bash
codegraph install --target codex --location global --yes
semble install --agent codex --type mcp --yes

rtk init --codex --global --dry-run
rtk init --codex --global
```

Semble 命令只安装 MCP，避免再注入一大段重复规则。审阅配置变化后，重启真正受影响的 Codex
CLI、Desktop 或 app-server，并开启新任务。

### 4. 检查当前仓库的 CodeGraph 索引

```bash
codegraph status --json .
```

仍可手动修复：

```bash
codegraph init .
```

```bash
codegraph sync .
```

索引只属于 owning repository，绝不能把父 workspace 的图当作 child repo 真值。下一步的
controller 可以只对这个 exact owning repo 自动执行一次 `init` 或 `sync`；该受限维护职责属于
当前 execution lane，与模型名称无关。

### 5. 自动检查并做有界维护

```bash
python3 codex/bin/toolchain-auto.py \
  --repo . \
  --semantic-query "deterministic inspection intent router" \
  --expected-path codex/v16/tool_routing.py
```

唯一通过条件是退出码 `0`、`"status":"ready"`、分母 `3/3`：

- CodeGraph 已配置、绑定当前 repo、索引完整且 fresh，并能找到预期当前源码；
- Semble 已配置、可调用、repo scope 正确，语义 query 能返回预期源码；
- `rtk` 能复现当前 Git identity，并保持确定性失败命令的非零退出码。

binary 存在不等于就绪。controller 先运行只读 doctor；若 CodeGraph index 可安全修复，它获取
private single-flight lock，只对 exact repo 执行一次 `init|sync`，然后重新检查。它不会安装包、
修改用户 config、清理全局 Semble cache、使用 sudo 或重复无进展修复。完全只读时加
`--check-only`。

### 6. 在隔离目录试用治理包

```bash
GOV_TRIAL_ROOT="$(mktemp -d)"
GOV_TRIAL_HOME="$GOV_TRIAL_ROOT/.codex"

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --dry-run

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME"

CODEX_HOOK_SOURCE=test \
CODEX_HOOK_RECEIPT_DIR="$GOV_TRIAL_ROOT/receipts" \
python3 "$GOV_TRIAL_HOME/hooks/session_context.py" <<'JSON'
{"hook_event_name":"SessionStart","model":"trial"}
JSON
```

预期：

- dry-run 给出 managed file denominator；
- 所有安装文件只在隔离目录；
- hook 输出符合原生 `hookSpecificOutput` 协议，且指定的 test receipt 目录中生成 private receipt；
- live Codex home 没有被修改。

Rollback：

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$GOV_TRIAL_HOME" \
  --rollback
```

只有之前目标目录确实被备份时才能 rollback。

### 7. 安装到正在使用的 Codex home

隔离试用全绿后：

```bash
ACTIVE_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME" \
  --dry-run

python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME"
```

Overlay 会保留 `config.toml`、credential、plugin、memory、session、connection、cache、receipt
以及其他全部 unmanaged path。确认 `[features] hooks = true`，重启真正受影响的 Codex surface，
再用 `/hooks` 审阅并 trust 新的 exact hook hash。需要恢复旧 managed files 时加 `--rollback`。

### 8. 给 Codex 复制这一段

```text
阅读这个仓库的 README 和 AGENTS.md。验证 package；检查当前
CodeGraph/Semble/rtk 配置；选择仓库专属 semantic sentinel，运行
toolchain-auto.py，允许其只对 exact repo index 做一次有界维护。编译完整
tool-task-contract.v16：需要未知语义入口时必须用 Semble，需要结构/影响时
必须用 CodeGraph，shell context 用 rtk，精确文本用 rg。最终必须有
receipt-backed 实际使用和 tool-enforcement.v16 completion_eligible=true；
不要打卡，也不要对同一故障循环。
```

师兄师姐只需完成 clone、审阅授权，剩余步骤可以交给 Codex 驱动，同时所有 mutation 和失败都
保持可见。

## “强制工具”到底是什么意思

它包含四个合同和一个 reliability plane。

### Gate 1：就绪

`tool-preflight.v16` 绑定当前 host/runtime、工具版本、Codex config、repo root、Git head、
worktree、CodeGraph index 和 semantic sentinel。任一 identity 变化都使旧 receipt 失效。

### Gate 2：完整任务适用性

`tool-task-contract.v16` 确定性覆盖四类 route：
`semantic_discovery|structural_analysis|exact_lookup|shell_context`，每行必须是
`required|not_applicable`，不允许漏声明。

### Gate 3：实际使用

`tool-usage.v16` 把每条 declared route 绑定到成功且与任务相关的调用、evidence reference 和
privacy-safe hook receipt hash。

### Gate 4：完成强制

`tool-enforcement.v16` 要求每个 required preferred route 都有成功且 task-relevant 的实际调用；
只有 `completion_eligible=true` 才能支持完成声明。

原生 `UserPromptSubmit` hook 先建立只含 hash 的 turn intake，并把一次性 task-contract recorder
所需的精确 hash 注入给 Codex。Recorder 必须显式二选一：仓库源码/读取/写入任务使用
`--repository-work`；完全不读写仓库的 plugin/model/用户配置/service/机器盘点使用
`--non-repository-task`。只有仓库 scope 才要求 CodeGraph/Semble/rtk 严格就绪；非仓库 scope
不得声明仓库路由，也不能在后续 scope 扩大后继续复用。
非仓库工具调用必须从所有 Git 仓库之外的 cwd 执行；仓库内 activity、显式仓库目标和仓库专用工具
都会被拒绝，直到新建并绑定 repository-scoped intake。`CODEX_HOME` 下的已安装状态仍属于机器 scope。
不可变完整 contract 建立前，
`PreToolUse` 会拒绝仓库工具，并记录
每个预期 call id。`PostToolUse` 只接受明确、受支持的成功结构；`Stop` 要求每个预期调用都有同一
current hook snapshot 下的 receipt，并要求每条必需路由至少有一次成功。普通失败会保留为诊断，
但同 intake、同 snapshot、同路由后续成功即可关闭它；身份、receipt、snapshot 完整性失败仍然硬阻断。
缺证据只续跑一次，随后由 `stop_hook_active` 打开 circuit，
不再死循环。Assistant 自己写的 marker 不具备裁决权。Hook 变化后须用 `/hooks` 审阅并 trust 新 hash。

### Reliability plane

`tool-maintenance.v16` 自动检查 3/3，只对 exact owning-repo CodeGraph index 修一次并重新检查，
同时持久化 failure fingerprint；下一次遇到完全相同且未变化的失败时直接打开 circuit，不再修。
普通 stale index 不是 `EXEC_INFRA_BLOCKED`。

| 任务意图 | 强制路由 |
|---|---|
| 未知语义入口或相似实现 | Semble |
| 已知 symbol、call、dependency 或 blast radius | CodeGraph |
| 展示给模型的 shell output | `rtk` |
| 精确 string、error、config 或 log | `rg`/有界精确读取 |
| hash、parser input、byte identity、精确 denominator | raw command |

无关地把每个工具调用一次属于违规。只有 preferred tool 真实失败，并留下 reason code 和
evidence reference 后才能 fallback；fallback 不得冒充等价语义或结构覆盖。

详细合同与修复方法：
[强制工具链详细说明](docs/TOOLCHAIN.md)；本页保留中文快速上手流程。

## 开发和审查模型

1. 冻结 objective、scope、invariants、non-goals、exact identity 和 evidence budget。
2. 只运行具备具体 WHY-RED 与已知 denominator 的 affected checks。
3. 每个任务只有一个 independent reviewer：
   - 低/中风险：Terra high；
   - 高风险：fresh Sol xhigh。
4. 稳定修复由同一 reviewer 做 delta-only 闭环。
5. coverage complete、unreviewed scope 为空、无 active P1/`BLOCKING`、evidence 与 exact head
   匹配时才 approve。
6. 使用 expected-head/match-head guard 合并。

正确性与证据是硬门。第一优化目标是得到正确判断或正确合并的时间，token/call cost 排第二。

## 完整验证

开发中先跑最小 affected checks。冻结 clean candidate 后运行：

```bash
git status --short
python3 scripts/presubmit.py --repo .
git diff --check
```

manifest 是精确 tracked path/hash 边界。新增、删除或修改 tracked 文件后必须同步 manifest。

## 隐私与限制

绝不能提交：

- API/GitHub token、OAuth state、cookie 或 credential；
- Codex session、prompt、transcript、memory 或 receipt；
- plugin/connection/model cache 或 browser profile；
- 个人绝对路径或私有仓库内容。

本包不能授予当前 Codex surface 没有暴露的模型或工具。CLI、Desktop、远程 host 和 app-server
可能在不同生命周期刷新。治理、MCP、hook 或 model-routing 变化后，重启对应 surface 并开启新任务。

详见 [SECURITY.md](SECURITY.md)、[PRIVACY.md](PRIVACY.md) 和
[privacy threat model](docs/privacy-threat-model.md)。

## 故障排查

| 现象 | 处理 |
|---|---|
| `CODEGRAPH_WRONG_PROJECT` | 停止，把 doctor/query 指向 owning child repo。 |
| `CODEGRAPH_STALE` | `toolchain-auto.py` 只同步 exact repo 一次并重新检查。 |
| `AUTO_REPAIR_NO_PROGRESS` | circuit 打开为 `MAINTENANCE_REQUIRED`，不得重复 spawn/retry。 |
| `AUTO_REPAIR_CIRCUIT_OPEN` | 同一未变化故障已经用完一次 repair；修复指定底层状态后再试。 |
| `EXTERNAL_TOOL_REPAIR_REQUIRED` | package/config/system owner 处理；这不是模型执行 infra 故障。 |
| `SEMBLE_MCP_NOT_CONFIGURED` | 运行已审阅的 Semble MCP 配置命令并重启 Codex。 |
| `SEMBLE_SENTINEL_SCOPE_ONLY` | Semble 返回了当前 repo 的活源码，但预期文件排名较低；readiness 继续通过，任务相关的 Semble 实际使用仍然必需。 |
| `SEMBLE_SENTINEL_MISMATCH` | 没有返回可用的当前 repo 活源码；改善 query 或修复工具/repo scope。 |
| `RTK_FALSE_GREEN` | 硬停止；修复前不得接受 shell evidence。 |
| `receipt_status=write_failed` | 修复私有 receipt 目录；runtime-proof acceptance 被阻塞。 |
| `Unknown model ...` | 检查精确 host/surface catalog；本仓库不能授予模型权限。 |
| Manifest verifier 为 RED | 停止，逐项检查，经审查更新后重跑。 |

## 仓库结构

```text
codex/                     可安装治理包
  AGENTS.md
  BRIEF-TEMPLATES.md
  hooks.json               原生 Codex 生命周期配置
  hooks/
  v16/
docs/TOOLCHAIN.md          工具就绪与路由详细合同
scripts/toolchain-doctor.py
codex/bin/toolchain-auto.py
scripts/install-governance.py
scripts/verify-governance.py
scripts/presubmit.py
tests/
manifest.json              精确 tracked path/hash 边界
```

## Codex 官方资料

- [Codex CLI](https://developers.openai.com/codex/cli)
- [配置基础](https://developers.openai.com/codex/config-basic)
- [配置参考](https://developers.openai.com/codex/config-reference)
- [AGENTS.md 与定制](https://developers.openai.com/codex/concepts/customization)
- [Codex Hooks](https://learn.chatgpt.com/docs/hooks)
