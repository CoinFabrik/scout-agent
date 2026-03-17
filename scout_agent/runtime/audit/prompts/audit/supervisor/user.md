Audit the current file: {current_file}

Instructions:
- Audit only the current file.
- Use built-in file tools only for the current file when needed.
- When calling `read_file` or `grep`, pass absolute paths only.
- Delegate to the three specialist subagents only when deeper review is needed.
- If you call a specialist, include the current file path, relevant facts, and the exact concern.
- Return only one JSON object with the shape `{{"findings":[...]}}`.
- If no specialists return findings, return `{{"findings":[]}}`.
- If specialists return findings, include only concrete findings and preserve all required finding fields: `pattern`, `severity`, `location`, `description`, and `evidence`.
