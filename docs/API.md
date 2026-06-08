# Clawd Agent Runtime — API 接口文档

## 概述

Clawd Agent Runtime 是一个基于 FastAPI 的 HTTP 服务，将 Clawd Codex 的 AI Agent 能力封装为 RESTful API。服务默认监听 `0.0.0.0:31366`，提供会话管理、技能安装、对话交互等接口，同时内置一个 Web 控制台。

**基础 URL**: `http://<host>:31366`

---

## 1. 环境变量

| 变量名 | 默认值 | 说明 |
|---|---|---|
| `CLAWD_SKILLS_DIR` | `~/.clawd/skills` | 技能文件存放目录 |
| `CLAWD_RUNTIME_ROOT` | `~/.clawd/runtime` | 运行时数据根目录（会话持久化等） |
| `CLAWD_WORKSPACE_ROOT` | `~/.clawd/runtime/workspaces` | Agent 工作空间根目录（每个 session 一个子目录） |

---

## 2. 通用约定

### 2.1 Content-Type

- 请求体统一使用 `application/json`
- 文件上传接口使用 `multipart/form-data`

### 2.2 错误响应格式

所有接口的错误响应遵循 FastAPI 默认格式：

```json
{
  "detail": "错误描述信息"
}
```

### 2.3 HTTP 状态码

| 状态码 | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 请求参数错误（无效 session_id、skill 名称不合法等） |
| 404 | 资源不存在（session 未找到） |
| 409 | 冲突（技能已存在且未启用覆盖） |
| 413 | 请求体过大（zip 文件或解压后超过限制） |
| 500 | 服务端内部错误 |

---

## 3. 数据模型

### 3.1 消息内容块（Content Blocks）

消息内容可能是纯文本字符串，也可能是内容块的列表。内容块有三种类型：

**TextContentBlock** — 文本块
```json
{ "type": "text", "text": "文本内容" }
```

**ToolUseContentBlock** — 工具调用块
```json
{
  "type": "tool_use",
  "id": "toolu_xxxx",
  "name": "Bash",
  "input": { "command": "ls -la", "description": "列出文件" }
}
```

**ToolResultContentBlock** — 工具结果块
```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_xxxx",
  "content": "命令输出...",
  "is_error": false
}
```

### 3.2 消息（Message）

```json
{
  "role": "user | assistant | system",
  "content": "文本字符串 或 [<content_block>, ...]"
}
```

### 3.3 使用量统计（Usage）

```json
{
  "input_tokens": 1234,
  "output_tokens": 567
}
```

---

## 4. API 接口

### 4.1 Web 控制台

#### `GET /`

返回内置的 Web 控制台页面（`index.html`），提供可视化的运行状态检查、技能管理和对话测试界面。

#### `HEAD /`

`GET /` 的 HEAD 版本，返回相同的响应头。

---

### 4.2 健康检查

#### `GET /health`

获取服务运行状态与配置信息。

**响应** `200 OK`

