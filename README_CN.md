# PDClaw 🦞

> *像一只精准的机械爪 —— 逐个抓取 Issue —— PDClaw 通过标签驱动的工作流和 AI 技能执行，在 GitHub Issues 上自动化 **Plan-Do-Check-Act** 循环。*

[English](./README.md) | 中文

## 设计哲学

PDClaw 将 PDCA（Plan-Do-Check-Act）持续改进方法论引入软件开发。无需手动协调代码审查、测试和部署，你只需在 GitHub Issue 上通过简单的 hashtag 评论交互 —— PDClaw 便会调度 AI Agent 执行每个阶段。

### 核心原则

- **标签驱动**：无需仪表盘、Webhook 或额外基础设施。在 Issue 上评论 `#pdca-start`，PDClaw 就会自动拾取。
- **AI 原生**：每个 PDCA 步骤都由 AI Agent 配合专属技能定义文件（`skills/pdca-*.md`）执行，产出结构化的输出文件。
- **Git 优先**：所有生成的产物存放在仓库的 `docs/` 目录中，位于特性分支（`pdca/<issue#>-<slug>`）上。你像审查其他代码变更一样审查、批准并合并。
- **人机协作**：PDClaw 从不会自动批准。你必须显式标记每一步（`#plan-approved`、`#do-approved`、`#check-approved`）来推进。决策（`#Deploy`、`#Fix`、`#Fallback`）始终是人工执行的。
- **关注点分离**：敏感凭证存放在环境变量中。其余配置通过 CLI 参数或 `config.ini` 管理 —— 可预测、可审计、易于上手。

