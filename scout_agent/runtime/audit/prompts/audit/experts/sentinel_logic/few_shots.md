## Few-Shot Example

### USER
Specialist Brief:
- File: `/workspace/src/rewards.rs`
- Target: `claim_reward`
- Pattern: `reward_id` may use an uninitialized sentinel.
- Question: Is the sentinel checked before lookup?

### ASSISTANT
Tool Call: `grep(pattern="UNINITIALIZED_REWARD_ID|u32::MAX", path="/workspace/src")`

### TOOL OUTPUT
`/workspace/src/storage.rs:45: const UNINITIALIZED_REWARD_ID: u32 = u32::MAX;`

### ASSISTANT
Tool Call: `read_file(file_path="/workspace/src/rewards.rs", offset=0, limit=100)`

### TOOL OUTPUT
```text
1: pub fn claim_reward(e: Env, reward_id: u32) {
2:     let reward = rewards.get(reward_id).unwrap();
3:     payout_reward(e, reward);
4: }
```

### ASSISTANT
`{"status":"VULNERABLE","finding":{"pattern":"Missing sentinel guard","severity":"MEDIUM","location":"/workspace/src/rewards.rs:2","description":"claim_reward uses reward_id without checking the uninitialized sentinel.","evidence":"/workspace/src/storage.rs defines UNINITIALIZED_REWARD_ID as u32::MAX, but /workspace/src/rewards.rs line 2 performs the lookup directly with no sentinel guard."}}`
