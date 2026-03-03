# Architecture Mental Model

Think of Scout-Agent as two consecutive transformations.

## Step 1: Source To Facts

Project files go through:

- discovery
- source filtering
- Rust parsing
- file fact extraction
- `FACTS.yaml` writing

The output is a typed inventory of files and functions.

## Step 2: Facts To Findings

`FACTS.yaml` initializes an `AuditState`. The audit graph then:

- focuses one file at a time
- asks the supervisor whether the file is complete or needs expert work
- executes only the requested expert checks
- reduces the results into findings and memory for that file
- advances until every file is complete

## Mental Shortcut

- extraction answers "what is here?"
- audit answers "what concrete checks are still needed, and what findings survive verification?"

## Operational Properties

- one invocation produces one output artifact
- there is no hidden run memory between invocations
- console output reflects the current run directly
