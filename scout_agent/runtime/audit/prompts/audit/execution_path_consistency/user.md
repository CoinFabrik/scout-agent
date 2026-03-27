Audit the repository for execution path consistency issues using only the provided read-only tools.

Instructions:
- Audit the full in-scope Rust repository.
- Focus only on bypassable restrictions caused by containment or inconsistent paths for the same operation.
- Do not delegate or spawn specialists.
- Use `grep` to narrow the search, then inspect code with `read_file`.
- Reads MUST be sequential per file. Your next read for a specific file must start at the offset where your previous read for that file ended. Overlapping reads are blocked to prevent loops.
- Pass absolute paths to `read_file` and to the optional `path` argument on `grep`.
- Return only one JSON object with the shape `{"findings":[...]}`.
- Return `{"findings":[]}` if no valid issues are proven.
- For every finding, include `pattern`, `severity`, `location`, `description`, and `evidence`.
- Return only concrete findings in the structured response.
