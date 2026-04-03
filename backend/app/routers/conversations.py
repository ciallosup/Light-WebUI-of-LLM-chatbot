from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db import get_session
from backend.app.models import Conversation, Message
from backend.app.schemas import ConversationCreate


router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _conv_to_dict(conv: Conversation):
    return {
        "id": conv.id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
    }


def _msg_to_dict(msg: Message):
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "role": msg.role,
        "content": msg.content,
        "attachments": msg.attachments,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


@router.post("")
def create_conversation(payload: ConversationCreate, session: Session = Depends(get_session)):
    conv = Conversation(title=payload.title or "新对话")
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return _conv_to_dict(conv)


@router.get("")
def list_conversations(session: Session = Depends(get_session)):
    statement = select(Conversation).order_by(Conversation.created_at.desc())
    rows = session.execute(statement).scalars().all()
    return [_conv_to_dict(c) for c in rows]


@router.get("/search")
def search_conversations(q: str = Query(min_length=1), session: Session = Depends(get_session)):
    kw = f"%{q.strip()}%"
    conv_stmt = (
        select(Conversation)
        .where(Conversation.title.like(kw))
        .order_by(Conversation.created_at.desc())
    )
    conv_rows = session.execute(conv_stmt).scalars().all()

    msg_stmt = (
        select(Message)
        .where(Message.content.like(kw))
        .order_by(Message.created_at.desc())
    )
    msg_rows = session.execute(msg_stmt).scalars().all()

    by_id: dict[int, dict] = {c.id: _conv_to_dict(c) for c in conv_rows}
    for m in msg_rows:
        if m.conversation_id in by_id:
            continue
        conv = session.get(Conversation, m.conversation_id)
        if conv:
            by_id[conv.id] = _conv_to_dict(conv)

    return list(by_id.values())


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: int, session: Session = Depends(get_session)):
    conv = session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs = session.execute(select(Message).where(Message.conversation_id == conversation_id)).scalars().all()
    for m in msgs:
        session.delete(m)
    session.delete(conv)
    session.commit()
    return {"ok": True}


@router.get("/{conversation_id}/messages")
def list_messages(conversation_id: int, session: Session = Depends(get_session)):
    conv = session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    statement = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
    rows = session.execute(statement).scalars().all()
    return [_msg_to_dict(m) for m in rows]
