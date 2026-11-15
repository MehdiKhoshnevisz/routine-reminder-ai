import re

# Emoji and pictographs (incl. variation selectors / joiners). Plain symbols like ● ○ « » are kept.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D\u20E3]+"
)


def strip_emoji(text: str) -> str:
    """The bot is emoji-free (text emoticons like :) instead); LLM output is filtered as a safety net."""
    return re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text)).strip()
