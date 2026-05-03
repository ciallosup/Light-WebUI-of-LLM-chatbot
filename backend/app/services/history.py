"""历史上下文（chat history）构造与裁剪。

设计目标：
1. 不再使用暴力 [-20:] 截断，改为基于 token 预算的滑动窗口；
2. 助手消息中的 <thinking>...</thinking> 推理链不应回传给模型——既省 token，
   也避免某些模型（如 DeepSeek-Reasoner）拒答；
3. partial（流式中断）助手消息不应回传，避免模型困惑"上句没说完"；
4. 保证消息以 user 开头（部分上游 API 要求），保证 user/assistant 配对；
5. 可选：保留首条 user 消息（任务初始描述），避免长会话失忆。
"""

from __future__ import annotations

from typing import Iterable

from backend.app.config import get_settings
from backend.app.models import Message
from backend.app.services.llm_client import strip_thinking


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。

    经验口径：
      - ASCII 字符 ≈ 4 chars / token
      - CJK 字符 ≈ 1.5 chars / token
      - 混合按 chars / 2 近似
    准确度对滑窗预算来说足够（误差 ±20% 不影响安全裕量）。
    """
    if not text:
        return 0
    ascii_count = 0
    cjk_count = 0
    other = 0
    for ch in text:
        code = ord(ch)
        if code < 128:
            ascii_count += 1
        elif 0x4E00 <= code <= 0x9FFF or 0x3000 <= code <= 0x30FF or 0xAC00 <= code <= 0xD7AF:
            # CJK Unified + 假名 + 谚文
            cjk_count += 1
        else:
            other += 1
    return int(ascii_count / 4 + cjk_count / 1.5 + other / 2) + 1


def _is_partial_assistant(row: Message) -> bool:
    if row.role != "assistant":
        return False
    return "stream_status=partial" in (row.attachments or "")


def _normalize_for_history(row: Message) -> dict | None:
    """把数据库行转成上游模型可消费的 history 项；过滤掉不该带的内容。"""
    if _is_partial_assistant(row):
        return None
    role = row.role
    content = row.content or ""
    if role == "assistant":
        # 不把模型自己的思考过程回传（既污染又费 token）
        content = strip_thinking(content)
    if not content.strip():
        return None
    return {"role": role, "content": content}


def _ensure_starts_with_user(messages: list[dict]) -> list[dict]:
    """保证以 user 开头：从前往后剥离非 user 项。"""
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def build_history_messages(rows: Iterable[Message]) -> list[dict]:
    """根据数据库消息行构造历史，应用 token 预算 + 条数 + 首条 user 锚定 + 配对保证。

    注意：返回的 list **不包含**当前轮次的 user 消息（调用方自己构造并 append）。
    """
    settings = get_settings()
    rows_list = list(rows)

    # 1) 标准化 + 过滤
    normalized: list[tuple[Message, dict]] = []
    for row in rows_list:
        item = _normalize_for_history(row)
        if item is None:
            continue
        normalized.append((row, item))

    if not normalized:
        return []

    # 2) 找到首条 user 消息（用于"始终保留任务初始描述"）
    first_user_item: dict | None = None
    if settings.llm_history_keep_first_user:
        for _, item in normalized:
            if item["role"] == "user":
                first_user_item = dict(item)  # 浅拷贝
                break

    # 3) 从最近一条往前累加 token，命中预算就停
    budget = max(500, int(settings.llm_history_token_budget))
    max_msgs = max(2, int(settings.llm_history_max_messages))

    used_tokens = 0
    selected_reversed: list[dict] = []
    for _, item in reversed(normalized):
        cost = estimate_tokens(item["content"]) + 4  # 每条额外 ~4 token 协议开销
        if selected_reversed and used_tokens + cost > budget:
            break
        if len(selected_reversed) >= max_msgs:
            break
        selected_reversed.append(item)
        used_tokens += cost

    selected = list(reversed(selected_reversed))

    # 4) 把首条 user 消息 prepend 进去（如果它没有自然落在窗口内且不重复）
    if first_user_item is not None:
        already_has_first = any(
            m["role"] == "user" and m["content"] == first_user_item["content"]
            for m in selected
        )
        if not already_has_first:
            # 标记"这是任务初始描述的快照，避免上下文丢失"
            anchor = dict(first_user_item)
            anchor["content"] = (
                "[历史会话开头的用户原始提问，作为长期上下文锚点]\n"
                + anchor["content"]
            )
            selected.insert(0, anchor)

    # 5) 保证以 user 开头（剥离开头的 assistant），并去除可能的连续同角色消息
    selected = _ensure_starts_with_user(selected)

    # 6) 修剪连续同角色消息（保留较新的一条），减少 API 拒答风险
    cleaned: list[dict] = []
    for m in selected:
        if cleaned and cleaned[-1]["role"] == m["role"]:
            cleaned[-1] = m  # 用更新的一条覆盖
        else:
            cleaned.append(m)

    return cleaned
