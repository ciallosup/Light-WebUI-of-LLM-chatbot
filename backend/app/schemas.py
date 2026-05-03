from typing import List, Optional

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: Optional[str] = "新对话"


class ConversationRead(BaseModel):
    id: int
    title: str
    created_at: str


class MessageRead(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    attachments: Optional[str] = None
    created_at: str


class ImagePayload(BaseModel):
    filename: str
    mime_type: str
    data_base64: str


class ChatSendRequest(BaseModel):
    conversation_id: int
    message: str = Field(min_length=1)
    file_contexts: List[str] = []
    images: List[ImagePayload] = []
    model: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class ChatSendResponse(BaseModel):
    user_message_id: int
    assistant_message_id: int
    assistant_reply: str


class RuntimeModelUpdate(BaseModel):
    model: str = Field(min_length=1)


class SystemPromptUpdate(BaseModel):
    content: str
