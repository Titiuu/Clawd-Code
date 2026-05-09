# Claude-Code 的 Agent 上下文管理架构：一篇看懂

Agent 看起来像是在“连续聊天”，但模型本身每次调用其实都是一次新的请求。所谓上下文管理，就是在每次请求前，把项目状态、历史对话、工具调用结果和项目指令整理成模型能读懂的输入。

在 Claude-Code 里，上下文主要分成两类：

- 运行时上下文：当前仓库是什么样、Git 状态如何、有哪些项目指令。
- 会话上下文：用户说过什么、助手回过什么、工具调用和工具结果是什么。

可以把整体流程理解成这样：

```text
用户输入 -> Conversation -> Agent Loop
              |             |
              |             +-> build_context_prompt()
              |             +-> provider.chat(...)
              |             +-> tool results 写回 Conversation
              |
              +-> /context 估算
              +-> /compact 摘要压缩
```

## 1. 会话历史：Conversation 是主账本

会话历史由 `src/agent/conversation.py` 管理。核心对象是 `Conversation`，里面有一个 `messages` 列表。

每条消息是一个 `Message`：

- `role`：`user`、`assistant` 或 `system`
- `content`：可以是纯文本，也可以是结构化 content block
- `timestamp`：消息时间
- `_is_internal`：内部消息标记，比如 compact boundary，不会发给模型

结构化 content block 主要有三类：

- `TextContentBlock`：普通文本
- `ToolUseContentBlock`：助手请求调用工具
- `ToolResultContentBlock`：工具返回结果

这让 Claude-Code 可以把“助手想调用工具”和“工具执行结果”也保存在会话历史里。下一轮模型调用时，这些内容会再次成为模型可见的上下文。

`Conversation` 还做了一层很基础的历史裁剪：默认 `max_history=100`。当消息数量超过上限时，会从最早的消息开始移除。这是简单的数量裁剪，不是语义压缩。

## 2. 运行时上下文：每轮临时生成

运行时上下文由 `src/context_system/builder.py` 的 `build_context_prompt()` 生成。

它每次会收集三类信息：

1. Workspace snapshot

   来自 `workspace_snapshot.py`，包含：

   - workspace root
   - current directory
   - 顶层文件和目录
   - 关键文件，比如 `README.md`、`pyproject.toml`、`requirements.txt`
   - Python 文件数和测试文件数

2. Git context

   来自 `git_context.py`，包含：

   - Git 仓库根目录
   - 当前分支
   - 最近一次 commit
   - `git status --short --branch` 快照

   Git status 有长度限制，超过约 2000 字符会被截断。

3. Project instructions

   来自 `claude_md.py`，负责加载项目和用户级的 `CLAUDE.md` 类指令文件。

   当前候选路径包括：

   - 用户目录下的 `.Claude/CLAUDE.md`
   - 用户目录下的 `.claude/CLAUDE.md`
   - 项目内的 `CLAUDE.md`
   - 项目内的 `.Claude/CLAUDE.md`
   - 项目内的 `.claude/CLAUDE.md`

   它会从当前目录一路向 workspace root 查找，并设置文件数量、单文件字符数、总字符数限制，避免把项目指令无限塞进 prompt。

最终 `build_context_prompt()` 会把这些信息渲染成 Markdown 风格的系统提示片段，例如：

```md
## Runtime Context
- Today's date: 2026-05-08
- Workspace root: ...
- Current directory: ...

## Git Context
- Current branch: ...

## Project Instructions
### ./CLAUDE.md
...
```

## 3. Agent Loop：把上下文送进模型

真正调用模型的地方在 `src/tool_system/agent_loop.py` 的 `run_agent_loop()`。

每轮调用前，它会做几件事：

1. 从 tool registry 读取工具 schema。
2. 解析 output style prompt。
3. 调用 `build_context_prompt()` 生成运行时上下文。
4. 把 output style prompt 和 runtime context 拼成最终 system prompt。
5. 根据 provider 类型组织请求。

Anthropic 类 provider 和 OpenAI 类 provider 的处理方式不一样：

- Anthropic / Minimax：通过 `system` 参数传 system prompt，消息体来自 `conversation.get_messages()`。
- OpenAI / GLM 等非 Anthropic provider：把 system prompt 作为第一条 `system` message 插入 OpenAI 格式消息列表。

工具调用循环也是在这里完成的：

```text
模型响应 -> 发现 tool_use -> dispatch 工具
          -> 工具结果写入上下文
          -> 再次调用模型
          -> 直到没有工具调用或达到 max_turns
```

对于 Anthropic 类 provider，工具调用会被写成 `ToolUseContentBlock`，工具结果会以 `ToolResultContentBlock` 追加到 `Conversation`。对于 OpenAI 类 provider，运行时内部会维护一份 OpenAI 格式的消息列表，同时也把助手文本写回 `Conversation`，用于会话历史和后续功能。

这就是为什么 agent 能“记得”刚刚读过哪个文件、跑过什么命令：工具结果不是临时打印完就丢，而是进入了会话上下文。

## 4. REPL：用户输入怎么进入上下文

REPL 的主流程在 `src/repl/core.py`。

用户每次输入普通聊天内容时，`chat()` 会先调用：

```python
self.session.conversation.add_user_message(user_input)
```

