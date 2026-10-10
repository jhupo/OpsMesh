# Python Code Review Skill

A [Claude Code](https://claude.com/claude-code) skill for analyzing Python code design, planning refactors, and reviewing changes using proven engineering principles and design patterns.

## What it does

When installed, this skill activates automatically whenever you ask Claude Code to:

- Review Python code or a PR/diff
- Plan a refactor or feature change
- Audit architecture or check code quality
- Identify code smells or technical debt
- Decide "is this the right pattern?" or "should I refactor this?"

It pulls from a curated set of design patterns (`references/patterns.md`) and engineering principles (`references/principles.md`) to ground its feedback.

## Contents

```
python-code-review/
├── SKILL.md                # Skill definition + workflow
└── references/
    ├── patterns.md         # Design patterns reference
    └── principles.md       # Engineering principles reference
```

## Installation

Skills live under `~/.claude/skills/<skill-name>/` for Claude Code to discover them.

### Option 1: Clone directly into your skills directory

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/Jallen64/python-code-review-skill.git ~/.claude/skills/python-code-review
```

### Option 2: Download a release archive

```bash
mkdir -p ~/.claude/skills/python-code-review
curl -L https://github.com/Jallen64/python-code-review-skill/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=1 -C ~/.claude/skills/python-code-review
```

### Option 3: Manual copy

1. Download or clone this repo anywhere.
2. Copy the `SKILL.md` file and the `references/` directory into `~/.claude/skills/python-code-review/`.

## Verifying installation

After installing, start a new Claude Code session and ask something like:

> Take a look at this Python file and tell me if it has any design issues.

Claude Code should announce that it's invoking the `python-code-review` skill before answering.

You can also list installed skills inside Claude Code with `/skills` (if available in your version) or just check:

```bash
ls ~/.claude/skills/python-code-review
```

## Updating

```bash
cd ~/.claude/skills/python-code-review
git pull
```

## Uninstalling

```bash
rm -rf ~/.claude/skills/python-code-review
```

## License

MIT — use, modify, and share freely.
