"""Multi-turn conversation driver with auto-generated follow-up questions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fy_loadtest.client import ChatClient, ChatResult


@dataclass
class TurnResult:
    turn: int
    prompt_tokens: int
    cached_tokens: int
    ttft_ms: float
    e2e_ms: float
    completion_tokens: int = 0
    content_preview: str = ""

    @property
    def cache_ratio(self) -> float:
        if self.prompt_tokens <= 0:
            return 0.0
        return self.cached_tokens / self.prompt_tokens


@dataclass
class ConversationResult:
    seed: str
    session_id: str
    turns: list[TurnResult] = field(default_factory=list)


_FOLLOWUP_PROMPT = "基于以上对话内容，提出一个相关的深入问题。只输出问题本身，不要任何前缀或解释。"


def build_followup_messages(history: list[dict]) -> list[dict]:
    return history + [{"role": "user", "content": _FOLLOWUP_PROMPT}]


def build_seed_messages(topic: str) -> list[dict]:
    seed_q = f"请详细介绍一下：{topic}"
    return [{"role": "user", "content": seed_q}]


async def run_conversation(
    client: ChatClient,
    *,
    model: str,
    seed_topic: str,
    max_turns: int,
    max_prompt_tokens: int,
    temperature: float,
    max_tokens: int,
    stream: bool,
) -> ConversationResult:
    session_id = uuid.uuid4().hex
    history: list[dict] = []
    result = ConversationResult(seed=seed_topic, session_id=session_id)

    # Build seed
    seed_messages = build_seed_messages(seed_topic)
    history.extend(seed_messages)

    for turn_num in range(1, max_turns + 1):
        # Send current history to get response
        chat_result = await client.chat(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
            messages=history,
        )

        if not chat_result.success:
            break

        turn = TurnResult(
            turn=turn_num,
            prompt_tokens=chat_result.usage.prompt_tokens,
            cached_tokens=chat_result.usage.cached_tokens,
            ttft_ms=chat_result.ttft_s * 1000 if chat_result.ttft_s > 0 else 0,
            e2e_ms=chat_result.e2e_s * 1000,
            completion_tokens=chat_result.usage.completion_tokens,
            content_preview=chat_result.content_text[:100] if chat_result.content_text else "",
        )
        result.turns.append(turn)

        # Check token limit
        if chat_result.usage.prompt_tokens >= max_prompt_tokens:
            break

        # Add assistant response to history
        history.append({"role": "assistant", "content": chat_result.content_text})

        # If not last turn, generate follow-up question
        if turn_num < max_turns:
            followup_msgs = build_followup_messages(history)
            q_result = await client.chat(
                model=model,
                max_tokens=256,
                temperature=temperature,
                stream=False,
                messages=followup_msgs,
            )
            if not q_result.success or not q_result.content_text.strip():
                break
            # Add the follow-up question as next user message
            history.append({"role": "user", "content": q_result.content_text.strip()})

    return result
