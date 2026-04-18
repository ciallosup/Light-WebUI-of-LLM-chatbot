import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import SessionLocal, get_session
from backend.app.models import Conversation, Message
from backend.app.schemas import ChatSendRequest, ChatSendResponse
from backend.app.services.llm_client import chat_completion_with_model, stream_chat_completion_with_model
from backend.app.services.runtime_settings import get_runtime_model, get_system_prompt


router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

_TITLE_MAX_LEN = 24
_TITLE_MIN_LEN = 4
_TITLE_SNIPPET_LIMIT = 500
_TITLE_BANNED_TERMS = {"新对话", "聊天记录", "对话记录", "会话记录"}


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


def _is_valid_title(title: str, user_text: str) -> bool:
    if not title:
        return False
    if len(title) < _TITLE_MIN_LEN:
        return False
    if title in _TITLE_BANNED_TERMS:
        return False

    user_line = _normalize_title_candidate(user_text)
    if user_line and title == user_line:
        return False

    lowered = title.lower()
    if lowered.startswith("帮我") or lowered.startswith("请你"):
        return False
    return True


def _clip_for_title(text: str) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= _TITLE_SNIPPET_LIMIT:
        return normalized
    return normalized[:_TITLE_SNIPPET_LIMIT].rstrip()


def _fallback_conversation_title(assistant_text: str) -> str:
    normalized = _normalize_title_candidate(assistant_text)
    if not normalized:
        return "新对话"
    if normalized.startswith("[本地模拟回复]"):
        return "新对话"
    if not _is_valid_title(normalized, user_text=""):
        return "新对话"
    return normalized


async def _try_generate_title_once(user_text: str, assistant_text: str, model: str, strict: bool) -> str:
    prompt = (
        "请基于这段对话生成一个简洁中文标题。要求：\n"
        "1) 8~18字；2) 不要标点结尾；3) 不能出现'新对话'、'聊天记录'等空泛词；\n"
        "4) 仅输出标题文本本身，不要解释。"
    )
    if strict:
        prompt += "\n5) 禁止复述用户原句，必须概括主题。"

    prompt += f"\n\n用户：{_clip_for_title(user_text)}\n助手：{_clip_for_title(assistant_text)}"
    return await chat_completion_with_model(
        history=[],
        message=prompt,
        file_contexts=[],
        images=[],
        model=model,
        system_prompt="你是一个擅长给对话起标题的助手。",
    )


async def _smart_conversation_title(user_text: str, assistant_text: str, model: str) -> str:
    for strict in (False, True):
        try:
            raw_title = await _try_generate_title_once(user_text, assistant_text, model, strict=strict)
        except Exception as exc:
            logger.warning("Conversation title generation failed (strict=%s): %s", strict, exc)
            continue

        title = _normalize_title_candidate(raw_title)
        if _is_valid_title(title, user_text=user_text):
            return title

        logger.info("Conversation title candidate rejected: %s", title)

    return _fallback_conversation_title(assistant_text)


