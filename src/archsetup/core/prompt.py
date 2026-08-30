"""Terminal yes/no prompt (used while the TUI is suspended or headless)."""

from __future__ import annotations

from . import i18n

# Both an English and a Turkish yes, because the tool is bilingual and the
# hint only ever shows one of them. Everything else is no, including an
# empty line -- callers that want a default answer say so in their own
# question text, and a bare Enter must not confirm a disk format.
YES = ("e", "evet", "y", "yes")


def ask_yes(prompt: str) -> bool:
    """Ask, showing the affirmative/negative pair for the active language.

    The hint used to be a hardcoded `[e/y]`, which named two *yeses* -- the
    Turkish `evet` and the English `yes` -- and no negative at all. It reads
    as an ordinary yes/no pair in either language and is wrong in both: a
    Turkish reader takes `y` for the second option and a reflex `y` from an
    English reader is the affirmative. Found by walking into it: `y` was
    typed at "add kernel headers?" meaning no, and the headers were
    installed. Two rows earlier the same prompt asks whether to format the
    selected devices.
    """
    try:
        reply = input(f"{prompt} {i18n.t('prompt.yes_no')}: ")
    except EOFError:
        return False
    return reply.strip().lower() in YES