然后再进入 `run_agent_loop()`。也就是说，用户输入先进入 `Conversation`，再由 agent loop 转成 provider 需要的 API 消息。

REPL 中还有一个直接流式回复路径，用于很短、明显不需要工具的普通聊天。这条路径会直接调用 provider streaming，并在完成后把助手回复写回 `Conversation`。但对于代码任务、文件任务、搜索任务等，仍然会走 agent loop。

## 5. /context：上下文用了多少 token

`/context` 命令不是修改上下文，而是做一次估算和展示。

入口在 `src/command_system/builtins.py` 的 `context_command_call()`，核心分析逻辑在 `src/context_system/context_analyzer.py`。

它会统计这些类别：

- System prompt
- System tools
- MCP tools
- Custom agents
- Memory files，也就是 `CLAUDE.md`
- Skills
- Messages
- Free space

token 估算来自 `src/token_estimation.py`：

- 优先使用 `tiktoken` 的 `cl100k_base`
- 如果不可用，就退回到按字符数粗估，大约 `len(text) // 4`

模型上下文窗口大小由 `get_context_window_for_model()` 判断。比如 Claude Sonnet、Claude Opus 默认按 200k token，OpenAI GPT-4o 类模型按 128k token。

需要注意的是，`/context` 展示的是估算值，不等同于 provider 返回的真实计费 token。若有 API usage 数据，它会优先用实际 usage 来计算总量。

## 6. /compact：把旧历史压成摘要

当会话很长时，只靠 `max_history=100` 会比较粗糙，因为它直接丢旧消息，可能丢掉重要背景。`/compact` 的目标是把旧对话总结成一条摘要，让后续模型仍能接住上下文。

主要逻辑在 `src/compact_service/service.py`。

一次 compact 大致分成九步：

1. 找到上一次 compact boundary 之后的消息，避免重复总结已经总结过的内容。
2. 统计 compact 前 token 数。
3. 做轻量预处理：剥离图片/文档块，清理部分旧工具结果。
4. 构造总结 prompt。
5. 调用 LLM 生成纯文本摘要。
6. 创建 compact boundary message。
7. 创建 compact summary message。
8. 用 boundary 和 summary 替换旧历史。
9. 返回用户可见的 compact 结果。

这里有两个特殊消息：

- boundary message：`system` 角色，带 `_is_internal=True`，用于标记压缩边界，不会通过 `conversation.get_messages()` 发给模型。
- summary message：`user` 角色，内容以 “This session is being continued from a previous conversation.” 开头，会继续发给模型。

所以 compact 之后，模型看到的不是原始长历史，而是一条“这段会话从之前继续，摘要如下”的用户消息。

如果 LLM 摘要调用失败，compact service 会退回到一个简单的文本摘要，至少保留消息数量、工具使用和最近用户/助手消息等信息。

## 7. Microcompact：轻量预处理，不是完整自动压缩

`src/context_system/microcompact.py` 里有一个更轻的压缩工具。

它主要做两件事：

- 把图片和文档块替换成 `[image]`、`[document]` 这类文本标记。
- 对较旧的工具结果清空内容，替换成 `[Old tool result content cleared]`。

这个模块被 `/compact` 流程使用，用来降低摘要请求本身的 token 压力。当前代码里它不是 agent loop 的自动压缩入口，也不等于完整的 auto compact。

## 8. Session：上下文可以落盘

`src/agent/session.py` 负责 session 持久化。

保存时会把这些信息写入 `~/.Claude/sessions/<session_id>.json`：

- session id
- provider
- model
- conversation
- created_at
- updated_at

其中 conversation 使用 `Conversation.to_dict()` 序列化，加载时再通过 `Conversation.from_dict()` 还原。这样会话历史可以跨 REPL 进程恢复。

compact boundary 的 `_is_internal` 字段也会被序列化保存，因此恢复 session 后，它仍然能作为内部边界存在，并继续被 API 消息过滤。

## 9. 这套架构的特点

这套设计的好处是分层清楚：

- `Conversation` 只管历史消息。
- `context_system` 只管收集和渲染运行时上下文。
- `agent_loop` 只管把上下文、工具 schema、provider 调用和工具结果串起来。
- `context_analyzer` 只管估算和展示。
- `compact_service` 只管长会话压缩。

这样每个模块的职责比较单一，也方便单独测试。

当前实现也有边界：

- workspace/git/`CLAUDE.md` 注入已经可用，但还不是完整 repo map 或深度项目理解。
- `/compact` 已经有手动压缩能力，但不是完整后台自动压缩系统。
- memory、README 摘要、disk-backed session memory、reactive compact 等能力仍在完善方向里。
- token 统计是估算，不能完全代表不同 provider 的真实 tokenizer 行为。

## 总结

Claude-Code 的上下文管理可以用一句话概括：

> 每轮请求前动态注入项目运行状态，同时持续维护会话历史；当历史太长时，用摘要替代旧消息，让 agent 尽量保留任务连续性。

代码上对应五个核心位置：

- `src/agent/conversation.py`：会话历史
- `src/context_system/builder.py`：运行时上下文构建
- `src/tool_system/agent_loop.py`：上下文进入模型和工具循环
- `src/context_system/context_analyzer.py`：`/context` 估算
- `src/compact_service/service.py`：`/compact` 压缩

理解这几处，就基本理解了当前 agent 上下文管理的主干。
