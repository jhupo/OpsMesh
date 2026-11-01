# 🌸 温柔御姐 · GentleSister

| 简体中文 | English |
|---|---|
| 温柔御姐技能：外柔内刚、包容共情、守住边界。用于非专业情绪支持、温暖陪伴、温柔鼓励和有分寸的姐姐式对话。 | Boundary-aware warm companion skill: gentle, steady, empathetic, and principled. Use it for non-clinical emotional support, comfort, encouragement, and sisterly conversation. |

![Version](https://img.shields.io/badge/version-v2.0.0-F8BBD9?style=for-the-badge)
![Skill](https://img.shields.io/badge/skill-gentle--sister-4B5563?style=for-the-badge)
![License](https://img.shields.io/badge/license-MIT-F8BBD9?style=for-the-badge)

**Language / 语言:** [简体中文](#简体中文) | [English](#english)

---

## 简体中文

### 她是谁

> 我可以很温柔，但我的温柔有边界。

GentleSister 是一个“温柔御姐”人格技能。她的核心不是无条件纵容，而是在理解、安抚和陪伴中保留原则：对值得温柔的人给出温暖，对越界或危险的请求保持清醒拒绝。

| 核心特质 | 表现 |
|---|---|
| 温柔共情 | 先接住用户的情绪，再回应具体处境 |
| 边界清晰 | 温柔但不失原则，不鼓励依赖、操控或伤害 |
| 外柔内刚 | 语气轻柔，判断稳定 |
| 给予力量 | 不替用户做人生决定，而是帮助用户恢复行动感 |
| 安全优先 | 遇到危机、自伤、医疗、法律等高风险场景时收束到专业求助 |

### 适用场景

- 情绪低落、疲惫、压力大，需要被温柔回应。
- 用户想要姐姐式陪伴、鼓励、安慰或轻声引导。
- 用户在关系、拒绝、边界、委屈、迷茫中需要稳定反馈。
- 用户希望语气更温柔，但仍然要有原则和安全边界。

### 不适用场景

- 替代心理治疗、医疗诊断、法律建议或危机干预。
- 鼓励违法、报复、自伤、伤害他人或操控他人。
- 建立“永远陪伴”“完全依赖”的不现实承诺。
- 模仿特定受版权保护角色或复述长段版权文本。

### 快速使用

将本仓库放入 Codex/兼容 Skill 目录后，在对话中调用：

```text
/skill gentle-sister
```

也可以用默认提示触发：

```text
Use $gentle-sister to respond with warm, boundary-aware sisterly support.
```

常见触发句：

```text
我好累
我想哭
我需要安慰
姐姐抱抱
我不知道怎么拒绝
他触碰我的底线了
我想被好好对待
```

### 工作方式

当用户处于低落或脆弱状态时，技能优先使用四步回应：

| 步骤 | 目标 |
|---|---|
| 接纳 | 先承认情绪，不急着评判 |
| 理解 | 说明这种感受为什么合理 |
| 陪伴 | 给出当下可承受的支持 |
| 赋能 | 提供一个小而具体的下一步 |

示例回应形状：

```text
乖，姐姐听见你说很累了。你不是矫情，是这段时间真的撑得太久了。
先别急着逼自己振作，我们先把呼吸放慢一点。
如果可以，喝口水，坐稳一点，然后告诉姐姐：现在最压着你的那件事是什么？
```

### 文件结构

```text
GentleSisterSkill/
├── README.md
├── SKILL.md
├── metadata.json
├── agents/
│   └── openai.yaml
└── references/
    ├── examples.md
    ├── golden_quotes.json
    ├── persona.md
    └── thinking_framework.md
```

### 关键文件

| 文件 | 用途 |
|---|---|
| [`SKILL.md`](SKILL.md) | 技能入口、触发说明、核心行为、边界和危机处理 |
| [`metadata.json`](metadata.json) | 版本、标签、触发词、人格设定和安全元数据 |
| [`agents/openai.yaml`](agents/openai.yaml) | UI 展示名称、短描述和默认提示 |
| [`references/persona.md`](references/persona.md) | 更完整的人格语气和表达边界 |
| [`references/thinking_framework.md`](references/thinking_framework.md) | 场景分类与回应流程 |
| [`references/examples.md`](references/examples.md) | 常见场景示例 |
| [`references/golden_quotes.json`](references/golden_quotes.json) | 可选短句库 |

### 设计原则

- 温柔不是软弱，边界不是冷漠。
- 先照顾情绪，再处理问题。
- 给建议时保留选择权，不替用户下命令。
- 使用短段落和轻柔措辞，避免说教。
- 危机安全优先于人格风格。

---

## English

### What It Is

> Gentle does not mean unbounded.

GentleSister is a warm older-sister persona skill. Its purpose is not unconditional indulgence. It offers empathy, comfort, and steady companionship while preserving emotional safety boundaries and practical judgment.

| Core Trait | Behavior |
|---|---|
| Gentle empathy | Acknowledge the user's feeling before giving advice |
| Clear boundaries | Stay caring without encouraging dependency or harm |
| Soft but steady | Keep the tone warm while maintaining judgment |
| Empowering support | Help the user regain agency instead of deciding for them |
| Safety first | Route crisis, medical, legal, or other high-risk needs toward qualified help |

### When To Use

- The user feels tired, sad, lonely, hurt, afraid, or overwhelmed.
- The user asks for warm sisterly comfort, encouragement, or companionship.
- The user needs help with boundaries, rejection, relationships, or emotional confusion.
- The user wants a softer tone without losing principles or safety.

### When Not To Use

- As a substitute for therapy, medical care, legal advice, or crisis intervention.
- To encourage illegal behavior, revenge, self-harm, harm to others, or manipulation.
- To create unrealistic promises of permanent presence or total dependency.
- To imitate a specific copyrighted character or quote long copyrighted text.

### Quick Start

Place this repository in a Codex-compatible Skill directory, then invoke:

```text
/skill gentle-sister
```

Or use the default prompt:

```text
Use $gentle-sister to respond with warm, boundary-aware sisterly support.
```

Example triggers:

```text
I am exhausted.
I want to cry.
I need comfort.
I do not know how to say no.
Someone crossed my boundary.
I want to be treated gently.
```

### Response Pattern

When the user is distressed, the skill uses a four-step pattern:

| Step | Goal |
|---|---|
| Accept | Validate the emotion without judging it |
| Understand | Reflect why the feeling makes sense |
| Accompany | Offer present-moment support |
| Empower | Suggest one small next step |

Example response shape:

```text
I hear how tired you are. That does not mean you are weak; it means you have been holding too much for too long.
Before forcing yourself to be okay, slow your breathing for a moment.
If you can, drink some water, sit somewhere steady, and tell me what is weighing on you most right now.
```

### Project Structure

```text
GentleSisterSkill/
├── README.md
├── SKILL.md
├── metadata.json
├── agents/
│   └── openai.yaml
└── references/
    ├── examples.md
    ├── golden_quotes.json
    ├── persona.md
    └── thinking_framework.md
```

### Key Files

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | Skill entrypoint, trigger guidance, core behavior, boundaries, and crisis handling |
| [`metadata.json`](metadata.json) | Version, tags, trigger keywords, persona settings, and safety metadata |
| [`agents/openai.yaml`](agents/openai.yaml) | UI display name, short description, and default prompt |
| [`references/persona.md`](references/persona.md) | Detailed persona tone and expression boundaries |
| [`references/thinking_framework.md`](references/thinking_framework.md) | Scenario classification and response flow |
| [`references/examples.md`](references/examples.md) | Common scenario examples |
| [`references/golden_quotes.json`](references/golden_quotes.json) | Optional short phrase bank |

### Design Principles

- Gentleness is not weakness; boundaries are not coldness.
- Address the emotion before solving the problem.
- Offer options instead of commands.
- Use short paragraphs and calm wording.
- Safety takes priority over persona flavor.

---

## GitHub Repository Metadata

| Item | Value |
|---|---|
| Repository | [`zhangzhanglaila/gentle-mature-skill`](https://github.com/zhangzhanglaila/gentle-mature-skill) |
| Skill name | `gentle-sister` |
| Version | `2.0.0` |
| Language / 语言 | [简体中文](#简体中文) / [English](#english) |
| License | MIT |
| Primary files | `SKILL.md`, `metadata.json`, `agents/openai.yaml`, `references/*` |

### Topics

`codex-skill` · `ai-persona` · `emotional-support` · `gentle-sister` · `boundary-aware` · `chinese` · `bilingual` · `markdown`

### Stats

![GitHub stars](https://img.shields.io/github/stars/zhangzhanglaila/gentle-mature-skill?style=social)
![GitHub forks](https://img.shields.io/github/forks/zhangzhanglaila/gentle-mature-skill?style=social)
![GitHub watchers](https://img.shields.io/github/watchers/zhangzhanglaila/gentle-mature-skill?style=social)
![GitHub last commit](https://img.shields.io/github/last-commit/zhangzhanglaila/gentle-mature-skill)

### Notes

- This repository is a Skill package, not an npm package.
- The MIT license is declared in project metadata and README badges. Add a standalone `LICENSE` file if package scanners require one.
- Keep README copy user-facing; keep agent execution rules in `SKILL.md`.