@router.post("/send", response_model=ChatSendResponse)
async def send_chat(payload: ChatSendRequest, session: Session = Depends(get_session)):
    conv = session.get(Conversation, payload.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    history_stmt = (
        select(Message)
        .where(Message.conversation_id == payload.conversation_id)
        .order_by(Message.created_at.asc())
    )
    history_rows = session.execute(history_stmt).scalars().all()
    history = [{"role": h.role, "content": h.content} for h in history_rows][-20:]
    selected_model = (payload.model or get_runtime_model(session) or "").strip()
    system_prompt = get_system_prompt(session)

    user_msg = Message(
        conversation_id=payload.conversation_id,
        role="user",
        content=payload.message,
        attachments=f"files={len(payload.file_contexts)},images={len(payload.images)}",
    )
    session.add(user_msg)
    session.commit()
    session.refresh(user_msg)
    user_message_id = user_msg.id

    start = time.perf_counter()
    try:
        reply = await chat_completion_with_model(
            history=history,
            message=payload.message,
            file_contexts=payload.file_contexts,
            images=[img.model_dump() for img in payload.images],
            model=selected_model,
            system_prompt=system_prompt,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "timeout" in msg.lower():
            raise HTTPException(status_code=504, detail=msg)
        raise HTTPException(status_code=502, detail=msg)
    latency_ms = int((time.perf_counter() - start) * 1000)

    ai_msg = Message(
        conversation_id=payload.conversation_id,
        role="assistant",
        content=reply,
        attachments=f"latency_ms={latency_ms}",
    )
    session.add(ai_msg)
    session.commit()
    session.refresh(ai_msg)
    assistant_message_id = ai_msg.id

    # 标题生成失败不应影响消息落库
    if conv.title == "新对话":
        try:
            conv.title = await _smart_conversation_title(payload.message, reply, selected_model)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("Conversation title update failed after assistant message saved: %s", exc)

    return ChatSendResponse(
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        assistant_reply=reply,
    )


@router.post("/stream")
async def send_chat_stream(payload: ChatSendRequest, session: Session = Depends(get_session)):
    conv = session.get(Conversation, payload.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    history_stmt = (
        select(Message)
        .where(Message.conversation_id == payload.conversation_id)
        .order_by(Message.created_at.asc())
    )
    history_rows = session.execute(history_stmt).scalars().all()
    history = [{"role": h.role, "content": h.content} for h in history_rows][-20:]
    should_auto_title = (conv.title == "新对话")
    selected_model = (payload.model or get_runtime_model(session) or "").strip()
    system_prompt = get_system_prompt(session)

    user_msg = Message(
        conversation_id=payload.conversation_id,
        role="user",
        content=payload.message,
        attachments=f"files={len(payload.file_contexts)},images={len(payload.images)}",
    )
    session.add(user_msg)
    session.commit()
    session.refresh(user_msg)
    user_message_id = user_msg.id

    async def event_generator():
        start = time.perf_counter()
        assembled_parts: list[str] = []

        async def _persist_assistant_reply(reply: str, latency_ms: int, stream_status: str) -> int | None:
            if not reply:
                return None

            persist_session = SessionLocal()
            try:
                ai_msg = Message(
                    conversation_id=payload.conversation_id,
                    role="assistant",
                    content=reply,
                    attachments=f"latency_ms={latency_ms};stream_status={stream_status}",
                )
                persist_session.add(ai_msg)
                persist_session.commit()
                persist_session.refresh(ai_msg)

                if stream_status == "completed" and should_auto_title:
                    try:
                        conv_row = persist_session.get(Conversation, payload.conversation_id)
                        if conv_row and conv_row.title == "新对话":
                            conv_row.title = await _smart_conversation_title(payload.message, reply, selected_model)
                            persist_session.commit()
                    except Exception as exc:
                        persist_session.rollback()
                        logger.warning("Conversation title update failed in stream mode after assistant message saved: %s", exc)

                return ai_msg.id
            except Exception as exc:
                persist_session.rollback()
                logger.warning("Assistant message persist failed in stream mode: %s", exc)
                return None
            finally:
                persist_session.close()

        meta = {
            "event": "meta",
            "user_message_id": user_message_id,
            "model": selected_model,
        }
        yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"

        try:
            async for delta in stream_chat_completion_with_model(
                history=history,
                message=payload.message,
                file_contexts=payload.file_contexts,
                images=[img.model_dump() for img in payload.images],
                model=selected_model,
                system_prompt=system_prompt,
            ):
                if not delta:
                    continue
                assembled_parts.append(delta)
                out = {"event": "delta", "delta": delta}
                yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"

            reply = "".join(assembled_parts)
            latency_ms = int((time.perf_counter() - start) * 1000)
            assistant_message_id = await _persist_assistant_reply(reply, latency_ms, "completed")

            done = {
                "event": "done",
                "assistant_reply": reply,
                "assistant_message_id": assistant_message_id,
                "latency_ms": latency_ms,
            }
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        except RuntimeError as exc:
            msg = str(exc)
            reply = "".join(assembled_parts)
            latency_ms = int((time.perf_counter() - start) * 1000)
            assistant_message_id = await _persist_assistant_reply(reply, latency_ms, "partial")
            err = {
                "event": "error",
                "detail": msg,
                "assistant_message_id": assistant_message_id,
                "latency_ms": latency_ms,
            }
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        except Exception as exc:
            msg = str(exc)
            reply = "".join(assembled_parts)
            latency_ms = int((time.perf_counter() - start) * 1000)
            assistant_message_id = await _persist_assistant_reply(reply, latency_ms, "partial")
            err = {
                "event": "error",
                "detail": msg,
                "assistant_message_id": assistant_message_id,
                "latency_ms": latency_ms,
            }
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
