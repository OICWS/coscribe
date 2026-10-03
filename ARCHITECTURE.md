# 本地办公 Agentic 系统 — 架构设计

> 本文档只记录"为什么这么设计"，不是逐次改动的流水账。具体功能的验证
> 过程、踩过的坑，见对应工具文件自己的 docstring/注释，以及
> `office-agent/ROADMAP.md`（功能演进记录）、
> `office-agent/src/coscribe/runtime_lg/README.md`（runtime 迁移史，
> 含真实 bug 记录）。

## 现状（当前实现）

- Agent runtime 是 `coscribe.runtime_lg`，基于 LangChain/LangGraph。下面
  "关键设计决策"第 1 节和"技术栈"一节描述的是最初自建的
  `Runner`/`RunState`/`ToolPolicy`/`FileStateStore` 版本，保留作为"当初
  为什么这么设计"的背景，**不是**当前实现——迁移原因是那套自建 runtime
  手写的 Gemini provider 适配层连续出过三个真实 bug。完整迁移过程见
  `runtime_lg/README.md`。
- Persona 系统（会话开始选人设）已删除——是从单一参考项目
  `andrewyng/openworker` 借鉴的做法，不是主流产品（Claude Cowork、
  ChatGPT）的通用模式；等价能力是全局的 `MEMORY.md`（Custom
  Instructions）。
- 前端：React + TypeScript（`frontend/`），`npm run build` 产出到
  `src/coscribe/web/static/`，后端协议不变。
- 桌面端：`office-agent-desktop/`，Electron 壳启动同一个 `coscribe-web`
  作为本地 sidecar 子进程（不是另一套前端）。原是 Tauri 壳，为原生嵌入
  Browser 面板换成 Electron；Tauri 壳已删除。
- 固定工作流：`coscribe.workflows`，每个工作流编译成一张 LangGraph
  `StateGraph` 运行（见下文第 6 节）。
- 内置浏览器：Electron `WebContentsView` 里的标签页，AI 通过 DevTools
  协议操作（见"范围边界"）。

## 目标

一个本地运行的、面向办公场景的 agent 系统。用户只需配置 LLM API Key
即可使用。

**目标用户**：更广泛、不一定懂代码的办公用户——本地读写文件、生成
文档/表格/PPT、连接第三方办公工具（Slack/Asana 等）、设置定时任务，是
"日常文件和工作流助理"，**不是**给开发者用的 agentic coding 工具。参考
claude-code 是借鉴其任务规划/Skill/Hooks/Subagent 的**实现思路**，不代表
产品定位要往开发者工具靠拢——内置 MCP 目录一度加过 `git`/`github`
连接器（含 GitHub OAuth Device Flow），判定跟目标用户不匹配后已整体
移除。新增功能前先问"这是不是普通办公用户会用到的"，不是"这对开发者是否
有用"。

- **骨架自建**：LLM 调用层、agent runtime（多智能体、任务规划、审查）、
  权限/审批引擎、会话与工作流持久化、UI。
- **能力借生态**：邮件/日历等具体办公能力一律通过 MCP、Skill、Plugin
  接入，不在骨架里手写。docx/pdf/xlsx/pptx 和浏览器是这条原则下的例外，
  见"范围边界"。

