import json
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


def _make_conversation_title(user_text: str, assistant_text: str) -> str:
    base = (user_text or "").strip() or (assistant_text or "").strip() or "新对话"
    first_line = base.splitlines()[0].strip()
    cleaned = " ".join(first_line.split())
    max_len = 24
    if len(cleaned) > max_len:
        return cleaned[:max_len] + "..."
    return cleaned or "新对话"


async def _smart_conversation_title(user_text: str, assistant_text: str, model: str) -> str:
    prompt = (
        "请基于这段对话生成一个简洁中文标题。要求：\n"
        "1) 8~18字；2) 不要标点结尾；3) 不能出现'新对话'、'聊天记录'等空泛词；\n"
        "4) 仅输出标题文本本身，不要解释。\n\n"
        f"用户：{user_text}\n"
        f"助手：{assistant_text}"
    )
    title = await chat_completion_with_model(
        history=[],
        message=prompt,
        file_contexts=[],
        images=[],
        model=model,
        system_prompt="你是一个擅长给对话起标题的助手。",
    )
    title = " ".join((title or "").strip().split())
    if not title:
        return _make_conversation_title(user_text, assistant_text)
    if len(title) > 24:
        title = title[:24]
    return title


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

    # 首轮回复后自动根据内容命名会话
    if conv.title == "新对话" and len(history_rows) == 0:
        try:
            conv.title = await _smart_conversation_title(payload.message, reply, selected_model)
        except Exception:
            conv.title = _make_conversation_title(payload.message, reply)

    session.commit()
    session.refresh(ai_msg)
    assistant_message_id = ai_msg.id

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
    should_auto_title = (conv.title == "新对话" and len(history_rows) == 0)
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

            persist_session = SessionLocal()
            try:
                ai_msg = Message(
                    conversation_id=payload.conversation_id,
                    role="assistant",
                    content=reply,
                    attachments=f"latency_ms={latency_ms}",
                )
                persist_session.add(ai_msg)

                if should_auto_title:
                    conv_row = persist_session.get(Conversation, payload.conversation_id)
                    if conv_row and conv_row.title == "新对话":
                        try:
                            conv_row.title = await _smart_conversation_title(payload.message, reply, selected_model)
                        except Exception:
                            conv_row.title = _make_conversation_title(payload.message, reply)

                persist_session.commit()
                persist_session.refresh(ai_msg)
            finally:
                persist_session.close()

            done = {
                "event": "done",
                "assistant_reply": reply,
                "assistant_message_id": ai_msg.id,
                "latency_ms": latency_ms,
            }
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        except RuntimeError as exc:
            msg = str(exc)
            err = {"event": "error", "detail": msg}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