---

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                      GitHub Issue                           │
│  评论: #pdca-start → #plan-approved → ... → #Deploy         │
└──────────────────────┬──────────────────────────────────────┘
                       │ 轮询
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                     pdclaw.py                               │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  状态管理    │  │  标签解析器  │  │  步骤调度器      │  │
│  │ (.pdca_state)│  │ (生命周期 +  │  │  (plan/do/check/ │  │
│  │              │  │  步骤标签)   │  │   act/decision)  │  │
│  └──────────────┘  └──────────────┘  └────────┬─────────┘  │
│                                                │            │
│                     ┌──────────────────────────┘            │
│                     ▼                                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                  AI 执行                              │  │
│  │  ┌─────────────────┐  ┌────────────────────────────┐ │  │
│  │  │  会话模式       │  │  无状态模式（回退方案）    │ │  │
│  │  │  (有状态,       │  │  (一次性 claude 调用)     │ │  │
│  │  │   跨步骤上下文) │  │                            │ │  │
│  │  └─────────────────┘  └────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────┘  │
│                     │                                       │
│  ┌──────────────────┴──────────────────────────────────┐   │
│  │              支撑系统                                │   │
│  │  ┌──────────────┐  ┌──────────────────────────────┐ │   │
│  │  │  记忆系统    │  │  技能定义                     │ │   │
│  │  │  (全局 +     │  │  skills/pdca-{plan,do,        │ │   │
│  │  │   每个Issue) │  │  check,act}.md               │ │   │
│  │  └──────────────┘  └──────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Git 仓库                                  │
│  pdca/<issue#>-<slug>/  ← 特性分支                           │
│  └── docs/<issue#>-<slug>/<step>/  ← 生成的产物               │
└─────────────────────────────────────────────────────────────┘
```

### 组件地图

| 组件 | 文件 | 职责 |
|---|---|---|
| **核心引擎** | `pdclaw.py` | 轮询 GitHub、解析标签、调度 PDCA 步骤、管理 Git 分支和状态 |
| **记忆系统** | `pdca_memory.py` | 持久化的全局 + 每个 Issue 知识库，注入 AI prompt |
| **记忆 CLI** | `pdca_memory_cli.py` | 命令行工具，用于查看和管理记忆 |
| **会话管理器** | `pdca_claude_session.py` | 有状态 AI 会话，同一 Issue 跨步骤保持上下文 |
| **指标收集器** | `pdca_metrics.py` | 运行时指标 — AI 调用延迟、步骤成功率、状态转换 |
| **仪表盘** | `pdca_dashboard.py` | 本地 HTTP 服务 + HTML UI，用于实时监控 |
| **技能定义** | `skills/*.md` | Markdown 模板，定义 AI 如何执行每个 PDCA 步骤 |
| **配置文件** | `config.ini` | 项目级设置（间隔、路径、模型、部署分支、仪表盘） |

### 数据流

```
Issue #pdca-start
  → 轮询循环检测到标签
  → 状态机确定当前步骤 (plan)
  → 加载技能文件 (skills/pdca-plan.md)
  → 注入记忆 (全局 + 当前 Issue 上下文)
  → AI 通过 claude CLI 执行 (会话模式或无状态模式)
  → 解析输出，文件写入 docs/<issue#>-<slug>/plan/
  → Git 提交 + 推送到 pdca/<issue#>-<slug> 分支
  → PDClaw 发布评论附带摘要，等待下一个标签
```

### 状态机

```
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  ▼                                                              │
 [idle] ──#pdca-start──▶ [plan] ──#plan-approved──▶ [do]        │
   ▲                         │                        │          │
   │                         │ #pdca-refresh          │ #do-approved
   │                         ▼                        ▼          │
   │ #pdca-abort          [plan] (重新执行)        [check]       │
   │ #pdca-reset             │                        │          │
   │                         │              #check-approved      │
   │                         │              #pdca-refresh         │
   │                         │                        ▼          │
   │                         │               [check] (重新执行)   │
   │                         │                        │          │
   │                         │         Check 成功 → 决策阶段      │
   │                         │              │         │          │
   │                         │     #Deploy  #Fix   #Fallback     │
   │                         │         │       │        │        │
   │                         │         ▼       │        │        │
   │                         │    [deploy]     │        ▼        │
   │                         │         │       │   [revert]      │
   │                         │         ▼       ▼        │        │
   │                         └──── [done] ◀── [do] ◀────┘        │
   │                                     │                       │
   └─────────────────────────────────────┘                       │
                              #pdca-close                        │

  注意：在正常流程中，Act 步骤被决策阶段
  (#Deploy / #Fix / #Fallback) 替代。#act-approved 标签和
  pdca-act 技能仅在需要显式 Act 执行的场景中存在，
  实际使用中较为少见。
```

---

## 环境要求

- **Python 3.10+**
- **`requests`** 库（`pip install requests`）
- **[Claude Code](https://claude.ai/code)** CLI 已安装并在 PATH 中可用（命令名 `claude`）
- **GitHub token** 具有仓库访问权限 → `GITHUB_TOKEN` 或 `GH_TOKEN`
- **DeepSeek API key** → `DEEPSEEK_API_KEY`

---

## 快速开始

```bash
# 1. 安装
pip install requests

# 2. 配置凭证
export GITHUB_TOKEN=ghp_your_token_here
export DEEPSEEK_API_KEY=sk_your_key_here

# 3. 运行（推荐先对单个 Issue 试跑）
python pdclaw.py --issue https://github.com/owner/repo/issues/42 --once --auto-run --verbose
```

> 测试通过后，去掉 `--issue` 和 `--once` 即可开始全仓库轮询：
> ```bash
> python pdclaw.py --repo owner/repo --auto-run
> ```

---

## 工作原理

### PDCA 工作流标签

在 GitHub Issue 上以评论形式添加这些标签，PDClaw 检测到后便会推进循环：

| 标签 | 触发步骤 | AI 技能 | 生成文件 |
|---|---|---|---|
| `#pdca-start` | **Plan** | `pdca-plan` | `Design.md`、`Impact.md` |
| `#plan-approved` | **Do** | `pdca-do` | `Change.md` |
| `#do-approved` | **Check** | `pdca-check` | `Review.md`、`Test.md` |
| `#check-approved` | **Check** | `pdca-check` | 运行 Check，然后进入决策阶段 |
| `#act-approved` | **Act** | `pdca-act` | `Decision.md`（较少使用 — 通常由决策阶段替代） |

> **为什么 Act 较少使用？** Check 步骤成功后，PDClaw 进入*决策阶段*，要求你选择 `#Deploy`、`#Fix` 或 `#Fallback`。这个人工决策在正常流程中替代了自动化的 Act 步骤。`#act-approved` 标签和 `pdca-act` 技能仅为向后兼容和需要显式 Act 执行的边缘场景而保留。

### 决策阶段标签

Check 成功后，PDClaw 会发布一条评论请求决策。回复以下标签之一：

| 标签 | 效果 |
|---|---|
| `#Deploy` | 将 `pdca/<issue#>-<slug>` 分支合并到配置的部署分支 |
| `#Fix` | 重置到 Do 步骤 — 在评论中附上反馈作为下次迭代的上下文 |
| `#Fallback` | 回滚所有变更并删除 PDCA 特性分支 |

### 控制标签

在循环中随时可用：

| 标签 | 操作 |
|---|---|
| `#pdca-refresh` | 重新执行当前步骤 |
| `#pdca-abort` | 停止处理该 Issue |
| `#pdca-close` | 关闭 GitHub Issue |
| `#pdca-skip` | 标记该 Issue 为跳过 |
| `#pdca-reset` | 清除所有状态并重新开始 |
| `#pdca-new-session` | 仅重置 AI 会话（保留状态和记忆） |

### 分步演练

1. **启动**：创建一个 GitHub Issue 描述变更内容。添加评论 `#pdca-start`。
2. **Plan**：PDClaw 检测到标签 → 加载 `skills/pdca-plan.md` → AI 分析代码库 → 生成 `Design.md` 和 `Impact.md` → 提交到 `pdca/<issue#>-<slug>`。
3. **审查并批准**：检查生成的文档。如果满意，评论 `#plan-approved`。
4. **Do**：PDClaw 加载 `skills/pdca-do.md` → AI 实现变更 → 生成附带 diff 摘要的 `Change.md`。
5. **Check**：评论 `#do-approved` → AI 生成 `Review.md` 和 `Test.md` 用于验证。
6. **决策**：评论 `#check-approved` → Check 再次运行 → PDClaw 请求最终决策（`#Deploy` / `#Fix` / `#Fallback`）。

所有生成的文件都存放在 `pdca/<issue#>-<slug>` 特性分支的 `docs/<issue#>-<slug>/<step>/` 下。在你部署之前，任何内容都不会触及主分支。

---

## 配置

### 优先级规则

```
CLI 参数  >  config.ini  >  内置默认值

环境变量仅用于敏感凭证。
```

### config.ini

位于 `pdclaw.py` 同级目录。所有键都有合理的默认值 — 该文件是可选的。

```ini
[runner]
; #Deploy 决策的目标分支 (CLI --deploy-branch > 此处)
deploy_branch = main
; 轮询间隔（秒）(CLI --interval > 此处)
interval = 180

[paths]
; 工作目录 — 你的本地 git 仓库根目录 (CLI --work-dir > 此处)
work_dir = .
; 内部状态追踪 (CLI --state-dir > 此处)
state_dir = .pdca_state
; 记忆存储 (CLI --memory-dir > 此处)
memory_dir = .pdca/memory
; 技能定义目录（相对于 pdclaw.py）
skills_dir = skills

[ai]
; 模型标识符 (CLI --model > 此处)
model = deepseek-v4-flash
; API 端点 (CLI --base-url > 此处)
base_url = https://api.deepseek.com/anthropic
```

使用 `--config` 指定其他配置文件：

```bash
python pdclaw.py --repo owner/repo --auto-run --config /path/to/production.ini
```

### CLI 参考

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--config` | `config.ini` | 配置文件路径 |
| `--repo` | — | GitHub 仓库 `owner/repo`（除非使用 `--issue` 否则必填） |
| `--issue` | — | 要处理的单个 Issue URL |
| `--interval` | `180` | 轮询间隔（秒） |
| `--deploy-branch` | `main` | `#Deploy` 决策的目标分支 |
| `--model` | `deepseek-v4-flash` | AI 模型标识符 |
| `--base-url` | `https://api.deepseek.com/anthropic` | AI API 基础 URL |
| `--work-dir` | `.` | 本地 git 仓库根目录 |
| `--state-dir` | `.pdca_state` | 状态追踪目录 |
| `--memory-dir` | `.pdca/memory` | 记忆存储目录 |
| `--auto-run` | 关闭 | 自动执行 `claude` CLI（关闭 = 手动模式） |
| `--once` | 关闭 | 运行一个轮询周期后退出 |
| `--verbose` / `-v` | 关闭 | 启用调试级别日志 |
| `--no-memory` | 关闭 | 禁用记忆系统 |
| `--use-session` | 开启 | 启用有状态 AI 会话 |
| `--no-session` | 关闭 | 强制无状态模式 |
| `--dashboard` | 开启 | 启用本地 Web 仪表盘 |
| `--no-dashboard` | 关闭 | 禁用仪表盘 |
| `--dashboard-port` | `9191` | 仪表盘 HTTP 端口 |
| `--metrics-dir` | `.pdca/metrics` | 指标存储目录 |

### 环境变量

**仅用于凭证。** 其余配置通过 CLI 或 `config.ini` 管理。

| 变量 | 说明 |
|---|---|
| `GITHUB_TOKEN` / `GH_TOKEN` | 具有仓库权限的 GitHub personal access token |
| `DEEPSEEK_API_KEY` | 用于访问 AI 模型的 DeepSeek API key |

---

## 功能特性

### 会话模式（默认）

有状态 AI 会话在同一 Issue 的 PDCA 步骤之间保持上下文。Plan 步骤的分析结果会传递给 Do，Do 的实现结果会传递给 Check，以此类推 — 无需每次都重新发送整个对话。

- 每个 Issue 拥有独立的会话（无交叉污染）
- 会话持久化存储在 `.pdca_state/<issue#>/claude_session.json`
- 使用 `--no-session` 回退到无状态的一次性调用
- 使用标签 `#pdca-new-session` 在循环中途重置会话

详见 [SESSION_MODE.md](./SESSION_MODE.md)。

### 记忆系统

持久化知识库，将项目上下文和历史经验注入每个 AI prompt。

- **全局记忆** — 编码规范、架构模式、经验教训（所有 Issue 共享）
- **Issue 记忆** — 每个 Issue 独立的决策、待办事项、上下文键值对
- AI 可通过输出中的 HTML 注释自行更新记忆
- 通过 `pdca_memory_cli.py` 或 CLI 直接管理

详见 [MEMORY_SYSTEM.md](./MEMORY_SYSTEM.md)。

### Git 集成

每个 PDCA 循环在其专属的特性分支上运行：

```
pdca/42-add-payment-gateway/
└── docs/
    └── 42-add-payment-gateway/
        ├── plan/
        │   ├── Design.md
        │   └── Impact.md
        ├── do/
        │   └── Change.md
        └── check/
            ├── Review.md
            └── Test.md
```

- PDClaw 从部署分支自动创建分支
- 每个步骤的输出都会被提交并推送
- `#Deploy` 将 PDCA 分支合并回部署分支
- `#Fallback` 删除分支并回滚所有内容

### 本地仪表盘

内置的 Web 仪表盘提供 PDClaw 状态的实时可见性 — 无需外部监控工具。

```
http://localhost:9191/          → 仪表盘首页
http://localhost:9191/api/status → JSON 快照
```

**可以查看的内容：**
- 活跃/已完成的 Issue 数量
- 每个 Issue 的进度（当前步骤、AI 调用次数、总 AI 耗时）
- 最近的 AI 调用历史（步骤、延迟、成功/失败、预估 token 数）
- 每个步骤的成功率及进度条
- 轮询周期计数和运行时长

```bash
# 仪表盘默认启用
python pdclaw.py --repo owner/repo --auto-run
# → 打开 http://localhost:9191

# 自定义端口
python pdclaw.py --repo owner/repo --auto-run --dashboard-port 8888

# 禁用仪表盘
python pdclaw.py --repo owner/repo --auto-run --no-dashboard
```

### 指标收集

所有 AI 调用的耗时、成功率和状态转换都持久化存储在 `.pdca/metrics/`：

```
.pdca/metrics/
├── ai_calls.jsonl     # 每次 AI 调用的延迟、token 数、模型
├── summary.json       # 仪表盘使用的滚动快照
└── daily_2026-06-13.json  # 每日归档（未来）
```

- 零开销 — 指标在内存中收集，定期刷新到磁盘
- 线程安全 — 不影响轮询循环
- 可通过 `http://localhost:9191/api/status` 查询

---

## 最佳实践

### 入门指南

1. **首先初始化记忆** — 运行 `python pdca_memory_cli.py init` 设置项目上下文、编码规范和常见模式。这能让 AI 从一开始就具备必要的领域知识。
2. **从小处着手** — 在启用全仓库轮询之前，先用 `--issue <url> --once --auto-run --verbose` 对单个范围明确的 Issue 进行测试。
3. **仔细审查 Plan 输出** — Plan 步骤决定了方向。糟糕的 `Design.md` = 糟糕的实现。必要时使用 `#pdca-refresh` 迭代。

### 日常工作流

4. **多使用 `#Fix`** — 如果 Do 或 Check 的输出不理想，使用 `#Fix` 并附上具体反馈，而不是中止。AI 会将你的反馈作为下次尝试的上下文。
5. **保持记忆更新** — 定期检查全局记忆（`python pdca_memory_cli.py show`）。移除过时的模式。从已完成的循环中添加新的经验教训。
6. **一个 Issue，一个关注点** — 每个 GitHub Issue 应只涉及一个功能或修复。这能保持 PDCA 分支专注且易于合并。

### 生产环境部署

7. **作为后台服务运行** — 使用 systemd、supervisor 或简单的 `nohup` 保持 PDClaw 持续轮询：

   ```bash
   nohup python pdclaw.py --repo owner/repo --auto-run --interval 300 \
     > pdca.log 2>&1 &
   ```

8. **使用专用 GitHub token** — 创建一个机器账号或细粒度 PAT，只授予目标仓库的最小权限（repo 读写）。
9. **配置部署分支** — 将 `deploy_branch` 设置为你的集成分支（如 `develop`）而非 `main`，增加一层安全保障。
10. **监控日志** — PDClaw 会记录每一步、标签检测和 AI 执行。初始设置时使用 `-v` 参数运行以验证一切正常。

### 安全

11. **切勿提交凭证** — `GITHUB_TOKEN` 和 `DEEPSEEK_API_KEY` 必须始终通过环境变量设置。切勿放入 `config.ini`。
12. **审查生成的代码** — AI 输出是起点，不是最终答案。部署前务必审查 `Change.md` 和实际的代码变更。
13. **敏感仓库使用 `--no-memory`** — 如果你的代码库包含专有信息，考虑禁用记忆系统以防止 Issue 间信息泄露。

---

## 项目结构

```
pdca-open-source/
├── pdclaw.py                # 核心引擎 — 轮询、状态机、Git 操作、AI 调度
├── pdca_memory.py           # 持久化记忆 — 全局 + 每个 Issue 知识库
├── pdca_memory_cli.py       # 记忆管理 CLI
├── pdca_claude_session.py   # 有状态 AI 会话管理器
├── pdca_metrics.py          # 运行时指标收集器
├── pdca_dashboard.py        # 本地 Web 仪表盘 (HTTP + HTML UI)
├── config.ini               # 项目配置（可选，有默认值）
├── skills/                  # AI 技能定义 (Markdown)
│   ├── pdca-plan.md         #   Plan 步骤 — 分析与设计
│   ├── pdca-do.md           #   Do 步骤 — 实现变更
│   ├── pdca-check.md        #   Check 步骤 — 审查与测试
│   └── pdca-act.md          #   Act 步骤 — 决策执行
├── MEMORY_SYSTEM.md         # 记忆系统文档
├── SESSION_MODE.md          # 会话模式文档
├── CONTRIBUTING.md          # 贡献指南
└── LICENSE                  # 许可证
```

---

## 许可证

详见 [LICENSE](./LICENSE)。
