"""Secret shapes that never enter stored or projected text."""

from __future__ import annotations

import datetime as dt
import json
import re

# Horizontal whitespace keeps an empty field from consuming the next line. The optional
# backslashes around a JSON key also cover a JSON object serialized inside another string.
_KEY = r"""(?:api[_-]?key|token|password|secret)(?:\\*["'])?[ \t]*[=:][ \t]*"""
_PREFIX = rf"(?:bearer[ \t]+|{_KEY})"
PREFIXED = re.compile(_PREFIX, re.IGNORECASE)
_UNQUOTED = re.compile(rf"""(?:(?![;|&]{_PREFIX})[^\s,"'])+""", re.IGNORECASE)
_NESTED_KEY_END = re.compile(rf"(?:^|[\s|,{{]){_KEY}$", re.IGNORECASE)
_NEXT_CREDENTIAL = re.compile(
    rf"""(?:[ \t]*[,;|&][ \t]*|[ \t]+)(?:\\*["'])?{_PREFIX}""", re.IGNORECASE
)
_END_OF_OBJECT = re.compile(r"""[ \t]*(?:[}\]]|\\*["'])+[ \t]*$""")
_LINES = re.compile(r"[^\r\n]+")
_PUNCTUATION = ".;:!?)]}，。；：！？）】"
_ESCAPE_PAIR = 2
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_JSON_CHAR = re.compile(
    r"\\u[dD][89aAbB][0-9A-Fa-f]{2}\\u[dD][c-fC-F][0-9A-Fa-f]{2}"
    r"|\\u[0-9A-Fa-f]{4}|\\.|[^\\]"
)
REDACTED = "[REDACTED]"
REDACTED_PHONE_CN = "[REDACTED_PHONE_CN]"
REDACTED_ID_CN = "[REDACTED_ID_CN]"
_MARKERS = {"phone_cn": REDACTED_PHONE_CN, "id_card_cn": REDACTED_ID_CN}
_PLACEHOLDERS = {REDACTED, *_MARKERS.values()}
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_DIGITS = "10X98765432"
_ID_BIRTH_START = 6
_ID_BIRTH_END = 14

BARE = re.compile(
    r"(?P<openai_key>\bsk-[A-Za-z0-9_-]{16,})"
    r"|(?P<groq_key>\bgsk_[A-Za-z0-9]{16,})"
    r"|(?P<huggingface_token>\bhf_[A-Za-z0-9]{16,})"
    r"|(?P<xai_key>\bxai-[A-Za-z0-9]{16,})"
    r"|(?P<numeric_token>\b\d{6,}\.[A-Za-z0-9]{24,})"
    r"|(?P<aliyun_ak>(?a:\bLTAI[A-Za-z0-9]{12,24}\b))"
    r"|(?P<aws_ak>(?a:\bAKIA[0-9A-Z]{16}\b))"
    r"|(?P<github_token>(?a:\b(?:(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,})\b))"
    r"|(?P<id_card_cn>(?<!\d)[1-9][0-9]{5}(?:19|20)[0-9]{2}"
    r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])[0-9]{3}[0-9Xx](?!\d))"
    r"|(?P<phone_cn>(?<!\d)1[3-9][0-9]{9}(?!\d))"
)


def _valid_cn_id(value: str) -> bool:
    checksum = sum(
        int(digit) * weight for digit, weight in zip(value[:-1], _ID_WEIGHTS, strict=True)
    )
    if value[-1].upper() != _ID_CHECK_DIGITS[checksum % len(_ID_CHECK_DIGITS)]:
        return False
    try:
        dt.date.fromisoformat(value[_ID_BIRTH_START:_ID_BIRTH_END])
    except ValueError:
        return False
    return True


def _category(match: re.Match[str] | None) -> str | None:
    if match is None or (match.lastgroup == "id_card_cn" and not _valid_cn_id(match[0])):
        return None
    return match.lastgroup


