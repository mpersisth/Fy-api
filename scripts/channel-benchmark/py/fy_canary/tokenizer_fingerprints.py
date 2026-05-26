"""Tokenizer fingerprint reference ranges.

All ranges are SPECULATIVE and must be calibrated via real API calls before
production use. Run `fy-canary baseline` to collect actual prompt_tokens
values, then update ranges to actual ± 2.

NOTE: prompt_tokens reported by the API includes chat-template overhead
(message framing tokens), not just the raw text. Ranges here account for
that overhead.
"""

from __future__ import annotations

# {model_name: [(exact_prompt_text, lo_tokens, hi_tokens), ...]}
TOKENIZER_FINGERPRINTS: dict[str, list[tuple[str, int, int]]] = {
    # GPT family — o200k_base tokenizer (GPT-4o series)
    "gpt-4o": [
        ("Hello, world!", 7, 12),
        ("人工智能改变了世界", 10, 18),
        ("def hello(): return 'world'", 12, 18),
        ("1234567890", 6, 10),
        ("AI 人工智能 2024", 10, 16),
    ],
    "gpt-4o-mini": [
        ("Hello, world!", 7, 12),
        ("人工智能改变了世界", 10, 18),
        ("def hello(): return 'world'", 12, 18),
        ("1234567890", 6, 10),
        ("AI 人工智能 2024", 10, 16),
    ],
    # Claude family — Anthropic tokenizer
    "claude-sonnet-4-6": [
        ("Hello, world!", 8, 14),
        ("人工智能改变了世界", 8, 16),
        ("def hello(): return 'world'", 12, 18),
        ("1234567890", 6, 10),
        ("AI 人工智能 2024", 9, 15),
    ],
    "claude-opus-4-7": [
        ("Hello, world!", 8, 14),
        ("人工智能改变了世界", 8, 16),
    ],
    "claude-haiku-4-5": [
        ("Hello, world!", 8, 14),
        ("人工智能改变了世界", 8, 16),
    ],
    # Gemini family — SentencePiece tokenizer
    "gemini-2.5-flash": [
        ("Hello, world!", 6, 12),
        ("人工智能改变了世界", 8, 16),
    ],
    # DeepSeek family — custom tokenizer, more efficient on CJK
    "deepseek-v4-flash": [
        ("Hello, world!", 6, 12),
        ("人工智能改变了世界", 5, 12),
        ("def hello(): return 'world'", 9, 16),
        ("1234567890", 5, 10),
        ("AI 人工智能 2024", 7, 14),
    ],
    "deepseek-v4-pro": [
        ("Hello, world!", 6, 12),
        ("人工智能改变了世界", 5, 12),
        ("def hello(): return 'world'", 9, 16),
        ("1234567890", 5, 10),
        ("AI 人工智能 2024", 7, 14),
    ],
    "deepseek-v3": [
        ("Hello, world!", 6, 12),
        ("人工智能改变了世界", 5, 12),
    ],
    "deepseek-r1": [
        ("Hello, world!", 6, 12),
        ("人工智能改变了世界", 5, 12),
    ],
}
