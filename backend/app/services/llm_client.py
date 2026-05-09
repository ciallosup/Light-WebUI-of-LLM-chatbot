import json
import re
from typing import List, Dict, Any, AsyncIterator

import httpx

from backend.app.config import get_settings


def _build_user_content(message: str, file_contexts: List[str], images: List[Dict[str, str]]) -> Any:
    has_images = len(images) > 0
    has_files = len(file_contexts) > 0

    if not has_images:
        if has_files:
            file_text = "\n\n".join([f"[文件片段 {i+1}]\n{t}" for i, t in enumerate(file_contexts)])
            return f"{message}\n\n以下是用户上传的文件内容参考：\n{file_text}"
        return message

    content = [{"type": "text", "text": message}]
    for i, text in enumerate(file_contexts):
        content.append({"type": "text", "text": f"[文件片段 {i+1}]\n{text}"})
    for img in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{img['mime_type']};base64,{img['data_base64']}"},
            }
        )
    return content


def _join_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for x in value:
            if not isinstance(x, dict):
                continue
            if isinstance(x.get("text"), str):
                parts.append(x.get("text", ""))
            elif isinstance(x.get("content"), str):
                parts.append(x.get("content", ""))
        return "".join([p for p in parts if p])
    return ""


_THINKING_TAG_RE = re.compile(r"<thinking>[\s\S]*?</thinking>", re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """从 raw 助手内容中移除 <thinking>...</thinking> 包裹，仅保留正文。

    用于喂给二次调用的 LLM（例如标题生成），避免把推理链当成上下文浪费 token。
    """
    if not text:
        return ""
    cleaned = _THINKING_TAG_RE.sub("", text)
    return cleaned.strip()


def _wrap_reasoning(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    return f"<thinking>{cleaned}</thinking>"


async def chat_completion(history: List[Dict[str, Any]], message: str, file_contexts: List[str], images: List[Dict[str, str]]) -> str:
    return await chat_completion_with_model(history, message, file_contexts, images, model=None, system_prompt=None)


async def chat_completion_with_model(
    history: List[Dict[str, Any]],
    message: str,
    file_contexts: List[str],
    images: List[Dict[str, str]],
    model: str | None,
    system_prompt: str | None,
    *,
    max_tokens: int | None = None,
    temperature: float = 0.7,
    request_timeout_sec: float | None = None,
) -> str:
    settings = get_settings()
    selected_model = (model or settings.llm_model or "").strip()

    # 无配置时提供本地回显，便于先联调前后端
    if not settings.llm_base_url or not selected_model:
        return f"[本地模拟回复] 你说的是：{message}"

    url = f"{settings.llm_base_url.rstrip('/')}{settings.llm_chat_path}"

    messages: List[Dict[str, Any]] = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})

    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})

    messages.append(
        {
            "role": "user",
            "content": _build_user_content(message, file_contexts, images),
        }
    )

    payload: Dict[str, Any] = {
        "model": selected_model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens and max_tokens > 0:
        payload["max_tokens"] = int(max_tokens)
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    # read 超时只控制单次 HTTP 请求；外层会有自己的业务总预算 wait_for。
    read_timeout = float(request_timeout_sec) if (request_timeout_sec and request_timeout_sec > 0) else float(settings.llm_timeout_sec)
    timeout = httpx.Timeout(
        connect=15.0,
        read=max(1.0, read_timeout),
        write=30.0,
        pool=15.0,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"LLM request timeout after {read_timeout}s when calling model '{selected_model}'"
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        if exc.response is not None:
            try:
                raw = await exc.response.aread()
                body = raw.decode("utf-8", errors="ignore")[:300]
            except Exception:
                body = ""
        raise RuntimeError(f"LLM upstream returned status {status}: {body}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM network error: {exc}") from exc

    # 兼容 OpenAI 风格
    if "choices" in data and data["choices"]:
        choice0 = data["choices"][0] if isinstance(data["choices"][0], dict) else {}
        msg = choice0.get("message", {})
        reasoning = ""
        if isinstance(msg, dict):
            if isinstance(msg.get("reasoning_content"), str):
                reasoning = msg.get("reasoning_content", "")
            elif isinstance(msg.get("reasoning"), str):
                reasoning = msg.get("reasoning", "")

        content = _join_content(msg.get("content", "")) if isinstance(msg, dict) else ""
        wrapped_reasoning = _wrap_reasoning(reasoning) if reasoning else ""
        if wrapped_reasoning and content:
            return f"{wrapped_reasoning}\n{content}"
        if wrapped_reasoning:
            return wrapped_reasoning
        if content:
            return content

        # choices 存在但 content 与 reasoning 均为空（部分模型如 gemini-pro 在某些
        # 情况下会返回空 content）——抛出有意义的异常，让上层（标题生成等）走 fallback，
        # 而不是把整个 JSON dict 转成字符串当作回复内容。
        finish_reason = choice0.get("finish_reason", "unknown")
        raise RuntimeError(
            f"LLM returned empty content (finish_reason={finish_reason!r}). "
            f"model={data.get('model', 'unknown')!r}"
        )

    # 真正的兜底：响应中没有 choices 字段（非 OpenAI 格式）
    raise RuntimeError(
        f"LLM response missing 'choices' field. "
        f"keys={list(data.keys())}, model={data.get('model', 'unknown')!r}"
    )



def _extract_stream_pieces(data: Dict[str, Any]) -> tuple[str, str]:
    """从一个 OpenAI 风格的 SSE chunk 提取 (reasoning_delta, content_delta)。

    注意：同一个 chunk 可能同时包含 reasoning 与 content（例如 DeepSeek-Reasoner
    在思考结束→正文开始的那一帧），原实现只 return 其中一个会丢字。
    """
    choices = data.get("choices") or []
    if not choices:
        # 兼容部分供应商的非 OpenAI 字段
        if isinstance(data.get("output_text"), str):
            return "", data.get("output_text", "")
        if isinstance(data.get("text"), str):
            return "", data.get("text", "")
        return "", ""

    c0 = choices[0] if isinstance(choices[0], dict) else {}

    reasoning_piece = ""
    content_piece = ""

    delta = c0.get("delta") if isinstance(c0, dict) else None
    if isinstance(delta, dict):
        for key in ("reasoning_content", "reasoning"):
            v = delta.get(key)
            if isinstance(v, str) and v:
                reasoning_piece = v
                break

        out = _join_content(delta.get("content"))
        if out:
            content_piece = out
        else:
            for key in ("text", "output_text"):
                v = delta.get(key)
                if isinstance(v, str) and v:
                    content_piece = v
                    break

    if not reasoning_piece and not content_piece:
        msg = c0.get("message") if isinstance(c0, dict) else None
        if isinstance(msg, dict):
            for key in ("reasoning_content", "reasoning"):
                v = msg.get(key)
                if isinstance(v, str) and v:
                    reasoning_piece = v
                    break
            out = _join_content(msg.get("content"))
            if out:
                content_piece = out
            else:
                for key in ("text", "output_text"):
                    v = msg.get(key)
                    if isinstance(v, str) and v:
                        content_piece = v
                        break

    if not reasoning_piece and not content_piece and isinstance(c0.get("text"), str):
        content_piece = c0.get("text", "")

    return reasoning_piece, content_piece


async def stream_chat_completion_with_model(
    history: List[Dict[str, Any]],
    message: str,
    file_contexts: List[str],
    images: List[Dict[str, str]],
    model: str | None,
    system_prompt: str | None,
    *,
    chunk_idle_timeout_sec: float | None = 90.0,
) -> AsyncIterator[str]:
    settings = get_settings()
    selected_model = (model or settings.llm_model or "").strip()

    if not settings.llm_base_url or not selected_model:
        text = f"[本地模拟回复] 你说的是：{message}"
        for ch in text:
            yield ch
        return

    url = f"{settings.llm_base_url.rstrip('/')}{settings.llm_chat_path}"

    messages: List[Dict[str, Any]] = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append(
        {
            "role": "user",
            "content": _build_user_content(message, file_contexts, images),
        }
    )

    payload = {
        "model": selected_model,
        "messages": messages,
        "temperature": 0.7,
        "stream": True,
    }
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    # 流式：read 用 chunk 间最大空闲间隔为上限（默认 90s），避免上游不断流但永不发数据时无限挂起。
    # 外层心跳和总预算由调用方控制。
    read_timeout: float | None
    if chunk_idle_timeout_sec is None or chunk_idle_timeout_sec <= 0:
        read_timeout = None
    else:
        read_timeout = float(chunk_idle_timeout_sec)
    timeout = httpx.Timeout(
        connect=15.0,
        read=read_timeout,
        write=30.0,
        pool=15.0,
    )

    # 思考链状态机：跨多个增量 chunk 时只发一对 <thinking>...</thinking>，避免被切碎。
    thinking_open = False
    thinking_closed = False

    def _open_thinking_if_needed() -> str:
        nonlocal thinking_open
        if thinking_open or thinking_closed:
            return ""
        thinking_open = True
        return "<thinking>"

    def _close_thinking_if_needed() -> str:
        nonlocal thinking_open, thinking_closed
        if not thinking_open or thinking_closed:
            return ""
        thinking_closed = True
        thinking_open = False
        return "</thinking>\n"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                content_type = (resp.headers.get("content-type") or "").lower()

                if "text/event-stream" not in content_type:
                    raw = await resp.aread()
                    text = raw.decode("utf-8", errors="ignore")
                    try:
                        data = json.loads(text)
                        reasoning_piece, content_piece = _extract_stream_pieces(data)
                        if reasoning_piece:
                            yield f"<thinking>{reasoning_piece}</thinking>\n"
                        if content_piece:
                            yield content_piece
                        if not reasoning_piece and not content_piece:
                            # 兼容非标准 JSON 响应
                            yield str(data)
                    except json.JSONDecodeError:
                        if text:
                            yield text
                    return

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue

                    payload_text = line[5:].strip()
                    if not payload_text:
                        continue
                    if payload_text == "[DONE]":
                        break

                    try:
                        data = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue

                    reasoning_piece, content_piece = _extract_stream_pieces(data)

                    # 同一帧可能同时含 reasoning 和 content：先发 reasoning（含闭合），再发 content。
                    if reasoning_piece:
                        prefix = _open_thinking_if_needed()
                        yield f"{prefix}{reasoning_piece}"
                    if content_piece:
                        # 一旦出现正文，就关闭未闭合的思考块
                        suffix = _close_thinking_if_needed()
                        if suffix:
                            yield suffix
                        yield content_piece

                # 流结束时若仍有未闭合的思考块，补上闭合标签
                tail = _close_thinking_if_needed()
                if tail:
                    yield tail
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"LLM stream chunk idle timeout when calling model '{selected_model}'"
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        if exc.response is not None:
            try:
                raw = await exc.response.aread()
                body = raw.decode("utf-8", errors="ignore")[:300]
            except Exception:
                body = ""
        raise RuntimeError(f"LLM upstream returned status {status}: {body}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM network error: {exc}") from exc