def _closing_quote(line: str, start: int, opening: str) -> tuple[int, int]:
    """Return value end and field end, accounting for escapes at this serialization depth."""
    quote = opening[-1]
    cursor = start
    while (position := line.find(quote, cursor)) >= 0:
        slash_start = position
        while slash_start > start and line[slash_start - 1] == "\\":
            slash_start -= 1
        slashes = position - slash_start
        if slashes % (_ESCAPE_PAIR * len(opening)) == len(opening) - 1:
            return position + 1 - len(opening), position + 1
        cursor = position + 1
    # Preserve the legacy spelling of a quoted Windows path ending in a backslash.
    if line.endswith(opening) and len(line) - len(opening) >= start:
        return len(line) - len(opening), len(line)
    return len(line), len(line)


def _replace_value(value: str, report: list[str], *, quoted: bool) -> str:
    if not value:
        return value
    if quoted and value.strip(""" \t\\"'""") in _PLACEHOLDERS:
        return value
    for marker in _PLACEHOLDERS:
        if value.startswith(marker):
            tail = value[len(marker) :]
            if not tail or (not quoted and not tail.strip(_PUNCTUATION)):
                return value
    token = value if quoted else value.rstrip(_PUNCTUATION) or value
    category = _category(BARE.fullmatch(token)) or "credential"
    report.append(category)
    return _MARKERS.get(category, REDACTED) + value[len(token) :]


def _json_string_boundaries(line: str) -> set[int]:
    """Find real closing quotes in JSON containers, including serialized child objects.

    Only an ambiguous keyed value needs this check. Keep offsets in the original line so
    each field can share the result and JSON formatting survives redaction.
    """
    ends: set[int] = set()
    pending: list[tuple[str, range | list[int]]] = [(line, range(1, len(line) + 1))]
    while pending:
        source, offsets = pending.pop()
        try:
            if not isinstance(json.loads(source), (dict, list, str)):
                continue
        except (ValueError, RecursionError):
            continue
        for token in _JSON_STRING.finditer(source):
            ends.add(offsets[token.end() - 1])
            child = json.loads(token[0])
            if child.lstrip().startswith(("{", "[", '"')):
                # Each JSON escape, including a UTF-16 surrogate pair, yields one code point.
                chars = _JSON_CHAR.finditer(source, token.start() + 1, token.end() - 1)
                pending.append((child, [offsets[char.end() - 1] for char in chars]))
    return ends


def _redact_line(line: str, report: list[str]) -> str:
    def replace_bare(match: re.Match[str]) -> str:
        category = _category(match)
        if category is None:
            return match[0]
        report.append(category)
        return _MARKERS.get(category, REDACTED)

    pieces: list[str] = []
    cursor = 0
    structured_ends: set[int] | None = None
    while prefix := PREFIXED.search(line, cursor):
        pieces.append(BARE.sub(replace_bare, line[cursor : prefix.start()]))
        pieces.append(prefix[0])
        cursor = prefix.end()
        quote_at = cursor
        while quote_at < len(line) and line[quote_at] == "\\":
            quote_at += 1
        if quote_at < len(line) and line[quote_at] in "\"'":
            opening = line[cursor : quote_at + 1]
            start = quote_at + 1
            end, cursor = _closing_quote(line, start, opening)
            value = line[start:end]
            closing = line[end:cursor]
            # A dangling second assignment makes the quote boundary ambiguous. As in
            # the legacy format, fail closed on the rest of this malformed line.
            if (
                closing
                and _NESTED_KEY_END.search(value)
                and not _NEXT_CREDENTIAL.match(line, cursor)
                and not _END_OF_OBJECT.match(line, cursor)
            ):
                if structured_ends is None:
                    structured_ends = _json_string_boundaries(line)
                if cursor not in structured_ends:
                    cursor = len(line)
            pieces.extend((opening, _replace_value(value, report, quoted=True), closing))
        elif plain := _UNQUOTED.match(line, cursor):
            pieces.append(_replace_value(plain[0], report, quoted=False))
            cursor = plain.end()
    pieces.append(BARE.sub(replace_bare, line[cursor:]))
    return "".join(pieces)


def redact(text: str) -> str:
    return redact_with_report(text)[0]


def redact_with_report(text: str) -> tuple[str, list[str]]:
    """Return the cleaned text and one category for each replacement."""
    report: list[str] = []

    return _LINES.sub(lambda line: _redact_line(line[0], report), text), report
