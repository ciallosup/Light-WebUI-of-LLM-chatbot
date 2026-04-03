# Local LLM Web Chat (Python + FastAPI)

## 功能
- 文本输入发送
- 流式输出开关（SSE）
- 富文本渲染（Markdown）+ 数学公式渲染（KaTeX）
- 文件上传（txt/md/pdf/docx）
- 图片上传 / 粘贴上传
- 文件上传按钮支持图片与文本文件混传
- 会话创建、切换、删除
- 历史会话搜索
- 模型切换（运行时切换，无需改 `.env`）
- 首轮消息智能标题总结（LLM 优先，规则兜底）
- 自定义聊天背景（URL/本地上传，自动本地持久化）

## 1. 安装依赖
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2. 配置环境变量
```bash
copy .env.example .env
```
编辑 `.env`，填入第三方 LLM API 的 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`。  
可选：配置 `LLM_MODELS=model_a,model_b,model_c` 作为前端可切换模型列表。
可选：配置 `LLM_TIMEOUT_SEC=180`（秒），用于慢模型（如 GPT5.4）避免本地请求过早超时。

## 3. 启动服务
```bash
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开：
- http://127.0.0.1:8000

## 说明
- 若未配置 LLM 地址/模型，后端会返回“本地模拟回复”用于联调。
- 当前数据库为本地 `chat.db`（SQLite）。

## 新增接口（第二阶段）
- `GET /api/settings/models`：获取可选模型与当前模型
- `PUT /api/settings/model`：更新当前模型（运行时生效）
- `GET /api/conversations/search?q=关键词`：搜索历史会话（标题 + 消息内容）
- `POST /api/chat/stream`：流式聊天输出（SSE）
