"""Terminal yes/no prompt (used while the TUI is suspended or headless)."""

from __future__ import annotations


def ask_yes(prompt: str) -> bool:
    try:
        reply = input(f"{prompt} [e/y]: ")
    except EOFError:
        return False
    return reply.strip().lower() in ("e", "evet", "y", "yes")
