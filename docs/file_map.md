# File Map

| Path | Responsibility |
| --- | --- |
| [scout_agent/app/main.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/app/main.py) | CLI entrypoint and command dispatch |
| [scout_agent/app/cli.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/app/cli.py) | argument parsing |
| [scout_agent/app/console_reporting.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/app/console_reporting.py) | plain text summaries, errors, and progress reporters |
| [scout_agent/app/extract_facts.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/app/extract_facts.py) | `extract-facts` command wiring |
| [scout_agent/app/audit.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/app/audit.py) | `audit` command wiring |
| [scout_agent/configuration/settings.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/configuration/settings.py) | path, model, mode, and parallelism resolution |
| [scout_agent/configuration/scout_config.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/configuration/scout_config.py) | `scout.json` loading |
| [scout_agent/runtime/extract/pipeline.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/extract/pipeline.py) | end-to-end extraction flow |
| [scout_agent/runtime/extract/execution.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/extract/execution.py) | bounded parallel file execution |
| [scout_agent/runtime/extract/facts_extractor.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/extract/facts_extractor.py) | file-level fact extraction calls |
| [scout_agent/runtime/audit/graph.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/audit/graph.py) | LangGraph assembly and node logic |
| [scout_agent/runtime/audit/supervisor.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/audit/supervisor.py) | supervisor prompt and decision validation |
| [scout_agent/runtime/audit/experts.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/audit/experts.py) | expert execution |
| [scout_agent/runtime/audit/reducer.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/audit/reducer.py) | batch reduction and dedupe |
| [scout_agent/runtime/audit/report_writer.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/audit/report_writer.py) | `REPORT.md` generation |
| [scout_agent/runtime/source/discovery.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/source/discovery.py) | production Rust file discovery |
| [scout_agent/runtime/source/rust_parser.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/runtime/source/rust_parser.py) | AST parsing |
| [scout_agent/domain/facts.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/domain/facts.py) | typed facts document models |
| [scout_agent/domain/audit.py](/Users/josegarcia/Desktop/scout-agent/scout_agent/domain/audit.py) | audit state, delegations, findings, and result models |
