from typing import Final

import tree_sitter_rust as tsr
from tree_sitter import Language, Node, Parser

RUST_LANGUAGE: Final[Language] = Language(tsr.language())


def build_parser(language: Language) -> Parser:
    # tree-sitter Python bindings have changed constructor style across versions.
    try:
        return Parser(language)
    except TypeError:
        parser = Parser()
        parser.language = language
        return parser


def node_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")
