"""Pre-segment Chinese for SQLite FTS5, with an optional dictionary."""

from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import pairwise
from types import ModuleType

TOKENIZER_ASCII = "ascii"
TOKENIZER_CJK = "cjk"
TOKENIZER_HYBRID = "cjk_hybrid"
SUPPORTED_TOKENIZERS = frozenset({TOKENIZER_ASCII, TOKENIZER_CJK, TOKENIZER_HYBRID})

INSTALL_HINT = "pip install 'agent-memory-core[cjk]'"

_ASCII_TOKENS = re.compile(r"[0-9A-Za-z_]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_PROJECTION_VERSION = 3


def _jieba(strict: bool) -> ModuleType | None:
    try:
        import jieba
    except ImportError as error:
        if strict:
            raise RuntimeError(
                "CJK tokenization requires jieba in strict mode; "
                f"install it with: {INSTALL_HINT}"
            ) from error
        return None
    return jieba


def validate_tokenizer(name: str) -> None:
    if name not in SUPPORTED_TOKENIZERS:
        raise ValueError(
            f"unknown tokenizer: {name!r}; expected one of {sorted(SUPPORTED_TOKENIZERS)}"
        )


class Tokenizer:
    def __init__(self, name: str, *, use_jieba: bool = True, strict_jieba: bool = False):
        validate_tokenizer(name)
        if strict_jieba and not use_jieba:
            raise ValueError("strict_jieba requires use_jieba")
        self.name = name
        if name == TOKENIZER_CJK and not use_jieba:
            raise ValueError("cjk requires jieba; use cjk_hybrid for the builtin tokenizer")
        self._jieba = _jieba(strict_jieba) if use_jieba and name != TOKENIZER_ASCII else None
        provider = getattr(self._jieba, "__version__", "installed") if self._jieba else "builtin"
        self.signature = f"{name}:{_PROJECTION_VERSION}:{provider}"

    def _chinese_tokens(self, run: str, *, indexing: bool = False) -> list[str]:
        tokens = [run[0]]
        tokens.extend(left + right for left, right in pairwise(run))
        if len(run) > 1:
            tokens.append(run[-1])
        if indexing:
            # A one-character query must also find an interior character. Long queries
            # keep only the two edge unigrams to avoid widening every OR expression.
            tokens.extend(run[1:-1])
        if self._jieba is not None:
            tokens.extend(word for word in self._jieba.cut(run) if _CJK_RUN.fullmatch(word))
        return list(dict.fromkeys(tokens))

    def segment(self, text: str) -> str:
        if self.name == TOKENIZER_ASCII:
            return text
        if self.name == TOKENIZER_CJK:
            return " ".join(
                token.strip() for token in self._dictionary_tokens(text) if token.strip()
            )
        return _CJK_RUN.sub(
            lambda span: " " + " ".join(self._chinese_tokens(span[0], indexing=True)) + " ", text
        )

    def query(self, text: str) -> list[str]:
        tokens = [token.lower() for token in _ASCII_TOKENS.findall(text)]
        if self.name == TOKENIZER_CJK:
            tokens.extend(
                word.lower()
                for token in self._dictionary_tokens(text)
                if (word := token.strip()) and not word.isascii() and len(word) > 1
            )
        elif self.name == TOKENIZER_HYBRID:
            for run in _CJK_RUN.findall(text):
                tokens.extend(self._chinese_tokens(run))
        return tokens

    def _dictionary_tokens(self, text: str) -> Iterable[str]:
        if self._jieba is None:
            raise RuntimeError(f"CJK tokenization requires jieba; install it with: {INSTALL_HINT}")
        return self._jieba.cut(text)


def segment_text(
    text: str, tokenizer: str, *, strict_jieba: bool = False, use_jieba: bool = True
) -> str:
    """Index-side projection; hybrid supports builtin segmentation with optional jieba."""
    return Tokenizer(tokenizer, strict_jieba=strict_jieba, use_jieba=use_jieba).segment(text)


def query_tokens(
    query: str, tokenizer: str, *, strict_jieba: bool = False, use_jieba: bool = True
) -> list[str]:
    return Tokenizer(tokenizer, strict_jieba=strict_jieba, use_jieba=use_jieba).query(query)
