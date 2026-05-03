import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.db import SessionLocal, get_session
from backend.app.models import Conversation, Message
from backend.app.schemas import ChatSendRequest, ChatSendResponse
from backend.app.services.history import build_history_messages
from backend.app.services.llm_client import (
    chat_completion_with_model,
    stream_chat_completion_with_model,
    strip_thinking,
)
from backend.app.services.runtime_settings import get_runtime_model, get_system_prompt


router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)
settings = get_settings()

_TITLE_MAX_LEN = 24
_TITLE_MIN_LEN = 2
# 标题生成喂给 LLM 的助手 snippet 长度（更短=更省 token；标题只需主题，不需要全文）。
_TITLE_USER_SNIPPET_LIMIT = 200
_TITLE_ASSISTANT_SNIPPET_LIMIT = 120
_TITLE_BANNED_TERMS = {"新对话", "聊天记录", "对话记录", "会话记录"}
_HEARTBEAT_EVENT = {"event": "heartbeat"}


def _first_line(text: str) -> str:
    return (text or "").splitlines()[0].strip()


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _strip_title_noise(text: str) -> str:
    title = _normalize_text(text)
    for prefix in ("标题：", "标题:", "title:", "Title:"):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
    title = title.strip('"\'“”‘’`')
    while title and title[-1] in "。！？；，,.!?;:：":
        title = title[:-1].rstrip()
    return title


def _normalize_title_candidate(raw_title: str) -> str:
    title = _first_line(raw_title)
    title = _strip_title_noise(title)
    if len(title) > _TITLE_MAX_LEN:
        title = title[:_TITLE_MAX_LEN].rstrip()
    return title


def _is_valid_title(title: str) -> bool:
    if not title:
        return False
    if len(title) < _TITLE_MIN_LEN:
        return False
    if title in _TITLE_BANNED_TERMS:
        return False
    return True


def _clip_for_title(text: str, limit: int) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()


def _title_from_user_message(user_text: str) -> str:
    """从用户首条消息直接派生标题（零 token 成本）。

    取首行，去除标点尾巴，截断到 _TITLE_MAX_LEN。
    """
    first = _first_line(user_text)
    title = _strip_title_noise(first)
    if len(title) > _TITLE_MAX_LEN:
        title = title[:_TITLE_MAX_LEN].rstrip()
    return title


def _fallback_conversation_title(user_text: str, assistant_text: str) -> str:
    user_title = _title_from_user_message(user_text)
    if _is_valid_title(user_title):
        return user_title
    cleaned_assistant = strip_thinking(assistant_text)
    asst_title = _normalize_title_candidate(cleaned_assistant)
    if _is_valid_title(asst_title) and not asst_title.startswith("[本地模拟回复]"):
        return asst_title
    return "新对话"


async def _llm_generate_title(user_text: str, assistant_text: str) -> str:
    """单次轻量 LLM 调用生成标题。

    省 token 设计：
      - 优先使用 LLM_TITLE_MODEL（轻量模型），未配置则回退到主模型；
      - 助手 snippet 强制 strip <thinking>，并截短到 120 字；
      - max_tokens=32，temperature=0.3；
      - 单次请求超时 LLM_TITLE_TIMEOUT_SEC（默认 20s），不阻塞主对话超时预算。
    """
    cleaned_assistant = strip_thinking(assistant_text)
    prompt = (
        "请基于这段对话生成一个简洁中文标题。要求：\n"
        "1) 6~16 字；2) 不要标点结尾；3) 不能出现'新对话'、'聊天记录'等空泛词；\n"
        "4) 仅输出标题文本本身，不要解释、不要引号。\n\n"
        f"用户：{_clip_for_title(user_text, _TITLE_USER_SNIPPET_LIMIT)}\n"
        f"助手：{_clip_for_title(cleaned_assistant, _TITLE_ASSISTANT_SNIPPET_LIMIT)}"
    )
    title_model = (settings.llm_title_model or settings.llm_model or "").strip() or None
    return await chat_completion_with_model(
        history=[],
        message=prompt,
        file_contexts=[],
        images=[],
        model=title_model,
        system_prompt="你是一个擅长给对话起标题的助手。仅输出标题本身。",
        max_tokens=32,
        temperature=0.3,
        request_timeout_sec=settings.llm_title_timeout_sec,
    )


