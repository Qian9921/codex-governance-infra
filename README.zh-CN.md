# Codex 自适应治理 Infra

简体中文 · [English](README.md)

这是一套只面向 Codex 的轻量 Infra，服务既做研究又做开发、以结果为导向的科研工程人员。目标是更快完成正确实现，同时留下可信证据、可收敛审查、干净代码和可复用知识，而不是把每个任务都变成发布仪式。

## 它做什么

```text
目标 → 复用扫描 → Luna 执行 → affected 证据
     → Sol 一次审查 → Qian/Liang PR 留痕 → 知识沉淀
```

- **轻量全局规范：** 目标、角色、相关工具、代码健康、证据、审查、隐私和结束。
- **自适应档位：** `QUICK`、`STANDARD`、显式启用的 `STRICT`。
- **代码健康：** 项目规范优先，Google 官方规范作为默认基线；新增重要抽象前做 `REUSE|EXTEND|NEW` 决策。
- **按意图用工具：** Semble 找未知语义和相似实现，CodeGraph 查已知结构和影响，`rg` 查精确事实，`rtk` 展示 shell context。
- **单调收敛审查：** 一个独立 reviewer；普通修复只做 delta closure。
- **安全安装：** manifest 白名单、dry-run、原子安装、备份、哈希验证和 rollback。

本仓库不宣称兼容 Claude Code、Kimi Code、Zcode 或其他 Agent runtime。

## 三个档位

| 档位 | 场景 | 证据和审查 | Hook |
|---|---|---|---|
| `QUICK` | 解释、盘点、文档、可逆机械修改 | targeted；正式 review 可选 | advisory |
| `STANDARD` | 普通研究工程和开发 | affected-first；一次独立 review | advisory |
| `STRICT` | 安全/隐私、精确数学、公共合同、不可逆变更、installer/hook/发布 | V16 FAST/CANDIDATE/FINAL | fail-closed integrity |

默认是 adaptive。需要严格 Hook 时，在启动对应 Codex surface 前设置：

```bash
export CODEX_GOVERNANCE_MODE=strict
```

严格模式是显式选择，不再自动惩罚所有仓库任务。

## 模型分工

- Sol：规划、架构、综合判断和独立审查。
- Luna：默认执行主力，负责工具、实现、测试、数据和 Git/GitHub。
- Spark：由 Luna 派发短小、隔离、可并行任务。
- Terra：只有 Luna 确实不可用时才作为执行 fallback。

这些是路由默认值，不是能力封禁。

## 十分钟安装

### 1. Clone 并验证

```bash
git clone https://github.com/Qian9921/codex-governance-infra.git
cd codex-governance-infra

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

Overlay 只拥有 manifest 中列出的路径。现有 config、credential、plugin、memory、session、connection、cache、receipt 和其他无关文件全部保留。

### 4. 安装

```bash
python3 scripts/install-governance.py \
  --source . \
  --codex-home "$ACTIVE_CODEX_HOME"
```

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

工具不是打卡表。依赖 CodeGraph 或 Semble 的答案前先验证其仓库身份。执行主力可以只对 exact owning repo 修复一次。可选工具损坏时报告覆盖降级；只有缺失事实对正确判断不可替代时才阻塞。

## 代码规范

仓库架构和 formatter/linter/compiler 配置优先。适用的 Google 官方语言规范作为默认基线。新增重要抽象前必须选择 `REUSE`、`EXTEND` 或 `NEW`。优先组合，避免平行框架、猜测未来的泛化层和 God Class。

详见[代码健康与 Google 基线](docs/code-health.md)。

## GitHub 职责

- Qian9921 负责开发、push、开 PR，并逐条回应 finding。
- Liang9921 独立评论、审查 exact head、approve，并用 expected-head 保护 merge。

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
