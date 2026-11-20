# Memory and Workbench architecture

## What belongs where

| Layer | Purpose | Retention and ownership |
| --- | --- | --- |
| `SOUL.md` | Small, stable identity and operating principles | Maintained by the distribution; not a place for user facts or credentials |
| `memories/MEMORY.md` | Durable working facts and conventions | Local to the installed profile; compact manually if its character budget is reached |
| `memories/USER.md` | Stable preferences and user context | Local to the installed profile; keep only facts useful across sessions |
| Conversation and session history | Current turn and past conversation detail | Managed by Hermes, never included in this distribution |
| `skills/` | Repeatable procedures | The `vault-evidence` skill is shipped; unrelated user skills are not part of the payload |
| Markdown notes or a project ledger | Dated decisions, sources, or a daily log | Operator-owned folder selected explicitly; the sample vault is a teaching fixture |

A memory tool write changes disk state first. A system prompt may still contain the memory snapshot taken at session start. Use a fresh session to test persistence; a successful write does not update the model's weights or prove recall in a currently running session. When a fact changes, revise the curated statement and keep the underlying decision history separately. Do not sweep conversations or every note into the prompt. See the upstream [persistent memory guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) for snapshot and recall behavior.

Optional structured recall is a separate layer: a fact store can hold small durable statements, and session search can recover what was actually said. Neither is the vault's search index or a substitute for checking the original evidence. This starter relies on Hermes' built-in curated memory and session history; it does not configure a separate fact-store plugin or bundle a database. To try persistence, ask the starter in its own profile to save a harmless fictional preference, check its `memories/USER.md`, then start a new session and ask it to recall the preference. Verify the file as well as the model's answer. Keep each writer in its own Hermes home.

## Retrieval flow

1. Explicit operator-selected folder → bounded scan of regular UTF-8 Markdown. Unsupported, linked, or oversized files are excluded and counted; draft off-vault content is not searched by default.
2. Sections, titles, and aliases → temporary SQLite FTS5 index in process memory. The browser reuses it across queries; standalone `search` pays the build cost each invocation. The index is separate from Hermes' built-in memory.
3. Distinctive lexical terms → ranked candidate paths, section lines, and SHA-256 digests. Not semantic similarity, and a match is not necessarily relevant. There is no remote embedding or external model call in the Workbench.
4. Chosen path → bounded current excerpt. Search-derived reads pin the digest; an edit requires re-searching or refreshing. The CLI direct read does not build the index. A digest identifies the source bytes, not truth or authority.
5. Wikilinks and supported Markdown links → directed graph. The route finder checks for file inventory changes before tracing and verifies the involved note bytes. It does not validate Obsidian plugins or all anchor syntax. Prefer fully qualified links when basenames collide.

## Maintenance and privacy

- The Workbench serves only on `127.0.0.1`, uses a session-specific browser token, and displays Markdown as text. It is not a multi-user server or a sandbox against other local software. Never tunnel it, host it on the internet, or copy the launch address. Close it between uses. Local-only browsing does not imply that an assistant's separate provider request remains on-device.
- There are no workers, cron jobs, hooks, chat bridges, analytics, automatic writebacks, backup services or cloud-memory credentials in this distribution. Add integrations only in your own profile, after verifying their permissions. Keep the original project vault and backups outside the starter repository.
- `health` reports local index drift and link warnings. Optional `--expect-maps` checks the starter map convention; it is not appropriate for arbitrary vaults. The UI does not know remote sync status. Rebuild after edits; verify important links and authority in the source editor. Keep backup checks separate.
- Use generic or invented notes for demos. Release from a fresh allowlisted tree; review source and reachable history instead of filtering a profile export or archive. Do not add the user's memory, chat logs, operational paths, credentials, or integration state.

## Provenance

This repository provides a generic agent personality, one retrieval skill and a standalone adapted Workbench. It runs on, but does not redistribute, upstream Hermes Agent. The examples are invented; their performance and recall are not measurements of a real deployment. No open-source license for the starter is declared at this time.