async def _generate_title(user_text: str, assistant_text: str) -> str:
    """生成会话标题。

    策略：优先用 LLM 轻量调用；失败/无效则直接用用户首条消息截断。
    与旧版相比：
      - 不再做"严格重试第二次"，最多 1 次 LLM 调用；
      - 助手内容会先 strip <thinking>；
      - 失败兜底改为"用户消息截断"，不再返回'新对话'。
    """
    try:
        raw_title = await asyncio.wait_for(
            _llm_generate_title(user_text, assistant_text),
            timeout=max(5.0, float(settings.llm_title_timeout_sec) + 5.0),
        )
        title = _normalize_title_candidate(raw_title)
        if _is_valid_title(title):
            return title
        logger.info("conversation_title_candidate_rejected title=%s", title)
    except asyncio.TimeoutError:
        logger.warning("conversation_title_generation_timeout")
    except Exception as exc:
        logger.warning("conversation_title_generation_failed err=%s", exc)

    return _fallback_conversation_title(user_text, assistant_text)


async def _update_title_async(conversation_id: int, user_text: str, assistant_text: str) -> None:
    """后台异步更新会话标题，不阻塞主响应。

    在独立 Session 中执行，捕获所有异常以保证主流程不受影响。
    """
    try:
        title = await _generate_title(user_text, assistant_text)
    except Exception as exc:
        logger.warning("conversation_title_async_generate_failed err=%s", exc)
        title = _fallback_conversation_title(user_text, assistant_text)

    if not title:
        return

    persist_session = SessionLocal()
    try:
        conv_row = persist_session.get(Conversation, conversation_id)
        if conv_row and conv_row.title == "新对话":
            conv_row.title = title
            persist_session.commit()
    except Exception as exc:
        persist_session.rollback()
        logger.warning("conversation_title_async_persist_failed err=%s", exc)
    finally:
        persist_session.close()