```json
{
  "status": "ok",
  "provider": "anthropic",
  "provider_configured": true,
  "skills_dir": "/home/user/.clawd/skills",
  "runtime_root": "/home/user/.clawd/runtime",
  "workspace_root": "/home/user/.clawd/runtime/workspaces"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | `string` | 固定为 `"ok"` |
| `provider` | `string` | 默认 LLM 提供商名称，如 `"anthropic"`、`"openai"` |
| `provider_configured` | `bool` | 是否已配置 API Key |
| `skills_dir` | `string` | 技能文件目录的绝对路径 |
| `runtime_root` | `string` | 运行时数据根目录 |
| `workspace_root` | `string` | Agent 工作空间根目录 |

---

### 4.3 技能管理

#### `GET /skills`

列出所有已安装的技能。

**响应** `200 OK`

```json
[
  {
    "name": "code-review",
    "description": "Review the current diff for correctness bugs and reuse/simplification/efficiency cleanups",
    "loaded_from": "/home/user/.clawd/skills/code-review/SKILL.md",
    "skill_root": "/home/user/.clawd/skills/code-review",
    "user_invocable": true
  }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `string` | 技能名称（全局唯一标识） |
| `description` | `string` | 技能功能描述 |
| `loaded_from` | `string` | SKILL.md 文件的路径 |
| `skill_root` | `string \| null` | 技能根目录（包含 SKILL.md 的目录） |
| `user_invocable` | `bool` | 是否允许用户通过 `/skill-name` 方式调用 |

---

#### `PUT /skills/{skill_name}`

安装/更新一个技能。技能以 `.zip` 格式上传，zip 包内必须恰好包含一个 `SKILL.md` 文件。

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `skill_name` | `string` | 技能名称，不能包含 `/` 或 `\` 路径分隔符 |

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `overwrite` | `bool` | `false` | 是否覆盖已存在的同名技能 |

**请求体** `multipart/form-data`

| 字段 | 类型 | 说明 |
|---|---|---|
| `file` | `file (.zip)` | 技能的 zip 包 |

**约束条件**
- zip 文件大小 ≤ 25 MB（`MAX_ZIP_BYTES`）
- 解压后总大小 ≤ 100 MB（`MAX_EXTRACTED_BYTES`）
- zip 内文件数 ≤ 2000（`MAX_ZIP_MEMBERS`）
- zip 内不允许符号链接
- zip 内路径不允许 `..` 或绝对路径（防止路径穿越攻击）
- 必须恰好包含一个 `SKILL.md` 文件，其所在目录即为技能根目录

**响应** `200 OK`

```json
{
  "name": "my-skill",
  "path": "/home/user/.clawd/skills/my-skill",
  "overwritten": false
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `string` | 技能名称 |
| `path` | `string` | 安装后的绝对路径 |
| `overwritten` | `bool` | 是否覆盖了已有技能 |

**错误码**
- `400` — 技能名称不合法、zip 格式无效、zip 包为空/文件过多、路径不安全
- `409` — 技能已存在且 `overwrite` 为 `false`
- `413` — zip 文件或解压后内容超过大小限制
- `500` — 文件系统写入失败

---

### 4.4 对话交互

#### `POST /chat`

向 Agent 发送一条消息并获取回复。这是核心接口，内部会运行完整的 Agent Loop（Agent 可调用工具、多轮推理，直到产出最终文本回复）。

**请求体** `application/json`

```json
{
  "message": "Please help me write a Python function to sort a list",
  "session_id": "abc123def456",
  "max_turns": 20
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `message` | `string` | ✅ | — | 用户消息，最少 1 个字符。支持以 `/skill-name args` 开头直接调用技能 |
| `session_id` | `string \| null` | ❌ | 自动生成 | 会话 ID。传入已有 ID 可继续对话；不传或传 `null` 则创建新会话。不能包含 `/` 或 `\` |
| `max_turns` | `int` | ❌ | `20` | Agent Loop 最大轮次（1–100）。防止无限循环 |

**消息前缀机制（Skill Slash 展开）**

如果 `message` 以 `/` 开头且不是单独的 `/` 或 `/help`，服务会将其解释为技能调用：
- 解析格式：`/skill_name args...`
- 通过技能注册表执行对应技能的 prompt 生成逻辑
- 如果技能调用成功且返回了有效的 prompt 文本，则用该 prompt 替换原始消息
- 如果技能不存在或执行失败，保留原始消息原样发送

**响应** `200 OK`

```json
{
  "session_id": "abc123def456",
  "workspace_dir": "/home/user/.clawd/runtime/workspaces/abc123def456",
  "response": "Here's a Python function to sort a list:\n\n```python\ndef sort_list(lst):\n    return sorted(lst)\n```",
  "usage": {
    "input_tokens": 1500,
    "output_tokens": 300
  },
  "num_turns": 3,
  "history": [
    { "role": "user", "content": "Please help me write a Python function to sort a list" },
    { "role": "assistant", "content": [{"type": "text", "text": "I'll help you write..."}, {"type": "tool_use", "id": "...", "name": "Bash", "input": {...}}] },
    { "role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": false}] },
    { "role": "assistant", "content": "最终回复文本..." }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | `string` | 会话 ID（新建或传入的） |
| `workspace_dir` | `string` | 该会话的工作空间目录绝对路径。Agent 的文件操作（Read、Write、Bash 等）在该目录下执行 |
| `response` | `string` | Agent 的最终文本回复 |
| `usage` | `object \| null` | Token 使用量统计，包含 `input_tokens` 和 `output_tokens` |
| `num_turns` | `int` | 实际消耗的 Agent Loop 轮次 |
| `history` | `array` | 本轮完整的对话历史（Anthropic API 格式）。包含用户消息、助手消息（含工具调用块）、工具结果消息等 |

**错误码**
- `400` — session_id 无效、provider 未配置 API Key

**并发控制**: 同一 `session_id` 的请求使用线程锁串行化，避免并发写会话文件产生竞态。

---

### 4.5 会话历史

#### `GET /sessions/{session_id}/history`

获取指定会话的完整对话历史与元信息。

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | `string` | 会话 ID |

**响应** `200 OK`

```json
{
  "session_id": "abc123def456",
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "workspace_dir": "/home/user/.clawd/runtime/workspaces/abc123def456",
  "created_at": "2026-06-08T10:30:00Z",
  "updated_at": "2026-06-08T10:35:00Z",
  "history": [
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | `string` | 会话 ID |
| `provider` | `string` | 使用的 LLM 提供商 |
| `model` | `string` | 使用的模型名称 |
| `workspace_dir` | `string` | 该会话的工作空间目录 |
| `created_at` | `string` | 会话创建时间（UTC, ISO 8601） |
| `updated_at` | `string` | 会话最后更新时间（UTC, ISO 8601） |
| `history` | `array` | 完整的对话历史消息列表 |

**错误码**
- `400` — session_id 无效
- `404` — session 不存在

---

## 5. 会话生命周期

```
新会话 (session_id = null 或不传)
   │
   ▼
POST /chat  ─────────────────────────────────────────────┐
   │  创建 workspace 目录 + 新 RuntimeSession             │
   │  持久化到 ~/.clawd/runtime/sessions/<id>.json        │
   │                                                     │
   ▼                                                     │
后续 POST /chat (带已有 session_id)                       │
   │  加载已有 session，追加消息到 conversation            │
   │  运行 Agent Loop                                     │
   │  保存更新后的 session                                │
   │                                                     │
   ▼                                                     │
GET /sessions/{id}/history                               │
   │  只读查询，不修改 session                             │
   │                                                     │
   ▼                                                     │
会话文件持久化在磁盘，服务重启后仍可继续对话                  │
```

**关键细节**:
- 会话使用 JSON 文件持久化，存储在 `~/.clawd/runtime/sessions/<session_id>.json`
- 写入时先写 `.tmp` 文件再 `os.replace` 原子替换，防止写入中断导致数据损坏
- 会话加载采用惰性策略：首条消息到达时才创建工作空间目录
- 历史消息数量受 `config.json` 中 `session.max_history`（默认 100）控制

---

## 6. Conversation 内部通信协议

Agent Runtime 内部使用 Anthropic API 兼容的消息格式进行 Conversation 管理。每条消息结构如下：

### 6.1 用户消息（纯文本）

```json
{ "role": "user", "content": "用户输入的文本" }
```

### 6.2 用户消息（含工具结果）

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_xxxx",
      "content": "命令执行输出...",
      "is_error": false
    }
  ]
}
```

### 6.3 助手消息（文本回复）

```json
{ "role": "assistant", "content": "最终文本回复" }
```

### 6.4 助手消息（含工具调用）

```json
{
  "role": "assistant",
  "content": [
    { "type": "text", "text": "Let me check that..." },
    {
      "type": "tool_use",
      "id": "toolu_xxxx",
      "name": "Bash",
      "input": { "command": "ls", "description": "List files" }
    }
  ]
}
```

---

## 7. 安全机制

### 7.1 路径穿越防护

所有涉及文件路径的操作（技能安装、session/workspace 访问）都使用 `_ensure_child()` 验证目标路径是配置目录的子路径，防止路径穿越攻击。

### 7.2 Zip 安全

技能上传的 zip 处理包含多层防护：
- 拒绝绝对路径和含 `..` 的路径
- 拒绝符号链接
- 限制文件大小、数量和总解压大小
- 强制要求恰好一个 `SKILL.md` 作为技能根

### 7.3 权限策略

Agent Runtime 模式下，所有工具调用权限请求默认**拒绝**（`_deny_permission_request` 返回 `(False, False)`），确保 Agent 在无人值守环境下不会执行危险操作。如需放宽权限，需修改 `AgentRuntimeService._deny_permission_request` 方法。

### 7.4 输入验证

- `session_id` 和 `skill_name` 均做严格的字符白名单校验
- `max_turns` 限定在 1–100 之间
- 消息长度至少 1 个字符

---

## 8. 配置与 Provider

服务启动时读取 `~/.clawd/config.json` 中的配置：

```json
{
  "default_provider": "anthropic",
  "providers": {
    "anthropic": {
      "api_key": "sk-ant-...",
      "base_url": "https://api.anthropic.com",
      "default_model": "claude-sonnet-4-6"
    },
    "openai": {
      "api_key": "sk-...",
      "base_url": "https://api.openai.com/v1",
      "default_model": "gpt-4o"
    }
  },
  "session": {
    "auto_save": true,
    "max_history": 100,
    "auto_compact_enabled": true,
    "auto_compact_buffer_tokens": 10000,
    "tool_result_truncation_enabled": true
  }
}
```

支持多种 Provider：Anthropic、OpenAI、智谱 GLM、MiniMax 以及 OpenAI 兼容接口。

---

## 9. Web 控制台

内置的 Web 控制台（`GET /`）提供：

- **Health 面板**: 查看 Provider 配置状态、Skills/Runtime/Workspace 路径
- **Skills 面板**: 查看已安装技能列表、上传/安装新技能（支持 zip 上传和覆盖开关）
- **Chat 面板**: 交互式对话测试，支持设置 max_turns、新建会话、查看历史对话

静态资源路径：`/static/app.css`、`/static/app.js`

---

## 10. 启动方式

### 10.1 命令行

```bash
python -m src.server.app
```

### 10.2 Docker

```bash
docker build -t clawd-runtime .
docker run -p 31366:31366 \
  -e CLAWD_SKILLS_DIR=/data/skills \
  -e CLAWD_RUNTIME_ROOT=/data/runtime \
  -v /host/config:/root/.clawd \
  clawd-runtime
```

### 10.3 编程调用

```python
from src.server.app import AgentRuntimeService

service = AgentRuntimeService()

# 发送消息
response = service.chat(ChatRequest(message="Hello!"))
print(response.response)
print(response.session_id)

# 继续对话
response2 = service.chat(ChatRequest(
    message="What did I just ask?",
    session_id=response.session_id,
))
```
