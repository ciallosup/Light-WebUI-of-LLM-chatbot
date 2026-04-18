import json
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


async def chat_completion(history: List[Dict[str, Any]], message: str, file_contexts: List[str], images: List[Dict[str, str]]) -> str:
    return await chat_completion_with_model(history, message, file_contexts, images, model=None, system_prompt=None)


async def chat_completion_with_model(
    history: List[Dict[str, Any]],
    message: str,
    file_contexts: List[str],
    images: List[Dict[str, str]],
    model: str | None,
    system_prompt: str | None,
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

    payload = {
        "model": selected_model,
        "messages": messages,
        "temperature": 0.7,
    }
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    timeout = httpx.Timeout(
        connect=15.0,
        read=max(1.0, float(settings.llm_timeout_sec)),
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
            f"LLM request timeout after {settings.llm_timeout_sec}s when calling model '{selected_model}'"
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
        msg = data["choices"][0].get("message", {})
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return "\n".join([t for t in texts if t])

    # 兜底
    return str(data)


def _extract_text_from_openai_chunk(data: Dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""

    c0 = choices[0] if isinstance(choices[0], dict) else {}
    delta = c0.get("delta") if isinstance(c0, dict) else None
    if isinstance(delta, dict):
        content = delta.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [x.get("text", "") for x in content if isinstance(x, dict)]
            return "".join([t for t in texts if t])

    msg = c0.get("message") if isinstance(c0, dict) else None
    if isinstance(msg, dict):
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [x.get("text", "") for x in content if isinstance(x, dict)]
            return "".join([t for t in texts if t])

    return ""


async def stream_chat_completion_with_model(
    history: List[Dict[str, Any]],
    message: str,
    file_contexts: List[str],
    images: List[Dict[str, str]],
    model: str | None,
    system_prompt: str | None,
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

    timeout = httpx.Timeout(
        connect=15.0,
        read=max(1.0, float(settings.llm_timeout_sec)),
        write=30.0,
        pool=15.0,
    )

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
                        out = _extract_text_from_openai_chunk(data)
                        if out:
                            yield out
                        else:
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

                    delta_text = _extract_text_from_openai_chunk(data)
                    if delta_text:
                        yield delta_text
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"LLM request timeout after {settings.llm_timeout_sec}s when calling model '{selected_model}'"
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
