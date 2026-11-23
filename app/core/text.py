import re

# Emoji and pictographs (incl. variation selectors / joiners). Plain symbols like ● ○ « » are kept.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D\u20E3]+"
)


def strip_emoji(text: str) -> str:
    """The bot is emoji-free (text emoticons like :) instead); LLM output is filtered as a safety net.

    NOTE: This is now only used for assistant messages, NOT for routine titles.
    Titles preserve emojis via _clean_title_for_agent().
    """
    return re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text)).strip()


def _clean_title_for_agent(text: str) -> str:
    """Clean title for agent processing - keep emojis, normalize whitespace only."""
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def extract_emojis(text: str) -> str:
    """Extract all emojis from text, concatenated."""
    return "".join(_EMOJI.findall(text))


def preserve_emojis_in_title(user_message: str, llm_title: str) -> str:
    """If user message has emojis but LLM title doesn't, append them.

    Fallback for models that don't reliably preserve emojis in titles.
    """
    user_emojis = extract_emojis(user_message)
    title_emojis = extract_emojis(llm_title)

    # Find emojis in user message that are NOT in title
    missing = "".join(ch for ch in user_emojis if ch not in title_emojis)

    if missing:
        # Append missing emojis to title
        if llm_title and not llm_title[-1].isspace() and not _EMOJI.match(llm_title[-1]):
            return llm_title + " " + missing
        return llm_title + missing
    return llm_title
