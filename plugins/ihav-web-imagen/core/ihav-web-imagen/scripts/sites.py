"""The web chats ihav-web-imagen knows, by id. Standard library only.

A site here can be signed in to (`runtime.py login`) and surveyed (`survey.py`). Only `ADAPTERS` can draw: a site gets an
adapter after its survey and one approved real run, never from documentation alone.
"""
from __future__ import annotations

SITES = {
    'chatgpt': ('ChatGPT', ('https://chatgpt.com/',)),
    'gemini': ('Gemini', ('https://gemini.google.com/app',)),
    'lechat': ('Le Chat', ('https://chat.mistral.ai/chat',)),
    'qwen': ('Qwen', ('https://chat.qwen.ai/',)),
    'grok': ('Grok', ('https://grok.com/',)),
    'perplexity': ('Perplexity', ('https://www.perplexity.ai/',)),
    'metaai': ('Meta AI', ('https://www.meta.ai/',)),
    'zai': ('Z.ai', ('https://chat.z.ai/', 'https://image.z.ai/')),
    'kimi': ('Kimi', ('https://www.kimi.com/',)),
}
ADAPTERS = ('chatgpt',)


def resolve(names: list[str] | None) -> list[str]:
    """`all` or no names -> every site; otherwise the names given, each checked."""
    if not names or 'all' in names:
        return list(SITES)
    unknown = [name for name in names if name not in SITES]
    if unknown:
        raise ValueError(f'Unknown site {", ".join(unknown)}; known: {", ".join(SITES)}')
    return list(dict.fromkeys(names))


def urls(names: list[str]) -> list[str]:
    return [url for name in names for url in SITES[name][1]]