def _schedule_title_update(conversation_id: int, user_text: str, assistant_text: str) -> None:
    """以 fire-and-forget 方式调度标题更新。

    放在主响应返回之后执行，避免阻塞 SSE done 事件 / 非流式 HTTP 响应。
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_update_title_async(conversation_id, user_text, assistant_text))
    except RuntimeError:
        # 没有事件循环时退化为同步兜底（不会发生在 FastAPI 路由里）
        logger.warning("conversation_title_no_event_loop")


def _build_user_attachments(payload: ChatSendRequest) -> str:
    parts = [
        f"files={len(payload.file_contexts)}",
        f"images={len(payload.images)}",
    ]
    if payload.idempotency_key:
        parts.append(f"idempotency_key={payload.idempotency_key}")
    return ";".join(parts)


def _build_assistant_attachments(latency_ms: int, stream_status: str | None, idempotency_key: str | None) -> str:
    parts = [f"latency_ms={latency_ms}"]
    if stream_status:
        parts.append(f"stream_status={stream_status}")
    # 仅在 completed 状态下写入幂等键，避免 partial 命中导致后续重试卡死。
    if idempotency_key and (stream_status is None or stream_status == "completed"):
        parts.append(f"idempotency_key={idempotency_key}")
    return ";".join(parts)


def _find_existing_assistant_by_idempotency(session: Session, conversation_id: int, idempotency_key: str | None) -> Message | None:
    """查找命中幂等键的助手消息。仅匹配 completed 状态（partial 不参与幂等去重）。"""
    if not idempotency_key:
        return None
    stmt = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.attachments.contains(f"idempotency_key={idempotency_key}"),
        )
        .order_by(Message.id.desc())
    )
    return session.execute(stmt).scalars().first()


def _find_existing_user_by_idempotency(session: Session, conversation_id: int, idempotency_key: str | None) -> Message | None:
    if not idempotency_key:
        return None
    stmt = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "user",
            Message.attachments.contains(f"idempotency_key={idempotency_key}"),
        )
        .order_by(Message.id.desc())
    )
    return session.execute(stmt).scalars().first()


@router.post("/send", response_model=ChatSendResponse)
async def send_chat(payload: ChatSendRequest, request: Request, session: Session = Depends(get_session)):
    request_id = getattr(request.state, "request_id", "n/a")
    if len(payload.message) > settings.max_message_len:
        raise HTTPException(status_code=400, detail=f"Message too long (max {settings.max_message_len})")

    logger.info(
        "chat_send_start request_id=%s conversation_id=%s model=%s idempotency_key=%s",
        request_id,
        payload.conversation_id,
        payload.model or "",
        payload.idempotency_key or "",
    )

    conv = session.get(Conversation, payload.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    existing_assistant = _find_existing_assistant_by_idempotency(session, payload.conversation_id, payload.idempotency_key)
    if existing_assistant:
        existing_user = _find_existing_user_by_idempotency(session, payload.conversation_id, payload.idempotency_key)
        logger.info(
            "chat_send_idempotent_hit request_id=%s conversation_id=%s assistant_message_id=%s",
            request_id,
            payload.conversation_id,
            existing_assistant.id,
        )
        return ChatSendResponse(
            user_message_id=existing_user.id if existing_user else existing_assistant.id,
            assistant_message_id=existing_assistant.id,
            assistant_reply=existing_assistant.content,
        )

    history_stmt = (
        select(Message)
        .where(Message.conversation_id == payload.conversation_id)
        .order_by(Message.created_at.asc())
    )
    history_rows = session.execute(history_stmt).scalars().all()
    # 使用基于 token 预算的滑动窗口替代暴力 [-20:]：
    # - strip 助手 <thinking>，避免回传思维链
    # - 过滤 partial 助手消息
    # - 保证以 user 开头并修剪连续同角色
    # - 保留首条 user（任务初始描述锚点）
    history = build_history_messages(history_rows)
    selected_model = (payload.model or get_runtime_model(session) or "").strip()
    system_prompt = get_system_prompt(session)

    user_msg = Message(
        conversation_id=payload.conversation_id,
        role="user",
        content=payload.message,
        attachments=_build_user_attachments(payload),
    )
    session.add(user_msg)
    session.commit()
    session.refresh(user_msg)
    user_message_id = user_msg.id

    start = time.perf_counter()
    try:
        reply = await asyncio.wait_for(
            chat_completion_with_model(
                history=history,

                message=payload.message,
                file_contexts=payload.file_contexts,
                images=[img.model_dump() for img in payload.images],
                model=selected_model,
                system_prompt=system_prompt,
            ),
            timeout=settings.llm_timeout_sec,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"LLM request timeout after {settings.llm_timeout_sec}s when calling model '{selected_model}'",
        )
    except RuntimeError as exc:
        msg = str(exc)
        elapsed = time.perf_counter() - start
        remaining = settings.llm_timeout_sec - elapsed
        retryable = any(token in msg.lower() for token in ("status 502", "status 504", "status 524", "network error", "timeout"))
        if remaining > 5 and retryable:
            try:
                reply = await asyncio.wait_for(
                    chat_completion_with_model(
                        history=history,
                        message=payload.message,
                        file_contexts=payload.file_contexts,
                        images=[img.model_dump() for img in payload.images],
                        model=selected_model,
                        system_prompt=system_prompt,
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=504,
                    detail=f"LLM request timeout after {settings.llm_timeout_sec}s when calling model '{selected_model}'",
                )
            except RuntimeError as retry_exc:
                msg = str(retry_exc)
                if "timeout" in msg.lower():
                    raise HTTPException(status_code=504, detail=msg)
                raise HTTPException(status_code=502, detail=msg)
        else:
            if "timeout" in msg.lower():
                raise HTTPException(status_code=504, detail=msg)
            raise HTTPException(status_code=502, detail=msg)

    latency_ms = int((time.perf_counter() - start) * 1000)

    ai_msg = Message(
        conversation_id=payload.conversation_id,
        role="assistant",
        content=reply,
        attachments=_build_assistant_attachments(latency_ms, None, payload.idempotency_key),
    )
    session.add(ai_msg)
    session.commit()
    session.refresh(ai_msg)
    assistant_message_id = ai_msg.id

    # 标题生成异步进行，不阻塞 HTTP 响应。
    if conv.title == "新对话":
        _schedule_title_update(payload.conversation_id, payload.message, reply)

    logger.info(
        "chat_send_done request_id=%s conversation_id=%s user_message_id=%s assistant_message_id=%s latency_ms=%s",
        request_id,
        payload.conversation_id,
        user_message_id,
        assistant_message_id,
        latency_ms,
    )
    return ChatSendResponse(
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        assistant_reply=reply,
    )


@router.post("/stream")
async def send_chat_stream(payload: ChatSendRequest, request: Request, session: Session = Depends(get_session)):
    request_id = getattr(request.state, "request_id", "n/a")
    if len(payload.message) > settings.max_message_len:
        raise HTTPException(status_code=400, detail=f"Message too long (max {settings.max_message_len})")

    logger.info(
        "chat_stream_start request_id=%s conversation_id=%s model=%s idempotency_key=%s",
        request_id,
        payload.conversation_id,
        payload.model or "",
        payload.idempotency_key or "",
    )

    conv = session.get(Conversation, payload.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    existing_assistant = _find_existing_assistant_by_idempotency(session, payload.conversation_id, payload.idempotency_key)
    existing_user = _find_existing_user_by_idempotency(session, payload.conversation_id, payload.idempotency_key)

    history_stmt = (
        select(Message)
        .where(Message.conversation_id == payload.conversation_id)
        .order_by(Message.created_at.asc())
    )
    history_rows = session.execute(history_stmt).scalars().all()
    # 与非流式分支保持一致的 token 预算 + thinking strip + partial 过滤策略
    history = build_history_messages(history_rows)
    should_auto_title = conv.title == "新对话"

    selected_model = (payload.model or get_runtime_model(session) or "").strip()
    system_prompt = get_system_prompt(session)

    if existing_assistant:
        user_message_id = existing_user.id if existing_user else existing_assistant.id
    else:
        user_msg = Message(
            conversation_id=payload.conversation_id,
            role="user",
            content=payload.message,
            attachments=_build_user_attachments(payload),
        )
        session.add(user_msg)
        session.commit()
        session.refresh(user_msg)
        user_message_id = user_msg.id

    async def event_generator():
        start = time.perf_counter()
        # 把 assembled_parts 提到主 try 外可见的作用域，并使用 list 引用
        # 以便在客户端断开（asyncio.CancelledError）时仍能把 partial 落库。
        assembled_parts: list[str] = []
        partial_persisted = False  # 防止重复落库

        async def _persist_assistant_reply(reply: str, latency_ms: int, stream_status: str) -> int | None:

            if not reply:
                return None

            persist_session = SessionLocal()
            try:
                ai_msg = Message(
                    conversation_id=payload.conversation_id,
                    role="assistant",
                    content=reply,
                    attachments=_build_assistant_attachments(latency_ms, stream_status, payload.idempotency_key),
                )
                persist_session.add(ai_msg)
                persist_session.commit()
                persist_session.refresh(ai_msg)
                return ai_msg.id
            except Exception as exc:
                persist_session.rollback()
                logger.warning(
                    "assistant_persist_failed_stream request_id=%s conversation_id=%s err=%s",
                    request_id,
                    payload.conversation_id,
                    exc,
                )
                return None
            finally:
                persist_session.close()

        async def _fallback_non_stream_reply(budget_sec: float) -> str:
            """流式空回复时的非流式兜底。

            重点：使用调用方传入的独立 budget_sec，而不是再去算"剩余总预算"，
            避免主预算被流式空转耗尽时 fallback 立刻返回空字符串。
            """
            if budget_sec <= 0:
                return ""
            try:
                return await asyncio.wait_for(
                    chat_completion_with_model(
                        history=history,
                        message=payload.message,
                        file_contexts=payload.file_contexts,
                        images=[img.model_dump() for img in payload.images],
                        model=selected_model,
                        system_prompt=system_prompt,
                        request_timeout_sec=budget_sec,
                    ),
                    timeout=budget_sec,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "chat_stream_fallback_timeout request_id=%s conversation_id=%s budget=%ss",
                    request_id,
                    payload.conversation_id,
                    budget_sec,
                )
                return ""
            except Exception as exc:
                logger.warning(
                    "chat_stream_fallback_failed request_id=%s conversation_id=%s err=%s",
                    request_id,
                    payload.conversation_id,
                    exc,
                )
                return ""

        def _fallback_budget() -> float:
            """计算非流式 fallback 的独立时间预算。

            对慢思维链/无思维链模型，必须保证至少 LLM_FALLBACK_MIN_SEC 秒的兜底时间，
            否则流式空回复时无法真正完成兜底。
            """
            elapsed = time.perf_counter() - start
            remaining_main = settings.llm_timeout_sec - elapsed
            return max(float(settings.llm_fallback_min_sec), float(remaining_main))

        meta = {
            "event": "meta",
            "user_message_id": user_message_id,
            "model": selected_model,
            "request_id": request_id,
        }
        yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"


        if existing_assistant:
            done = {
                "event": "done",
                "assistant_reply": existing_assistant.content,
                "assistant_message_id": existing_assistant.id,
                "latency_ms": 0,
                "cached": True,
            }
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
            logger.info(
                "chat_stream_idempotent_hit request_id=%s conversation_id=%s assistant_message_id=%s",
                request_id,
                payload.conversation_id,
                existing_assistant.id,
            )
            return

        # ====================== 主流式循环 ======================
        # 外层 try 兜底处理"客户端断开"（asyncio.CancelledError / GeneratorExit）：
        # 切换会话/切换模型/重发新消息时前端会 abort 当前流，
        # 这里需要把已经收到的 partial 写入数据库，避免回答消失。
        try:
            try:
                stream_iter = stream_chat_completion_with_model(
                    history=history,
                    message=payload.message,
                    file_contexts=payload.file_contexts,
                    images=[img.model_dump() for img in payload.images],
                    model=selected_model,
                    system_prompt=system_prompt,
                ).__aiter__()

                while True:
                    try:
                        delta = await asyncio.wait_for(stream_iter.__anext__(), timeout=max(1.0, settings.sse_heartbeat_sec))
                    except asyncio.TimeoutError:
                        heartbeat = dict(_HEARTBEAT_EVENT)
                        heartbeat["ts"] = datetime.utcnow().isoformat()
                        yield f"data: {json.dumps(heartbeat, ensure_ascii=False)}\n\n"
                        continue
                    except StopAsyncIteration:
                        break

                    if not delta:
                        continue

                    assembled_parts.append(delta)
                    out = {"event": "delta", "delta": delta}
                    yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"

                reply = "".join(assembled_parts)
                latency_ms = int((time.perf_counter() - start) * 1000)

                # 流式无内容 → fallback 到非流式（独立预算）
                if not reply.strip():
                    logger.info(
                        "chat_stream_empty_invoking_fallback request_id=%s conversation_id=%s",
                        request_id,
                        payload.conversation_id,
                    )
                    fallback_reply = ""
                    budget = _fallback_budget()
                    fallback_task = asyncio.create_task(_fallback_non_stream_reply(budget))
                    while True:
                        done_set, _ = await asyncio.wait({fallback_task}, timeout=max(1.0, settings.sse_heartbeat_sec))
                        if fallback_task in done_set:
                            fallback_reply = fallback_task.result()
                            break
                        heartbeat = dict(_HEARTBEAT_EVENT)
                        heartbeat["ts"] = datetime.utcnow().isoformat()
                        yield f"data: {json.dumps(heartbeat, ensure_ascii=False)}\n\n"

                    if fallback_reply.strip():
                        latency_ms = int((time.perf_counter() - start) * 1000)
                        # 用 shield 保护落库不被取消（Starlette 会在客户端断开时取消 generator 任务）
                        assistant_message_id = await asyncio.shield(
                            _persist_assistant_reply(fallback_reply, latency_ms, "completed")
                        )
                        partial_persisted = True
                        done = {
                            "event": "done",
                            "assistant_reply": fallback_reply,
                            "assistant_message_id": assistant_message_id,
                            "latency_ms": latency_ms,
                        }
                        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                        if should_auto_title:
                            _schedule_title_update(payload.conversation_id, payload.message, fallback_reply)
                        logger.info(
                            "chat_stream_fallback_done request_id=%s conversation_id=%s assistant_message_id=%s latency_ms=%s",
                            request_id,
                            payload.conversation_id,
                            assistant_message_id,
                            latency_ms,
                        )
                        return

                    latency_ms = int((time.perf_counter() - start) * 1000)
                    done = {
                        "event": "done",
                        "assistant_reply": "",
                        "assistant_message_id": None,
                        "latency_ms": latency_ms,
                        "empty_reply": True,
                    }
                    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                    logger.warning(
                        "chat_stream_empty_reply request_id=%s conversation_id=%s model=%s latency_ms=%s",
                        request_id,
                        payload.conversation_id,
                        selected_model,
                        latency_ms,
                    )
                    return

                # 正常完成
                assistant_message_id = await asyncio.shield(
                    _persist_assistant_reply(reply, latency_ms, "completed")
                )
                partial_persisted = True
                done = {
                    "event": "done",
                    "assistant_reply": reply,
                    "assistant_message_id": assistant_message_id,
                    "latency_ms": latency_ms,
                }
                yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                # 标题生成异步进行，不阻塞 done 事件
                if should_auto_title:
                    _schedule_title_update(payload.conversation_id, payload.message, reply)
                logger.info(
                    "chat_stream_done request_id=%s conversation_id=%s assistant_message_id=%s latency_ms=%s",
                    request_id,
                    payload.conversation_id,
                    assistant_message_id,
                    latency_ms,
                )
            except (RuntimeError, Exception) as exc:
                msg = str(exc)
                reply = "".join(assembled_parts)

                # 没有任何流式内容 → 尝试非流式 fallback（独立预算）
                if not reply.strip():
                    fallback_reply = ""
                    budget = _fallback_budget()
                    logger.info(
                        "chat_stream_error_invoking_fallback request_id=%s conversation_id=%s err=%s budget=%.1fs",
                        request_id,
                        payload.conversation_id,
                        msg,
                        budget,
                    )
                    try:
                        fallback_task = asyncio.create_task(_fallback_non_stream_reply(budget))
                        while True:
                            done_set, _ = await asyncio.wait({fallback_task}, timeout=max(1.0, settings.sse_heartbeat_sec))
                            if fallback_task in done_set:
                                fallback_reply = fallback_task.result()
                                break
                            heartbeat = dict(_HEARTBEAT_EVENT)
                            heartbeat["ts"] = datetime.utcnow().isoformat()
                            yield f"data: {json.dumps(heartbeat, ensure_ascii=False)}\n\n"
                    except Exception as fb_exc:
                        logger.warning(
                            "chat_stream_fallback_outer_error request_id=%s conversation_id=%s err=%s",
                            request_id,
                            payload.conversation_id,
                            fb_exc,
                        )

                    if fallback_reply.strip():
                        latency_ms = int((time.perf_counter() - start) * 1000)
                        assistant_message_id = await asyncio.shield(
                            _persist_assistant_reply(fallback_reply, latency_ms, "completed")
                        )
                        partial_persisted = True
                        done = {
                            "event": "done",
                            "assistant_reply": fallback_reply,
                            "assistant_message_id": assistant_message_id,
                            "latency_ms": latency_ms,
                        }
                        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                        if should_auto_title:
                            _schedule_title_update(payload.conversation_id, payload.message, fallback_reply)
                        logger.info(
                            "chat_stream_fallback_done_after_error request_id=%s conversation_id=%s assistant_message_id=%s latency_ms=%s",
                            request_id,
                            payload.conversation_id,
                            assistant_message_id,
                            latency_ms,
                        )
                        return

                # partial 内容存在 → 落库 partial（不写 idempotency_key）
                latency_ms = int((time.perf_counter() - start) * 1000)
                assistant_message_id = None
                if reply:
                    assistant_message_id = await asyncio.shield(
                        _persist_assistant_reply(reply, latency_ms, "partial")
                    )
                    partial_persisted = True
                err = {
                    "event": "error",
                    "detail": msg,
                    "assistant_message_id": assistant_message_id,
                    "latency_ms": latency_ms,
                }
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                logger.warning(
                    "chat_stream_error request_id=%s conversation_id=%s err=%s latency_ms=%s",
                    request_id,
                    payload.conversation_id,
                    msg,
                    latency_ms,
                )
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端中断（前端 abort）。此时不能 yield，但仍应把 partial 落库。
            if not partial_persisted and assembled_parts:
                reply = "".join(assembled_parts)
                if reply.strip():
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    try:
                        # shield 让落库的 task 不被外层取消传播打断；
                        # 注意：generator 已经在被 close，shield 内部仍能完成。
                        await asyncio.shield(
                            _persist_assistant_reply(reply, latency_ms, "partial")
                        )
                        logger.info(
                            "chat_stream_persisted_on_disconnect request_id=%s conversation_id=%s chars=%s latency_ms=%s",
                            request_id,
                            payload.conversation_id,
                            len(reply),
                            latency_ms,
                        )
                    except Exception as exc:
                        logger.warning(
                            "chat_stream_persist_on_disconnect_failed request_id=%s conversation_id=%s err=%s",
                            request_id,
                            payload.conversation_id,
                            exc,
                        )
            # 重新抛出以让 Starlette 正常清理
            raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")


