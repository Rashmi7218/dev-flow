import re

TICKET_PATTERN = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def extract_ticket_key(*texts: str | None) -> str | None:
    for text in texts:
        if not text:
            continue
        match = TICKET_PATTERN.search(text)
        if match:
            return match.group(0)
    return None
