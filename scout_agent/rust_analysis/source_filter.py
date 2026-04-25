from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final

from tree_sitter import Node
from scout_agent.rust_analysis.tree_sitter_utils import (
    RUST_LANGUAGE,
    build_parser,
    node_text,
)

_CONTAINER_NODE_TYPES: Final[frozenset[str]] = frozenset(
    {"source_file", "declaration_list"}
)


@dataclass(frozen=True, slots=True)
class AnalysisSource:
    relative_path: str
    raw_text: str
    analysis_text: str
    content_sha256: str


def is_test_rust_path(relative_path: str) -> bool:
    normalized = Path(relative_path).as_posix()
    path = Path(normalized)

    if "tests" in path.parts:
        return True

    basename = path.name
    if basename == "tests.rs":
        return True
    if basename.startswith("test_") and basename.endswith(".rs"):
        return True
    return bool(basename.endswith("_test.rs"))


def build_analysis_source(
    *,
    path: Path,
    relative_path: str,
) -> AnalysisSource:
    raw_text = path.read_text(encoding="utf-8")
    analysis_text, hash_bytes = _build_sanitized_views(
        raw_text,
        relative_path=relative_path,
    )

    return AnalysisSource(
        relative_path=relative_path,
        raw_text=raw_text,
        analysis_text=analysis_text.strip(),
        content_sha256=sha256(hash_bytes).hexdigest(),
    )


def sanitize_rust_source_for_analysis(
    source_text: str,
    *,
    relative_path: str,
) -> str:
    analysis_text, _ = _build_sanitized_views(
        source_text,
        relative_path=relative_path,
    )
    return analysis_text


def _build_sanitized_views(
    source_text: str,
    *,
    relative_path: str,
) -> tuple[str, bytes]:
    source_bytes = source_text.encode("utf-8")
    parser = build_parser(RUST_LANGUAGE)
    tree = parser.parse(source_bytes)
    root = tree.root_node

    if root.has_error:
        raise ValueError(f"Failed to parse Rust source without errors: {relative_path}")

    spans = _collect_strip_spans(root, source_bytes)

    line_preserving = bytearray(source_bytes)
    for start_byte, end_byte in spans:
        for index in range(start_byte, end_byte):
            if line_preserving[index] in (0x0A, 0x0D):
                continue
            line_preserving[index] = 0x20

    return line_preserving.decode("utf-8"), _strip_spans(source_bytes, spans)


def _collect_strip_spans(
    container_node: Node,
    source: bytes,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []

    if container_node.type not in _CONTAINER_NODE_TYPES:
        return spans

    children = list(container_node.named_children)
    index = 0

    while index < len(children):
        child = children[index]

        if child.type == "attribute_item":
            first_attribute = child
            attribute_items: list[Node] = []

            while index < len(children) and children[index].type == "attribute_item":
                attribute_items.append(children[index])
                index += 1

            if index >= len(children):
                break

            item = children[index]
            if _should_strip_item(attribute_items, source):
                spans.append((first_attribute.start_byte, item.end_byte))
            else:
                spans.extend(_collect_nested_strip_spans(item, source))

            index += 1
            continue

        spans.extend(_collect_nested_strip_spans(child, source))
        index += 1

    return spans


def _strip_spans(
    source: bytes,
    spans: list[tuple[int, int]],
) -> bytes:
    if not spans:
        return source

    merged_spans = _merge_spans(spans)
    chunks: list[bytes] = []
    current = 0

    for start_byte, end_byte in merged_spans:
        chunks.append(source[current:start_byte])
        current = end_byte

    chunks.append(source[current:])
    return b"".join(chunks)


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []

    for start_byte, end_byte in sorted(spans):
        if not merged or start_byte > merged[-1][1]:
            merged.append((start_byte, end_byte))
            continue

        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end_byte))

    return merged


def _collect_nested_strip_spans(
    node: Node,
    source: bytes,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []

    for child in node.named_children:
        if child.type in _CONTAINER_NODE_TYPES:
            spans.extend(_collect_strip_spans(child, source))

    return spans


def _should_strip_item(
    attribute_items: list[Node],
    source: bytes,
) -> bool:
    return any(
        _is_test_attribute_item(attribute_item, source)
        for attribute_item in attribute_items
    )


def _is_test_attribute_item(
    attribute_item: Node,
    source: bytes,
) -> bool:
    if attribute_item.type != "attribute_item":
        return False

    attribute_node = attribute_item.child_by_field_name("attr")
    if attribute_node is None:
        for child in attribute_item.named_children:
            if child.type == "attribute":
                attribute_node = child
                break

    if attribute_node is None:
        return False

    normalized = "".join(node_text(source, attribute_node).split())
    return normalized == "cfg(test)" or normalized == "test"
