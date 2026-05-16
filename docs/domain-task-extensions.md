# Domain Task Extensions

## Purpose

Different agent teams need different task data. A novel-writing team, software team, research team, design team, and data team cannot all be represented well by only generic run logs.

This is a backend concern. The backend should keep a stable generic task/run model while supporting domain-specific task state, correction, and view payloads.

## Core Principle

Use a shared execution backbone:

- Workspace
- AgentTeam
- Task
- TaskStep
- AgentRun
- RunEvent
- Approval
- Artifact

Add domain extensions on top:

- `team_type`
- `domain_type`
- `domain_projects`
- `domain_items`
- `review_comments`
- `revision_requests`
- task view payloads

The frontend can later render different views, but the backend owns the data model, permissions, state, and correction workflow.

## Team Type

`agent_teams` should include a team type.

Examples:

- `generic`
- `novel`
- `software`
- `research`
- `design`
- `data`
- `operations`

Suggested fields:

```text
agent_teams
- id
- workspace_id
- name
- team_type
- manager_agent_profile_id
- config
- coordination_rules
- status
```

`team_type` helps the orchestrator choose default planning behavior, required domain objects, and task view type.

## Task Domain Type

`tasks` can include `domain_type` and domain state.

Suggested fields:

```text
tasks
- id
- workspace_id
- agent_team_id
- domain_type
- title
- status
- generic_state
- domain_state
```

`generic_state` stores shared state such as progress, current step, current agent, and runtime status.

`domain_state` stores lightweight domain status that is useful to query without loading all domain items.

## Domain Projects

`domain_projects` represent a workspace-scoped body of domain work.

Examples:

- a novel project
- a software repository/project
- a research project
- a design project
- a data analysis project

Suggested fields:

```text
domain_projects
- id
- workspace_id
- domain_type
- name
- description
- status
- metadata
- created_at
- updated_at
```

## Domain Items

`domain_items` are project-owned domain objects.

Suggested fields:

```text
domain_items
- id
- workspace_id
- domain_project_id
- task_id
- item_type
- title
- status
- content
- metadata
- created_at
- updated_at
```

Examples by domain:

### Novel

- `story_bible`
- `outline`
- `character`
- `location`
- `chapter`
- `scene`
- `draft`
- `continuity_issue`

### Software

- `repository`
- `issue`
- `implementation_plan`
- `diff`
- `test_result`
- `review_comment`

### Research

- `research_brief`
- `source`
- `finding`
- `claim`
- `citation`
- `report_section`

### Design

- `brief`
- `moodboard`
- `asset`
- `variant`
- `design_review`
- `export`

### Data

- `dataset`
- `processing_step`
- `metric`
- `chart`
- `insight`
- `report_section`

## Task View API

The backend should expose a task view endpoint that returns both generic task state and domain-specific payload.

Endpoint:

```text
GET /api/workspaces/{workspace_id}/tasks/{task_id}/view
```

Example response:

```json
{
  "task": {
    "id": "task_123",
    "title": "Write chapter 6",
    "status": "running"
  },
  "team_type": "novel",
  "domain_type": "novel",
  "view_type": "novel_task_detail",
  "generic": {
    "progress": 62,
    "current_step": "revision",
    "runs": [],
    "approvals": [],
    "artifacts": []
  },
  "domain": {
    "project": {},
    "items": [],
    "sections": [
      "outline",
      "chapters",
      "characters",
      "editorial_comments",
      "continuity_issues"
    ]
  }
}
```

Possible `view_type` values:

- `generic_task_detail`
- `novel_task_detail`
- `software_task_detail`
- `research_task_detail`
- `design_task_detail`
- `data_task_detail`

The view endpoint must enforce workspace permissions and domain item ownership.

## Correction Model

Users need to correct agent work as structured backend state, not only as chat messages.

### Review Comments

Suggested fields:

```text
review_comments
- id
- workspace_id
- task_id
- domain_project_id
- domain_item_id
- artifact_id
- author_user_id
- author_agent_run_id
- target_type
- target_id
- comment
- status
- created_at
- resolved_at
```

### Revision Requests

Suggested fields:

```text
revision_requests
- id
- workspace_id
- task_id
- domain_project_id
- domain_item_id
- artifact_id
- requested_by_user_id
- assigned_agent_profile_id
- instruction
- status
- created_at
- completed_at
```

Statuses:

- `open`
- `queued`
- `running`
- `completed`
- `rejected`
- `cancelled`

## Correction Flow

1. User comments on a task, artifact, or domain item.
2. Backend creates `review_comment` or `revision_request`.
3. Orchestration validates workspace, target object, and agent permission.
4. Backend creates a task step or agent run for the revision.
5. Worker executes the revision through OpenAI Agents SDK.
6. Agent receives structured target context and instruction.
7. Output updates the target domain item or creates a new artifact.
8. Backend appends run events and marks revision complete.

## Novel Example

Project:

```text
domain_projects
- domain_type: novel
- name: 雾港档案
- status: drafting
```

Items:

```text
domain_items
- item_type: story_bible
- item_type: outline
- item_type: character
- item_type: chapter
- item_type: scene
- item_type: continuity_issue
```

Revision request:

```text
target: chapter 6
instruction: Rewrite this chapter with a darker tone and preserve the red lighthouse clue.
assigned_agent_profile_id: chapter_writer
```

## Software Example

Project:

```text
domain_projects
- domain_type: software
- name: chaincloud-agent-team
```

Items:

```text
domain_items
- item_type: issue
- item_type: implementation_plan
- item_type: diff
- item_type: test_result
- item_type: review_comment
```

Revision request:

```text
target: diff_123
instruction: Add workspace isolation tests for this API path and rerun targeted tests.
assigned_agent_profile_id: software_engineer
```

## Research Example

Items:

```text
domain_items
- item_type: source
- item_type: finding
- item_type: claim
- item_type: citation
- item_type: report_section
```

Revision request:

```text
target: claim_456
instruction: This claim is weak. Find a stronger source or mark it unsupported.
assigned_agent_profile_id: research_agent
```

## Backend Rules

- Generic task/run state remains the execution source of truth.
- Domain items are workspace-scoped.
- Domain items should link to task/run/artifact when produced by agents.
- Corrections are structured records.
- Agents receive corrections as structured context.
- Frontend should not invent domain state; it should render backend view payloads.
- Mature domains can later move from generic `domain_items` to dedicated tables.

## MVP Recommendation

Do not build every domain immediately.

For backend MVP:

- add `team_type` to agent teams
- add `domain_type` and `domain_state` to tasks
- add flexible `domain_projects`
- add flexible `domain_items`
- add `review_comments`
- add `revision_requests`
- add task view endpoint

Start with `generic` and one prototype domain later, likely `research` or `novel`.
