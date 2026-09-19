"""Finite browser destinations from user input, never model-generated URLs."""

import re
from urllib.parse import urlsplit

# Address-book shortcuts only. Jev still chooses every action on every site.
SHORTCUTS = (
    (r"\byou\s?tube\b", "https://www.youtube.com/"),
    (r"\b(?:aws|amazon web services)\b", "https://console.aws.amazon.com/"),
    (r"\bwikipedia\b", "https://en.wikipedia.org/"),
    (r"\bgithub\b", "https://github.com/"),
    (r"\bgoogle drive\b", "https://drive.google.com/"),
    (r"\bgoogle docs\b", "https://docs.google.com/"),
    (r"\bgoogle calendar\b", "https://calendar.google.com/"),
    (r"\bgmail\b", "https://mail.google.com/"),
    (r"\breddit\b", "https://www.reddit.com/"),
    (r"\b(?:open|visit|browse|go to)\s+(?:up\s+)?(?:my\s+)?x\b|\bon x\b|\btwitter\b", "https://x.com/"),
)
URL = re.compile(r"https?://[^\s<>\"“”]+", re.IGNORECASE)
DOMAIN = re.compile(
    r"(?<![\w@/.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:[a-z]{2,24})(?:/[^\s<>\"“”]*)?", re.IGNORECASE,
)


def navigation_urls(goal, values=()):
    """Offer at most 16 explicit addresses or named shortcuts, in mention order."""
    found = []
    for text in (goal, *values):
        candidates = [(m.start(), m.group().rstrip(".,;!?')]}")) for m in URL.finditer(text)]
        occupied = [m.span() for m in URL.finditer(text)]
        for match in DOMAIN.finditer(text):
            if not any(start <= match.start() < end for start, end in occupied):
                candidates.append((match.start(), "https://" + match.group().rstrip(".,;!?')]}")))
        for pattern, address in SHORTCUTS:
            candidates.extend((m.start(), address) for m in re.finditer(pattern, text, re.IGNORECASE))
        for _, address in sorted(candidates, key=lambda item: item[0]):
            parsed = urlsplit(address)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                continue
            if address not in found:
                found.append(address)
    if len(found) > 16:
        raise ValueError("Supply at most 16 web addresses for one run.")
    return found


# Extract verbatim user text from common instruction clauses, never from web pages.
TEXT_COMMAND = re.compile(
    r"\b(?:search(?:\s+\w+){0,3}?\s+for|look up|type|enter)\s+"
    r"(?P<value>[^\r\n]+?)"
    r"(?=\s+(?:and then|then|and (?:open|click|select|scroll|stop|go|search|type|enter)|"
    r"into|in the|in a)\b|[,;]\s*(?:then|and)\b|$)",
    re.IGNORECASE,
)


def task_text_values(goal):
    """Finite exact spans for ordinary unquoted search/type instructions."""
    candidates = []
    for match in TEXT_COMMAND.finditer(goal):
        if re.search(r"\b(?:do not|don't|never|not|avoid)\s+(?:just\s+)?$", goal[:match.start()], re.IGNORECASE):
            continue
        value = match['value'].strip().rstrip('.;').rstrip(',').rstrip()
        # Quoted values are already handled by the exact quote parser in model.py.
        if not value or value[0] in ('"', '“', '”', "'", '‘', '’'):
            continue
        if len(value) <= 2000 and value not in candidates:
            candidates.append(value)
    return candidates
