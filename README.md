# Light WebUI of LLM Chatbot

中文 | [English](#english)

一个本地运行的轻量级 LLM WebUI：后端使用 FastAPI，前端为原生 HTML/CSS/JS，支持第三方兼容 OpenAI 的 Chat Completions API。

## 功能特性（中文）

- 聊天对话：文本发送、会话创建/切换/删除
- 流式输出：SSE 实时返回模型增量内容
- 富文本渲染：Markdown + KaTeX 数学公式
- 多模态输入：
  - 文本文件上传（`.txt/.md/.pdf/.docx`）并提取内容作为上下文
  - 图片上传与剪贴板粘贴上传
- 历史能力：会话搜索（标题 + 消息内容）
- 模型能力：
  - 运行时切换模型（无需改 `.env`）
  - 支持预配置可选模型列表
- Prompt 管理：支持保存全局 System Prompt
- UI 个性化：
  - 聊天背景图 URL / 本地上传并持久化
  - 侧边栏与面板折叠状态持久化
  - 折叠图标/文案支持前端配置化
- 稳定性增强（近期）：
  - 流式回复在异常中断时也会尽量落库（保留 partial 内容）
  - 流式消息支持耗时统计（`latency_ms`）并在前端展示
  - 智能自动滚动：手动上滚查看历史时不再被强制拉回底部
  - 流式请求与会话绑定隔离：切换会话时不串流、不污染当前会话
  - 新会话在首轮回复完成后自动生成标题（避免长期“新对话”）

## 技术栈

- **Backend**: FastAPI, SQLAlchemy, SQLite
- **Frontend**: Vanilla JS, HTML, CSS
- **LLM Client**: 兼容 OpenAI Chat Completions 的第三方 API

## 快速开始

### 1) 安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2) 配置环境变量

```bash
copy .env.example .env
```

然后编辑 `.env`（不要提交到仓库）：

- `LLM_BASE_URL`：第三方 API Base URL
- `LLM_API_KEY`：API Key
- `LLM_MODEL`：默认模型
- `LLM_MODELS`：前端下拉可选模型（逗号分隔）
- `LLM_CHAT_PATH`：默认 `/v1/chat/completions`
- `LLM_TIMEOUT_SEC`：请求超时秒数（慢模型建议调大）
- `MAX_UPLOAD_MB`：上传大小限制（MB）

### 3) 启动服务

```bash
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器访问：<http://127.0.0.1:8000>

## UI 配置：折叠图标/文案自定义

在 `frontend/app.js` 中编辑 `uiConfig`：

```js
const uiConfig = {
  sidebarToggle: {
    collapsed: '☰',
    expanded: '⇔',
  },
  panelToggle: {
    collapsed: '展开',
    expanded: '折叠',
  },
};
```

说明：
- `sidebarToggle`: 控制左侧边栏折叠按钮图标
- `panelToggle`: 控制“背景自定义 / System Prompt”面板折叠按钮文案

## 主要 API（简要）

- `POST /api/chat/send`：非流式聊天
- `POST /api/chat/stream`：流式聊天（SSE）
- `GET /api/conversations`：会话列表
- `POST /api/conversations`：新建会话
- `DELETE /api/conversations/{id}`：删除会话
- `GET /api/conversations/search?q=...`：会话搜索
- `GET /api/settings/models`：获取模型列表与当前模型
- `PUT /api/settings/model`：更新当前模型
- `GET /api/settings/system-prompt`：读取系统提示词
- `PUT /api/settings/system-prompt`：保存系统提示词
- `POST /api/upload/file|image|background`：上传文本/图片/背景

## 注意事项

- `.env`、`chat.db`、`uploads/` 默认已在 `.gitignore` 中，避免敏感信息和本地数据泄露。
- 未配置完整 LLM 参数时，后端可返回模拟回复用于联调。

---

## English

Lightweight local WebUI for LLM chat. The backend is built with FastAPI and the frontend is plain HTML/CSS/JavaScript. It works with third-party APIs compatible with OpenAI Chat Completions.

### Features

- Chat basics: send messages, create/switch/delete conversations
- Streaming output via SSE
- Rich rendering: Markdown + KaTeX math
- Multimodal input:
  - Text file upload (`.txt/.md/.pdf/.docx`) with extracted context
  - Image upload and clipboard paste
- Conversation search (title + message content)
- Runtime model switching (without editing `.env`)
- Global System Prompt save/load
- UI personalization:
  - Custom chat background (URL/upload + persistence)
  - Collapsed state persistence for sidebar/panels
  - Configurable collapse icons/labels
- Recent reliability updates:
  - Streaming replies persist partial content on abnormal interruption
  - Reply latency (`latency_ms`) is recorded and shown in UI
  - Smart auto-scroll: no forced jump to bottom while user is reading history
  - Stream-to-conversation isolation to avoid cross-conversation rendering
  - Auto title generation after the first completed assistant reply

### Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Open: <http://127.0.0.1:8000>

### Environment Variables

- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
- `LLM_MODELS` (comma-separated model list for UI)
- `LLM_CHAT_PATH` (default: `/v1/chat/completions`)
- `LLM_TIMEOUT_SEC`, `MAX_UPLOAD_MB`, `HOST`, `PORT`

### UI Config (Collapse Icons/Labels)

Edit `uiConfig` in `frontend/app.js`:

```js
const uiConfig = {
  sidebarToggle: { collapsed: '☰', expanded: '⇔' },
  panelToggle: { collapsed: 'Expand', expanded: 'Collapse' },
};
```

You can replace icons/text to match your preferred style.
