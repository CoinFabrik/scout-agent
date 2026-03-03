from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from tree_sitter import Language, Node, Parser
import tree_sitter_rust as tsr

FunctionKind = Literal["function", "method"]
FunctionVisibility = Literal["public", "private", "unknown"]

RUST_LANGUAGE: Final[Language] = Language(tsr.language())


@dataclass(frozen=True, slots=True)
class ParsedRustFunction:
    function_id: str
    name: str
    kind: FunctionKind
    visibility: FunctionVisibility
    line_start: int
    line_end: int
    signature: str
    source_text: str
    impl_target: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedRustFile:
    relative_path: str
    source_text: str
    functions: list[ParsedRustFunction]


def parse_rust_source(source: bytes, *, relative_path: str) -> ParsedRustFile:
    parser = _build_parser()
    tree = parser.parse(source)
    root = tree.root_node

    if root.has_error:
        raise ValueError(f"Failed to parse Rust source without errors: {relative_path}")

    source_text = source.decode("utf-8")
    functions = _collect_functions(
        container_node=root,
        source=source,
        relative_path=relative_path,
    )

    functions.sort(
        key=lambda item: (item.line_start, item.line_end, item.name, item.kind)
    )
    return ParsedRustFile(
        relative_path=relative_path,
        source_text=source_text,
        functions=functions,
    )


def _build_parser() -> Parser:
    # tree-sitter Python bindings have changed constructor style across versions.
    try:
        return Parser(RUST_LANGUAGE)
    except TypeError:
        parser = Parser()
        parser.language = RUST_LANGUAGE
        return parser


def _collect_functions(
    *,
    container_node: Node,
    source: bytes,
    relative_path: str,
) -> list[ParsedRustFunction]:
    functions: list[ParsedRustFunction] = []

    for child in container_node.named_children:
        if child.type == "function_item":
            functions.append(
                _build_parsed_function(
                    function_node=child,
                    source=source,
                    relative_path=relative_path,
                    kind="function",
                    impl_target=None,
                )
            )
            continue

        if child.type == "impl_item":
            functions.extend(
                _extract_impl_methods(
                    impl_node=child,
                    source=source,
                    relative_path=relative_path,
                )
            )
            continue

        if child.type == "mod_item":
            functions.extend(
                _extract_inline_module_functions(
                    mod_node=child,
                    source=source,
                    relative_path=relative_path,
                )
            )

    return functions


def _extract_inline_module_functions(
    *,
    mod_node: Node,
    source: bytes,
    relative_path: str,
) -> list[ParsedRustFunction]:
    body_node = mod_node.child_by_field_name("body")
    if body_node is None:
        return []

    return _collect_functions(
        container_node=body_node,
        source=source,
        relative_path=relative_path,
    )


def _extract_impl_methods(
    *,
    impl_node: Node,
    source: bytes,
    relative_path: str,
) -> list[ParsedRustFunction]:
    impl_target = _extract_impl_target(impl_node, source)
    methods: list[ParsedRustFunction] = []

    for child in impl_node.named_children:
        if child.type != "declaration_list":
            continue

        for declaration in child.named_children:
            if declaration.type != "function_item":
                continue

            methods.append(
                _build_parsed_function(
                    function_node=declaration,
                    source=source,
                    relative_path=relative_path,
                    kind="method",
                    impl_target=impl_target,
                )
            )

    return methods


def _extract_impl_target(impl_node: Node, source: bytes) -> str | None:
    type_node = impl_node.child_by_field_name("type")
    if type_node is None:
        return None

    text = _node_text(source, type_node).strip()
    return text or None


def _build_parsed_function(
    *,
    function_node: Node,
    source: bytes,
    relative_path: str,
    kind: FunctionKind,
    impl_target: str | None,
) -> ParsedRustFunction:
    name_node = function_node.child_by_field_name("name")
    if name_node is None:
        raise ValueError(f"Function node is missing a name in {relative_path}")

    name = _node_text(source, name_node).strip()
    if not name:
        raise ValueError(f"Function node has an empty name in {relative_path}")

    line_start = function_node.start_point[0] + 1
    line_end = function_node.end_point[0] + 1
    body_node = function_node.child_by_field_name("body")

    if body_node is None:
        signature = _node_text(source, function_node).strip()
    else:
        signature = (
            source[function_node.start_byte : body_node.start_byte]
            .decode("utf-8")
            .rstrip()
        )

    source_text = _node_text(source, function_node).rstrip()
    visibility = _extract_visibility(function_node, source)

    return ParsedRustFunction(
        function_id=f"{relative_path}::{name}#L{line_start}",
        name=name,
        kind=kind,
        visibility=visibility,
        line_start=line_start,
        line_end=line_end,
        signature=signature,
        source_text=source_text,
        impl_target=impl_target,
    )


def _extract_visibility(function_node: Node, source: bytes) -> FunctionVisibility:
    for child in function_node.children:
        if child.type != "visibility_modifier":
            continue

        raw_visibility = _node_text(source, child).strip()
        if raw_visibility.startswith("pub"):
            return "public"
        return "unknown"

    return "private"


def _node_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")
