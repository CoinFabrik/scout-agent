## Few-Shot Example

### USER
Specialist Brief:
- File: `/workspace/src/vault.rs`
- Target: `deposit`
- Pattern: A vector input is aggregated directly.
- Question: Can duplicates inflate the stored result?

### ASSISTANT
Tool Call: `read_file(file_path="/workspace/src/vault.rs", offset=0, limit=100)`

### TOOL OUTPUT
```text
1: pub fn deposit(e: Env, user: Address, amount: Vec<i128>) {
2:     let mut total = 0;
3:     for a in amount.iter() {
4:         total += a;
5:     }
6: }
```

### ASSISTANT
`{"status":"SAFE"}`
