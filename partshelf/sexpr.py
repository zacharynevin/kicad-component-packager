"""Small, strict reader for KiCad S-expressions, preserving quoted atoms.

Unknown fields survive reading and writing. We never interpret a file as code.
"""
from __future__ import annotations

from dataclasses import dataclass


class FormatError(ValueError):
    pass


@dataclass(frozen=True)
class Atom:
    value: str
    quoted: bool = False


def atom(value: object, quoted: bool = False) -> Atom:
    return Atom(str(value), quoted)


def q(value: object) -> Atom:
    return atom(value, True)


def value(node) -> str:
    return node.value if isinstance(node, Atom) else ""


def tag(node) -> str:
    return value(node[0]) if isinstance(node, list) and node else ""


def children(node, name: str):
    return [item for item in node if tag(item) == name]


def child(node, name: str):
    return next((item for item in node if tag(item) == name), None)


def field(node, name: str, default: str = "") -> str:
    item = child(node, name)
    return value(item[1]) if item and len(item) > 1 else default


def walk(node):
    if isinstance(node, list):
        yield node
        for item in node:
            yield from walk(item)


def loads(text: str) -> list:
    stack, root = [], None
    i, length, count = 0, len(text), 0
    while i < length:
        c = text[i]
        if c.isspace() or c == "\ufeff":
            i += 1
            continue
        if c == ";":
            end = text.find("\n", i)
            i = length if end < 0 else end + 1
            continue
        if c == "(":
            if len(stack) >= 128:
                raise FormatError("S-expression nesting is too deep")
            node = []
            if stack:
                stack[-1].append(node)
            elif root is None:
                root = node
            else:
                raise FormatError("Expected one root expression")
            stack.append(node)
            i += 1
            continue
        if c == ")":
            if not stack:
                raise FormatError("Unexpected closing parenthesis")
            stack.pop()
            i += 1
            continue
        if not stack:
            raise FormatError("Text outside root expression")
        if c == '"':
            i += 1
            buf = []
            while i < length and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                    if i == length:
                        raise FormatError("Unterminated string escape")
                    escaped = text[i]
                    # Preserve unknown escapes: vendors sometimes use Windows paths.
                    buf.append({"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}.get(escaped, "\\" + escaped))
                else:
                    buf.append(text[i])
                i += 1
            if i == length:
                raise FormatError("Unterminated quoted string")
            token = q("".join(buf))
            i += 1
        else:
            start = i
            while i < length and not text[i].isspace() and text[i] not in "();":
                i += 1
            token = atom(text[start:i])
        stack[-1].append(token)
        count += 1
        if count > 2_000_000:
            raise FormatError("S-expression is too large")
    if stack or root is None or not root:
        raise FormatError("Incomplete S-expression")
    return root


def dumps(node, depth: int = 0) -> str:
    if isinstance(node, Atom):
        if not node.quoted:
            return node.value
        return '"' + node.value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'
    if not any(isinstance(item, list) for item in node):
        return "(" + " ".join(dumps(item) for item in node) + ")"
    first_list = next(i for i, item in enumerate(node) if isinstance(item, list))
    head = "(" + " ".join(dumps(item) for item in node[:first_list])
    rest = "".join("\n" + "  " * (depth + 1) + dumps(item, depth + 1) for item in node[first_list:])
    return head + rest + "\n" + "  " * depth + ")"


def encode(node) -> bytes:
    return (dumps(node) + "\n").encode("utf-8")
