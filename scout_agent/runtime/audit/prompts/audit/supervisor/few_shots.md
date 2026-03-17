## Few-Shot Example

### USER
Audit the current file: `/workspace/src/vault.rs`

### ASSISTANT
Tool Call: `read_file(file_path="/workspace/src/vault.rs", offset=0, limit=100)`

### TOOL OUTPUT
```rust
pub fn deposit(e: Env, user: Address, amount: Vec<i128>) {
    let mut total = 0;
    for a in amount.iter() {
        total += a;
    }
    let mut balance = balances.get(user).unwrap_or(0);
    balances.set(user, balance + total);
}
```

### ASSISTANT
Tool Call: `task(subagent_type="collection_validation", description="Specialist Brief:\n- File: /workspace/src/vault.rs\n- Target: deposit\n- Pattern: Input Vec is summed directly into total.\n- Question: Can duplicate elements inflate the resulting balance update?")`

### TOOL OUTPUT
`{"status":"VULNERABLE","finding":{"pattern":"Unvalidated collection input","severity":"MEDIUM","location":"/workspace/src/vault.rs:3","description":"deposit sums every element from the input vector without duplicate validation.","evidence":"/workspace/src/vault.rs lines 3-4 add every element in amount into total with no uniqueness check before storage."}}`

### ASSISTANT
`{"findings":[{"pattern":"Unvalidated collection input","severity":"MEDIUM","location":"/workspace/src/vault.rs:3","description":"deposit sums every element from the input vector without duplicate validation.","evidence":"/workspace/src/vault.rs lines 3-4 add every element in amount into total with no uniqueness check before storage."}]}`