参考项目：
- [`aisuite`](https://github.com/andrewyng/aisuite)：LLM Provider 抽象层
  与 MCP client，直接作为依赖复用。
- [`claude-code`](https://github.com/anthropics/claude-code)：任务规划
  （Task 工具族 + Plan Mode）、Skill 加载、Hooks 事件模型、Subagent
  隔离的**设计思路**，用 Python 重新实现（原实现是 TypeScript 且与终端
  UI 强耦合，直接搬运代码成本高于重写）。

---

## 分层总览（原始设计；具体实现已迁移到 runtime_lg，层的概念大致仍适用）

```
┌─────────────────────────────────────────────────────────┐
│  UI（本地 Web）                                           │
│  - 对话界面：自然语言驱动，隐式生成工作流                    │
│  - 任务面板：实时展示当前计划（Task 列表）与执行进度           │
│  - 审批弹窗：中高风险工具调用需要用户确认                    │
│  - 扩展管理页：增删 MCP Server、管理 Skills、管理 Plugins    │
├─────────────────────────────────────────────────────────┤
│  Agent Runtime                                            │
│  - Coordinator agent：任务分解 + 派发 + 维护任务列表          │
│  - Specialist agents：按领域划分（文档/文件/网页等）          │
│  - Reviewer agent：复核产出是否满足原始需求，高风险动作落地前 │
│    或任务收尾前介入                                        │
│  - Task 追踪工具：外化的计划列表                            │
│  - Plan Mode：复杂任务先只读产出计划，用户确认后再放开写权限    │
├─────────────────────────────────────────────────────────┤
│  权限 / 审批引擎                                           │
│  - ToolMetadata(risk_level, requires_approval)             │
│  - 不区分工具来源（内置 / MCP / Skill 脚本），统一走同一套    │
│    风险分级与审批流程                                       │
├─────────────────────────────────────────────────────────┤
│  扩展点：MCP / Skill / Hooks / Plugin（见下文第 5 节）        │
├─────────────────────────────────────────────────────────┤
│  LLM Provider 层                                           │
│  - 统一 chat(messages, tools, model="provider:model") 接口  │
├─────────────────────────────────────────────────────────┤
│  会话 / 工作流持久化                                        │
│  - 隐式工作流：对话自动产生并持久化                          │
│  - 显式工作流：某次运行的 tool_call 序列抽取为可编辑/        │
│    可重放/可定时触发的参数化模板（已实现，见第 6 节）         │
├─────────────────────────────────────────────────────────┤
│  内置基础工具（最小集合，不做办公专用解析）                    │
│  - 本地文件读写搜索                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 关键设计决策与理由

### 1. 为什么 Agent Runtime 最初是自己写的，不是直接依赖 aisuite

最初判断是"直接依赖 `aisuite.agents`"，但已发布版本（0.1.14）里
`agents/`/`toolkits/` 根本不在发布包里，只存在于未发布的 GitHub `main`
分支代码中。而且已发布版本 `Client` 的"传 `tools=[...]` + `max_turns`
自动执行"路径完全没有审批介入点，跟本项目"风险分级+审批"的核心需求直接
冲突。所以：LLM Provider 层继续用已发布的 aisuite（`ProviderFactory` +
`Tools`）；Agent Runtime（`Agent`/`RunState`/`ToolPolicy`/
`FileStateStore`）完全自己写，设计思路参考那份未发布代码，但不依赖任何
未发布/无版本保证的第三方模块；`Runner` 自己驱动多轮循环（拿模型回复 →
`ToolPolicy` 判断是否需要审批 → 批准的才执行 → 结果写回历史 → 下一轮）。

（此设计已被 LangGraph 版 `runtime_lg` 取代，见"现状"一节。）

### 2. Planning 和 Reflection 不是"装了 MCP/Skill/Hooks/Subagent 就自带"的

Tool use 和 Multiagent 是结构性问题，靠 Runtime + MCP + Skill + Subagent
就解决了。但 Planning（先想清楚步骤）和 Reflection（回头检查自己做得
对不对）需要专门设计：

- **Planning**：不需要单独的"规划 agent"，是 Coordinator 自带的任务
  列表工具能力（对应 claude-code 的 `TaskCreateTool/TaskUpdateTool`
  思路）。可选加 Plan Mode：复杂任务先只读产出计划、用户确认后再放开
  写权限。
- **Reflection**：最好由没参与做这件事的 agent 来看，所以是单独的
  **Reviewer agent**——在专家 agent 完成产出后、或高风险操作落地前，对照
  原始需求复核一遍。

净增：1 个新 agent 角色（Reviewer）+ 1 个工具（任务追踪）。

### 3. 从 claude-code 抄思路、自己用 Python 重新实现的部分

aisuite 里没有、需要参考 claude-code 设计自己实现（不直接搬 TS 代码）：
Task 追踪工具族、Plan Mode、Skill 加载机制（`SKILL.md` 常驻描述 + 触发
后加载完整说明/脚本）、精简版 Hooks（`PreToolUse`/`PostToolUse`/
`SessionStart` 三种）、Subagent 上下文隔离（独立上下文窗口、独立工具
白名单、结果不污染主对话）。

### 4. 明确不抄的部分（对本地单用户场景是过度设计）

claude-code 的多层权限配置来源体系（五级覆盖）是给团队协作场景设计的，
本地单用户直接用三档 `risk_level` + 审批回调即可；`PostgresStateStore`
——本地场景文件存储够用；OpenAPI 自动生成工具、A2A 协议——生态不成熟或
收益低，不做。

### 5. 扩展点：MCP / Skill / Hooks / Plugin

- **MCP**：外部工具/数据的标准协议，config 格式与 Claude Desktop/Claude
  Code 的 `mcpServers` 兼容。用户可直接把社区现成的 MCP server（Google
  Drive、邮件等）配置进来。"Connector"本质是
  MCP 的更友好 UI 包装（预填好 command/args 的一键配置模板）。
- **Skill**：按需加载的操作说明+脚本包，覆盖"怎么做"，不是协议而是
  文件系统约定。两个来源：coscribe 自带三个内置 skill（PPTX/Excel/Word，
  包内固定路径随包分发）和用户自己在 `skills_dir` 里放的本地 skill，
  按会话勾选哪些生效。
- **Hooks**：生命周期事件挂自定义脚本做治理/联动，复用同一套 ToolPolicy
  审批引擎的事件点。
- **Plugin**：Skill + MCP 配置 + 命令打包成一键安装单元，是分发层，不是
  新协议。后期做。

### 6. 对话隐式执行；要重复的任务固化成固定工作流，AI 在运行之外

日常用法仍是隐式的：用户用自然语言描述任务，Coordinator agent 规划并
执行，任务面板实时展示计划/进度。

要重复的任务（定时任务）有两种：写成指令、每次由模型重新决定的"提示词
任务"；和把一次成功对话里的工具调用固化成固定步骤的"固定工作流"。固定
工作流追求稳定和速度，所以**运行时不交给模型决策**：步骤、参数、检查条件
都是写死的，只有显式的"模型步骤"才调用模型。每个工作流编译成一张
LangGraph `StateGraph`（分支、循环、审批用 `interrupt`，检查点可续跑），
没有另写图引擎。

AI 只在运行之外工作：
- **生成时**：从对话草拟步骤，真实运行测试（`test_workflow`），按失败
  原因修改、再测，最多 3 轮，测试结果和产出放在审核卡最前面，由用户
  保存。
- **失败时**：运行就在失败的那一步停下，不产出可疑数据；记录报错和当时
  的页面。AI 在一个新对话里复现、找原因、修改、测试，起草一张修改卡，
  仍由用户保存。保存后下次运行又是纯固定步骤。

步骤编辑器（增删改步骤、输入、检查条件）是这套固定工作流的界面，不是
通用的图编辑器。

---

## 技术栈（当前）

- 语言：Python（后端）+ TypeScript（前端）
- Agent Runtime：`coscribe.runtime_lg`（LangChain/LangGraph）
- 后端：FastAPI，WebSocket 推送审批请求/执行轨迹
- 前端：React + TypeScript，构建产物到 `web/static/`
- 桌面端：Electron（`office-agent-desktop/`），本地 sidecar 模式
- 持久化：本地文件/JSON（`state_dir`），无需数据库

## 范围边界（v1 明确不做 / 内置而非 MCP 的例外）

- **docx/pdf/xlsx/pptx 是内置工具，不走 MCP**：调研过的同类 MCP server
  都是个人维护的小众仓库，没有 playwright（微软官方）、fetch（MCP 官方
  参考实现）那样的权威来源——引入即让一个未经审计的第三方进程读写用户
  本地文件，信任风险比用成熟、审计充分的标准库更高。docx/pdf 用
  `python-docx`/`reportlab`（写）+ `mammoth`+`markdownify`/`pdfplumber`
  （读，借用 markitdown 内部同款库，绕开 markitdown 自己强制的
  `magika`/`onnxruntime` 依赖）；xlsx 用 `openpyxl`；pptx 用
  `python-pptx`（自 2024-08 起未发版，没有更好选择）。pptx 生成时用
  headless LibreOffice 做溢出检查（工具内部实现细节，不暴露成通用代码
  执行能力），LibreOffice 是可选运行时依赖，缺失时优雅降级、跳过检查。
  四个写工具都会生成缩略图（存 `state_dir/previews/`，不进用户
  workspace）。
- **`add_xlsx_chart`/`add_pptx_chart`**：只用 openpyxl/python-pptx 高层
  图表 API，不手写 XML；范围收窄到单组柱状/折线/饼图，规避"schema 合法
  但 Office 判定文件损坏"这类坑。
- **`add_docx_chart`/`add_docx_image`**：python-docx 没有图表 API，所以复用
  python-pptx 的图表 XML 与内嵌工作簿生成器，自己把 chart part 和内嵌
  xlsx 挂进 docx 包，再用一个 inline drawing 指向它。结果是 Word 里可
  编辑的原生图表，不依赖绘图库（打包版排除了 matplotlib）。图表宽度不
  超过页面文字栏宽，`after_text` 必须唯一命中一个段落。
- **xlsx 公式支持**：写公式前硬拒绝 LibreOffice 算不出来的
  `XLOOKUP`/`XMATCH`/`SORT`/`FILTER`/`UNIQUE`/`SEQUENCE`；`_xlfn.`
  前缀自动补给 2007 后新函数；用 headless LibreOffice + StarBasic 宏
  强制重算（普通 `--convert-to` 不会重算）。同样优雅降级：没装
  LibreOffice/超时/工作簿链接外部文件时只跳过，不让写入本身失败。
- **pptx/docx 手写 OOXML 的几处**（TOC、批注、tracked changes、
  transition、animation、背景图裁剪+z-order、半透明遮罩）：凡是
  python-pptx/python-docx 没有高层 API 的地方，写入后都过一遍
  LibreOffice headless 转 PDF 确认文件没损坏。动画范围刻意收窄：只有
  fade/fly-in 两种进入动画；docx track changes 不做表格级标记。
- **`run_python_script`/`run_node_script`**：补上真实代码执行能力
  （xlsx 大数据处理、pptxgenjs 等）。架构决定"零沙盒、纯靠审批"——沿用
  Claude Code 自己在原生 Windows 无 WSL2 时同样零沙盒的信任模型。故意
  不做 import 白名单/黑名单（容易绕过，只给假安全感）。唯一加的是文件夹
  护栏（`tools/script_guard.py`）：写入只允许工作区和会话添加的文件夹
  （Node 用 `--permission`，Python 用 audit hook），越界即拒绝并提示添加
  文件夹。它是防模型失误的护栏，不是边界——仍可读任意文件、联网、启动
  程序，直接调系统函数的 Python 也能绕过；界面和文档都如实这么写。各自用独立
  venv/`NODE_PATH` 跟 coscribe 自己的运行环境隔开（稳定性考虑）。网络
  访问不做限制（本地桌面应用没有对应的容器网络策略基础设施）。
- **`web_search`**：内置而非 MCP，用于事实核实/grounding（非 RAG）。
  底层 `ddgs` 自动在多个免费搜索引擎间选择，失败一个换下一个。
- **图片能力**（`tools/images.py`+`set_pptx_background_image`+
  `add_pptx_scrim`）：用户明确选择接入 raw DuckDuckGo 图片搜索、不做
  版权筛选，docstring/coordinator 指令如实告知生成的图需要用户自己核实
  版权。背景图裁剪按真实宽高比 cover；半透明遮罩解决深色文字压深色照片
  的对比度问题，颜色不自动套用，留给 `review_work` 判断。
- **`review_work`**：能读文档、看渲染图的审查 agent，只给只读工具，
  配图片走多模态 `image_url` 消息格式。是否调用交给模型自己判断（建议
  但不强制）——强制会让一份三行备忘录也要多付一次 LLM 调用成本。
- **内置 Skills（PPTX/Excel/Word）**：设置面板里可同时勾选多个、随时
  切换。扫描包内固定路径，跟用户自己的 `skills_dir` 是两个独立来源，
  合并使用。
- **`WorkspaceScope`**：file/document/spreadsheet/presentation 四类
  工具共用，默认只能读写 `COSCRIBE_WORKSPACE_ROOT`；
  `COSCRIBE_EXTRA_READABLE_DIRS`/`_WRITABLE_DIRS` 允许额外的、显式
  列出的目录，路径穿越和未列出的绝对路径依然拒绝。
- **挂起/恢复（Scheduled Tasks / selfwake）**：`WakeRequest`（线程内、
  临时暂停单次对话）和 `ScheduledTrigger`（全局、持久化实体，不依赖
  已有对话）是两个不同概念，模式复用但数据模型不共享。每次运行有自己
  独立的对话，不会把消息插进用户当时的对话里；打开着的标签页会实时看到
  运行过程。**已知限制**：时间解析用机器本地时区，不支持"每用户自己
  时区"。
- **内置浏览器**（`tools/browser.py` + 桌面端 `browserAgent.ts`）：
  内置而非 Playwright MCP——要用用户自己浏览器里的登录状态、让用户在
  面板里看着 AI 操作，并按网站授权。AI 用元素描述（如
  `button "Execute"`）而非一次性的 ref 指定目标，页面稳定后才操作，
  下载直接存进工作区。只有桌面端有；网页版没有浏览器工具。
- **Secrets**：`.env`/`providers.json`/`mcp.json` 三处明文存储，两层
  加固、互相独立：无条件 `chmod 0600`；可选 OS 钥匙串（`keyring`），
  backend 不可用时优雅降级回明文，保存动作本身不因钥匙串不可用而失败。
- **不做**：邮件/日历手写集成（走 MCP）、OAuth 一键授权基础设施、通用
  的图形化工作流画布、OpenAPI 自动工具生成、A2A 协议。
