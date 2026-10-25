# Repository skills

Read the relevant integration skill before editing its implementation. These are OpsMesh maintenance instructions, not runtime plugins or permissions to run tests.

| Skill | Integration | Locked version |
| --- | --- | --- |
| [openai-knowledge](skills/openai-knowledge/SKILL.md) | Native Agents, sessions, MCP, sandbox and models | openai-agents 0.22.3 |
| [adapter-qq](skills/adapter-qq/SKILL.md) | QQ official botpy client, events, native APIs and messages | qq-botpy 1.2.1 |
| [dingtalk-sdk](skills/dingtalk-sdk/SKILL.md) | Stream callbacks, robots, cards and OpenAPI | dingtalk-stream 0.24.4b1 |

The DingTalk SDK version is a deliberate upstream prerelease for its asynchronous lifecycle and shutdown behavior. It remains pinned in `uv.lock`; do not replace it with an older stable version without reviewing the lifecycle contract.

Skills refer to official upstream documentation and the installed source. The lockfile determines available interfaces. Keep secrets, captured platform payloads and local acceptance data outside this directory.

Both platform adapters inherit the OpsMesh Adapter in `gateway/adapters/base.py`.
QQ uses the installed Tencent `qq-botpy` package directly. Its SDK.json records
the pinned official package and repository; no platform source is vendored.
Follow the pinned versions in uv.lock. The host requires cryptography >=50.0.2.

Public extension contracts are owned by opsmesh.contracts. Framework modules must never import the generated opsmesh_sdk. Generate SDK runtime bindings and stubs from those definitions in the separately staged SDK project.
