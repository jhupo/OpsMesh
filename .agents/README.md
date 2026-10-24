# Repository skills

Read the relevant integration skill before editing its implementation. These are OpsMesh maintenance instructions, not runtime plugins or permissions to run tests.

| Skill | Integration | Locked version |
| --- | --- | --- |
| [openai-knowledge](skills/openai-knowledge/SKILL.md) | Native Agents, sessions, MCP, sandbox and models | openai-agents 0.22.3 |
| [adapter-qq](skills/adapter-qq/SKILL.md) | QQ transport, native APIs and messages | nonebot-adapter-qq 1.7.3; nonebot2 2.5.0 |
| [nonebot](skills/nonebot/SKILL.md) | Driver/Adapter boundaries and safe platform integration | nonebot2 2.5.0 |
| [dingtalk-sdk](skills/dingtalk-sdk/SKILL.md) | Stream callbacks, robots, cards and OpenAPI | dingtalk-stream 0.24.4b1 |

The DingTalk SDK version is a deliberate upstream prerelease for its asynchronous lifecycle and shutdown behavior. It remains pinned in `uv.lock`; do not replace it with an older stable version without reviewing the lifecycle contract.

Skills refer to official upstream documentation and the installed source. The lockfile determines available interfaces. Keep secrets, captured platform payloads and local acceptance data outside this directory.

QQ 1.7.3 is installed from commit `e79e4a0830b55107752e706b2807d6651d29d463` on `codex/adapter-qq-crypto50`. It is based on upstream tag `v1.7.3` (`9bf584471e4fa658f276903481b99373c43016fd`) with only the cryptography upper bound raised from `<49` to `<51`. No upstream Python code or license notice changes. The host requires cryptography >=50.0.2.
