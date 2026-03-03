from scout_agent.runtime.source.rust_parser import parse_rust_source


def test_rust_parser_handles_nested_modules_and_impls() -> None:
    parsed = parse_rust_source(
        b"""mod nested {
    pub fn inner() {
        let _ = 1;
    }

    impl Vault {
        pub fn deposit(amount: i128) {
            let _ = amount;
        }
    }

    mod deeper {
        fn secret() {
            let _ = 2;
        }
    }
}
""",
        relative_path="contracts/gateway.rs",
    )

    assert [
        (fn.name, fn.kind, fn.visibility, fn.impl_target)
        for fn in parsed.functions
    ] == [
        ("inner", "function", "public", None),
        ("deposit", "method", "public", "Vault"),
        ("secret", "function", "private", None),
    ]


def test_rust_parser_rejects_malformed_rust() -> None:
    try:
        parse_rust_source(b"pub fn broken(", relative_path="contracts/broken.rs")
    except ValueError as exc:
        assert "Failed to parse Rust source without errors" in str(exc)
    else:
        raise AssertionError("Expected parse_rust_source to raise ValueError")

