## Few-Shot Example

### USER
Specialist Brief:
- File: `/workspace/src/rewards.rs`
- Target: `withdraw`
- Pattern: Principal is modified before time-dependent accrual is refreshed.
- Question: Are accrued rewards updated before state mutation?

### ASSISTANT
Tool Call: `read_file(file_path="/workspace/src/rewards.rs", offset=0, limit=100)`

### TOOL OUTPUT
```text
1: pub fn withdraw(user: Address, amount: i128) {
2:     let mut stake = stakes.get(&user);
3:     stake.amount -= amount;
4:     stake.last_update = e.ledger().timestamp();
5:     stakes.set(&user, stake);
6: }
```

### ASSISTANT
`{"status":"VULNERABLE","finding":{"pattern":"Time-state update after mutation","severity":"MEDIUM","location":"/workspace/src/rewards.rs:3","description":"withdraw mutates stake.amount before refreshing time-dependent state.","evidence":"/workspace/src/rewards.rs lines 3-4 update the principal before recording the current timestamp, so accrual is not settled before mutation."}}`
