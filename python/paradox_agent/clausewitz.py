"""Small dependency-free parser for text-format Clausewitz save data.

Stellaris saves use repeated keys, anonymous values, and anonymous nested blocks,
so a normal ``dict`` cannot represent the format without losing information.
``PdxObject`` preserves entry order and repeated keys while still providing
convenient lookup helpers for the observation extractor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, TypeAlias


Scalar: TypeAlias = str | int | float | bool


@dataclass(slots=True)
class PdxObject:
    entries: list[tuple[str, Value]] = field(default_factory=list)
    values: list[Value] = field(default_factory=list)

    def get(self, key: str, default: Value | None = None) -> Value | None:
        for entry_key, value in reversed(self.entries):
            if entry_key == key:
                return value
        return default

    def get_all(self, key: str) -> list[Value]:
        return [value for entry_key, value in self.entries if entry_key == key]

    def object(self, key: str) -> "PdxObject | None":
        value = self.get(key)
        return value if isinstance(value, PdxObject) else None


Value: TypeAlias = Scalar | PdxObject


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str = ""


def _tokens(text: str) -> Iterator[_Token]:
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace() or char == "\ufeff":
            index += 1
            continue
        if char == "#":
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if char == "{":
            index += 1
            yield _Token("LBRACE")
            continue
        if char == "}":
            index += 1
            yield _Token("RBRACE")
            continue
        if char == "=":
            index += 1
            yield _Token("EQUALS")
            continue
        if char == '"':
            index += 1
            parts: list[str] = []
            while index < length:
                char = text[index]
                if char == '"':
                    index += 1
                    break
                if char == "\\" and index + 1 < length:
                    escaped = text[index + 1]
                    parts.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
                    index += 2
                    continue
                parts.append(char)
                index += 1
            yield _Token("STRING", "".join(parts))
            continue

        start = index
        while index < length and not text[index].isspace() and text[index] not in '{}=#"':
            index += 1
        yield _Token("ATOM", text[start:index])


def _atom(token: _Token) -> Scalar:
    if token.kind == "STRING":
        return token.text
    if token.text == "yes":
        return True
    if token.text == "no":
        return False
    try:
        return int(token.text)
    except ValueError:
        try:
            return float(token.text)
        except ValueError:
            return token.text


class _Parser:
    def __init__(self, text: str) -> None:
        self._tokens = iter(_tokens(text))
        self._lookahead: _Token | None = None

    def _peek(self) -> _Token | None:
        if self._lookahead is None:
            self._lookahead = next(self._tokens, None)
        return self._lookahead

    def _take(self) -> _Token | None:
        token = self._peek()
        self._lookahead = None
        return token

    def parse(self) -> PdxObject:
        return self._object(expect_close=False)

    def _value(self) -> Value:
        token = self._take()
        if token is None:
            raise ValueError("Unexpected end of Clausewitz data")
        if token.kind == "LBRACE":
            return self._object(expect_close=True)
        if token.kind in {"ATOM", "STRING"}:
            return _atom(token)
        raise ValueError(f"Unexpected token {token.kind} while reading a value")

    def _object(self, *, expect_close: bool) -> PdxObject:
        result = PdxObject()
        while True:
            token = self._peek()
            if token is None:
                if expect_close:
                    raise ValueError("Unclosed Clausewitz block")
                return result
            if token.kind == "RBRACE":
                if not expect_close:
                    raise ValueError("Unexpected closing brace")
                self._take()
                return result
            if token.kind == "LBRACE":
                result.values.append(self._value())
                continue
            if token.kind not in {"ATOM", "STRING"}:
                raise ValueError(f"Unexpected token {token.kind}")

            first = self._take()
            if self._peek() is not None and self._peek().kind == "EQUALS":
                self._take()
                result.entries.append((first.text, self._value()))
            else:
                result.values.append(_atom(first))


def parse_clausewitz(text: str) -> PdxObject:
    """Parse a complete text-format Clausewitz document."""

    return _Parser(text).parse()

