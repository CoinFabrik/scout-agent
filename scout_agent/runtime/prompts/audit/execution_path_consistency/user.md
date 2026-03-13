Audit the repository for execution path consistency issues using only the provided read-only tools.

Instructions:
- Audit the full in-scope Rust repository.
- Focus only on bypassable restrictions caused by containment or inconsistent paths for the same operation.
- Do not delegate or spawn specialists.
- Use `read_fact_entry` when extracted facts can narrow the search.
- Return only one JSON object with the shape `{"findings":[...]}`.
- Return `{"findings":[]}` if no valid issues are proven.
- For every finding, include `pattern`, `severity`, `location`, `description`, and `evidence`.
- Return only deduped concrete findings in the structured response.
