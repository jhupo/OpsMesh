# API 数据模型

此文件由当前 OpenAPI 生成。字段表和完整 JSON Schema 同时保留；`required` 是字段是否必须出现，`null` 是字段值能否为空，两者含义不同。

[返回接口索引](README.md)

<a id="schema-AcceptInvitationRequest"></a>

## AcceptInvitationRequest

The invitation token is the credential that opens the account.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `token` | string (uuid) | 是 | `format="uuid"` |
| `display_name` | string | 是 | `maxLength=100`; `minLength=1` |
| `password` | string | 是 | `maxLength=256`; `minLength=8` |

```json
{
  "additionalProperties": false,
  "description": "The invitation token is the credential that opens the account.",
  "properties": {
    "display_name": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Display Name",
      "type": "string"
    },
    "password": {
      "maxLength": 256,
      "minLength": 8,
      "title": "Password",
      "type": "string"
    },
    "token": {
      "format": "uuid",
      "title": "Token",
      "type": "string"
    }
  },
  "required": [
    "token",
    "display_name",
    "password"
  ],
  "title": "AcceptInvitationRequest",
  "type": "object"
}
```

<a id="schema-AcceptInvitationResponse"></a>

## AcceptInvitationResponse

The account and first session one accepted invitation produced.

``session_token`` is the secret the new account presents as
``X-Session-ID``; it is never stored, only its digest.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `user_id` | string (uuid) | 是 | `format="uuid"` |
| `session_token` | string (uuid) | 是 | `format="uuid"` |
| `expires_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "description": "The account and first session one accepted invitation produced.\n\n``session_token`` is the secret the new account presents as\n``X-Session-ID``; it is never stored, only its digest.",
  "properties": {
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "session_token": {
      "format": "uuid",
      "title": "Session Token",
      "type": "string"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "session_token",
    "expires_at"
  ],
  "title": "AcceptInvitationResponse",
  "type": "object"
}
```

<a id="schema-AdminUpdateUserRequest"></a>

## AdminUpdateUserRequest

The administrator-writable fields of one account.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `display_name` | string anyOf null | 否 | `anyOf=[{"maxLength": 100, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `work_root` | string anyOf null | 否 | `anyOf=[{"maxLength": 500, "type": "string"}, {"type": "null"}]` |
| `status` | string anyOf null | 否 | `anyOf=[{"maxLength": 20, "type": "string"}, {"type": "null"}]` |
| `platform_roles` | array&lt;string&gt; anyOf null | 否 | `anyOf=[{"items": {"type": "string"}, "maxItems": 8, "type": "array"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The administrator-writable fields of one account.",
  "properties": {
    "display_name": {
      "anyOf": [
        {
          "maxLength": 100,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Display Name"
    },
    "platform_roles": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 8,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Platform Roles"
    },
    "status": {
      "anyOf": [
        {
          "maxLength": 20,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Status"
    },
    "work_root": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Work Root"
    }
  },
  "title": "AdminUpdateUserRequest",
  "type": "object"
}
```

<a id="schema-AgentConfiguration"></a>

## AgentConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `mcp_connections` | array&lt;[MCPBinding](schemas.md#schema-MCPBinding)&gt; | 否 | `maxItems=100` |
| `name` | string | 是 | `maxLength=120`; `minLength=1` |
| `instructions` | string | 否 | `default=""`; `maxLength=100000` |
| `model` | [ModelConfiguration](schemas.md#schema-ModelConfiguration) | 是 | — |
| `tools` | array&lt;[opsmesh__orchestration__execution__contracts__ToolConfiguration](schemas.md#schema-opsmesh__orchestration__execution__contracts__ToolConfiguration)&gt; | 否 | `maxItems=100` |
| `handoffs` | array&lt;string&gt; | 否 | `maxItems=100` |
| `agent_tools` | array&lt;string&gt; | 否 | `maxItems=100` |
| `output_schema` | object anyOf null | 否 | `anyOf=[{"additionalProperties": true, "type": "object"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "agent_tools": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Agent Tools",
      "type": "array"
    },
    "handoffs": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Handoffs",
      "type": "array"
    },
    "instructions": {
      "default": "",
      "maxLength": 100000,
      "title": "Instructions",
      "type": "string"
    },
    "mcp_connections": {
      "items": {
        "$ref": "#/components/schemas/MCPBinding"
      },
      "maxItems": 100,
      "title": "Mcp Connections",
      "type": "array"
    },
    "model": {
      "$ref": "#/components/schemas/ModelConfiguration"
    },
    "name": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "output_schema": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output Schema"
    },
    "tools": {
      "items": {
        "$ref": "#/components/schemas/opsmesh__orchestration__execution__contracts__ToolConfiguration"
      },
      "maxItems": 100,
      "title": "Tools",
      "type": "array"
    }
  },
  "required": [
    "name",
    "model"
  ],
  "title": "AgentConfiguration",
  "type": "object"
}
```

<a id="schema-AgentGraphSpec"></a>

## AgentGraphSpec

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `entry_agent` | string | 是 | `pattern="^[a-zA-Z0-9_-]{1,64}$"` |
| `agents` | object | 是 | `additionalProperties={"$ref": "#/components/schemas/AgentTemplate"}`; `maxProperties=100`; `minProperties=1` |
| `limits` | [ExecutionLimits](schemas.md#schema-ExecutionLimits) | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "agents": {
      "additionalProperties": {
        "$ref": "#/components/schemas/AgentTemplate"
      },
      "maxProperties": 100,
      "minProperties": 1,
      "title": "Agents",
      "type": "object"
    },
    "entry_agent": {
      "pattern": "^[a-zA-Z0-9_-]{1,64}$",
      "title": "Entry Agent",
      "type": "string"
    },
    "limits": {
      "$ref": "#/components/schemas/ExecutionLimits"
    }
  },
  "required": [
    "entry_agent",
    "agents"
  ],
  "title": "AgentGraphSpec",
  "type": "object"
}
```

<a id="schema-AgentTemplate"></a>

## AgentTemplate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `maxLength=120`; `minLength=1` |
| `instructions` | string | 否 | `default=""`; `maxLength=100000` |
| `model_slot` | string | 否 | `default="default"`; `maxLength=64`; `minLength=1` |
| `tools` | array&lt;string&gt; | 否 | `maxItems=100` |
| `mcp` | array&lt;string&gt; | 否 | `maxItems=100` |
| `skills` | array&lt;string&gt; | 否 | `maxItems=100` |
| `handoffs` | array&lt;string&gt; | 否 | `maxItems=100` |
| `agent_tools` | array&lt;string&gt; | 否 | `maxItems=100` |
| `output_schema` | object anyOf null | 否 | `anyOf=[{"additionalProperties": true, "type": "object"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "agent_tools": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Agent Tools",
      "type": "array"
    },
    "handoffs": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Handoffs",
      "type": "array"
    },
    "instructions": {
      "default": "",
      "maxLength": 100000,
      "title": "Instructions",
      "type": "string"
    },
    "mcp": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Mcp",
      "type": "array"
    },
    "model_slot": {
      "default": "default",
      "maxLength": 64,
      "minLength": 1,
      "title": "Model Slot",
      "type": "string"
    },
    "name": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "output_schema": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Output Schema"
    },
    "skills": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Skills",
      "type": "array"
    },
    "tools": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Tools",
      "type": "array"
    }
  },
  "required": [
    "name"
  ],
  "title": "AgentTemplate",
  "type": "object"
}
```

<a id="schema-ApprovalDecisionRequest"></a>

## ApprovalDecisionRequest

One decision; the idempotency key makes its retry replayable.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `reason` | string anyOf null | 否 | `anyOf=[{"maxLength": 2000, "type": "string"}, {"type": "null"}]` |
| `idempotency_key` | string | 是 | `maxLength=200`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "description": "One decision; the idempotency key makes its retry replayable.",
  "properties": {
    "idempotency_key": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Idempotency Key",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    }
  },
  "required": [
    "idempotency_key"
  ],
  "title": "ApprovalDecisionRequest",
  "type": "object"
}
```

<a id="schema-ApprovalPage"></a>

## ApprovalPage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `approvals` | array&lt;[ApprovalRequestView](schemas.md#schema-ApprovalRequestView)&gt; | 是 | — |
| `total` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "approvals": {
      "items": {
        "$ref": "#/components/schemas/ApprovalRequestView"
      },
      "title": "Approvals",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "approvals",
    "total"
  ],
  "title": "ApprovalPage",
  "type": "object"
}
```

<a id="schema-ApprovalRequestView"></a>

## ApprovalRequestView

One approval request; the presented status derives expiry.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `approval_id` | string (uuid) | 是 | `format="uuid"` |
| `kind` | string | 是 | — |
| `status` | string | 是 | — |
| `title` | string | 是 | — |
| `summary` | string | 是 | — |
| `details` | object | 是 | `additionalProperties=true` |
| `target_type` | string | 是 | — |
| `target_id` | string | 是 | — |
| `target_version` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `approver_role` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `request_key` | string | 是 | — |
| `approver_user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `requester_user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `expires_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `decided_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `decided_by_user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `decision_reason` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "One approval request; the presented status derives expiry.",
  "properties": {
    "approval_id": {
      "format": "uuid",
      "title": "Approval Id",
      "type": "string"
    },
    "approver_role": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approver Role"
    },
    "approver_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approver User Id"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "decided_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Decided At"
    },
    "decided_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Decided By User Id"
    },
    "decision_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Decision Reason"
    },
    "details": {
      "additionalProperties": true,
      "title": "Details",
      "type": "object"
    },
    "expires_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Expires At"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "request_key": {
      "title": "Request Key",
      "type": "string"
    },
    "requester_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Requester User Id"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    },
    "target_id": {
      "title": "Target Id",
      "type": "string"
    },
    "target_type": {
      "title": "Target Type",
      "type": "string"
    },
    "target_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Target Version"
    },
    "title": {
      "title": "Title",
      "type": "string"
    }
  },
  "required": [
    "approval_id",
    "kind",
    "status",
    "title",
    "summary",
    "details",
    "target_type",
    "target_id",
    "target_version",
    "approver_role",
    "request_key",
    "approver_user_id",
    "requester_user_id",
    "expires_at",
    "created_at",
    "decided_at",
    "decided_by_user_id",
    "decision_reason"
  ],
  "title": "ApprovalRequestView",
  "type": "object"
}
```

<a id="schema-ArtifactView"></a>

## ArtifactView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `artifact_id` | string (uuid) | 是 | `format="uuid"` |
| `source_kind` | string | 是 | — |
| `source_id` | string (uuid) | 是 | `format="uuid"` |
| `owner_user_id` | string (uuid) | 是 | `format="uuid"` |
| `name` | string | 是 | — |
| `digest` | string | 是 | — |
| `size` | integer | 是 | — |
| `media_type` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `labels` | object | 是 | `additionalProperties=true` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "artifact_id": {
      "format": "uuid",
      "title": "Artifact Id",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "digest": {
      "title": "Digest",
      "type": "string"
    },
    "labels": {
      "additionalProperties": true,
      "title": "Labels",
      "type": "object"
    },
    "media_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Media Type"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "owner_user_id": {
      "format": "uuid",
      "title": "Owner User Id",
      "type": "string"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "source_id": {
      "format": "uuid",
      "title": "Source Id",
      "type": "string"
    },
    "source_kind": {
      "title": "Source Kind",
      "type": "string"
    }
  },
  "required": [
    "artifact_id",
    "source_kind",
    "source_id",
    "owner_user_id",
    "name",
    "digest",
    "size",
    "media_type",
    "labels",
    "created_at"
  ],
  "title": "ArtifactView",
  "type": "object"
}
```

<a id="schema-AssetRef"></a>

## AssetRef

One content-addressed input asset of a run.

The destination is a safe relative path inside the execution
environment: the shared path validator rejects absolute paths, parent
segments and backslashes, and the transfer resolves containment
against the environment root before writing.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `ref` | string | 是 | `maxLength=240`; `minLength=1` |
| `digest` | string | 是 | `pattern="^[a-f0-9]{64}$"` |
| `destination` | string | 是 | `maxLength=240`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "description": "One content-addressed input asset of a run.\n\nThe destination is a safe relative path inside the execution\nenvironment: the shared path validator rejects absolute paths, parent\nsegments and backslashes, and the transfer resolves containment\nagainst the environment root before writing.",
  "properties": {
    "destination": {
      "maxLength": 240,
      "minLength": 1,
      "title": "Destination",
      "type": "string"
    },
    "digest": {
      "pattern": "^[a-f0-9]{64}$",
      "title": "Digest",
      "type": "string"
    },
    "ref": {
      "maxLength": 240,
      "minLength": 1,
      "title": "Ref",
      "type": "string"
    }
  },
  "required": [
    "ref",
    "digest",
    "destination"
  ],
  "title": "AssetRef",
  "type": "object"
}
```

<a id="schema-AuditAppendView"></a>

## AuditAppendView

The receipt of one extension append; a replayed key reports no new fact.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `event_id` | string (uuid) | 是 | `format="uuid"` |
| `replayed` | boolean | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "The receipt of one extension append; a replayed key reports no new fact.",
  "properties": {
    "event_id": {
      "format": "uuid",
      "title": "Event Id",
      "type": "string"
    },
    "replayed": {
      "title": "Replayed",
      "type": "boolean"
    }
  },
  "required": [
    "event_id",
    "replayed"
  ],
  "title": "AuditAppendView",
  "type": "object"
}
```

<a id="schema-AuditEventPage"></a>

## AuditEventPage

One cursor page of audit events ordered by occurrence sequence.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `events` | array&lt;[AuditEventView](schemas.md#schema-AuditEventView)&gt; | 是 | — |
| `next_after_sequence` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "One cursor page of audit events ordered by occurrence sequence.",
  "properties": {
    "events": {
      "items": {
        "$ref": "#/components/schemas/AuditEventView"
      },
      "title": "Events",
      "type": "array"
    },
    "next_after_sequence": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Next After Sequence"
    }
  },
  "required": [
    "events",
    "next_after_sequence"
  ],
  "title": "AuditEventPage",
  "type": "object"
}
```

<a id="schema-AuditEventView"></a>

## AuditEventView

One sanitized audit fact with its database-assigned occurrence sequence.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `event_id` | string (uuid) | 是 | `format="uuid"` |
| `sequence` | integer | 是 | — |
| `occurred_at` | string (date-time) | 是 | `format="date-time"` |
| `recorded_at` | string (date-time) | 是 | `format="date-time"` |
| `category` | string | 是 | — |
| `severity` | string | 是 | — |
| `schema_version` | integer | 是 | — |
| `operation_id` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `correlation_id` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `causation_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `trace_id` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `actor_type` | string | 是 | — |
| `actor_id` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `configuration_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `installation_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `instance_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `invocation_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `package_key` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `configuration_revision` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `action` | string | 是 | — |
| `target_type` | string | 是 | — |
| `target_id` | string | 是 | — |
| `summary` | string | 是 | — |
| `details` | object | 是 | `additionalProperties=true` |
| `evidence_refs` | array&lt;string&gt; | 是 | — |
| `source` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "One sanitized audit fact with its database-assigned occurrence sequence.",
  "properties": {
    "action": {
      "title": "Action",
      "type": "string"
    },
    "actor_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Actor Id"
    },
    "actor_type": {
      "title": "Actor Type",
      "type": "string"
    },
    "category": {
      "title": "Category",
      "type": "string"
    },
    "causation_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Causation Id"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "configuration_revision": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Revision"
    },
    "correlation_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Correlation Id"
    },
    "details": {
      "additionalProperties": true,
      "title": "Details",
      "type": "object"
    },
    "event_id": {
      "format": "uuid",
      "title": "Event Id",
      "type": "string"
    },
    "evidence_refs": {
      "items": {
        "type": "string"
      },
      "title": "Evidence Refs",
      "type": "array"
    },
    "installation_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Installation Id"
    },
    "instance_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Instance Id"
    },
    "invocation_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Invocation Id"
    },
    "occurred_at": {
      "format": "date-time",
      "title": "Occurred At",
      "type": "string"
    },
    "operation_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Operation Id"
    },
    "package_key": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Package Key"
    },
    "recorded_at": {
      "format": "date-time",
      "title": "Recorded At",
      "type": "string"
    },
    "schema_version": {
      "title": "Schema Version",
      "type": "integer"
    },
    "sequence": {
      "title": "Sequence",
      "type": "integer"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    },
    "target_id": {
      "title": "Target Id",
      "type": "string"
    },
    "target_type": {
      "title": "Target Type",
      "type": "string"
    },
    "trace_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Trace Id"
    }
  },
  "required": [
    "event_id",
    "sequence",
    "occurred_at",
    "recorded_at",
    "category",
    "severity",
    "schema_version",
    "operation_id",
    "correlation_id",
    "causation_id",
    "trace_id",
    "actor_type",
    "actor_id",
    "configuration_id",
    "installation_id",
    "instance_id",
    "invocation_id",
    "package_key",
    "configuration_revision",
    "action",
    "target_type",
    "target_id",
    "summary",
    "details",
    "evidence_refs",
    "source"
  ],
  "title": "AuditEventView",
  "type": "object"
}
```

<a id="schema-AuthenticatedUser"></a>

## AuthenticatedUser

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `user_id` | string (uuid) | 是 | `format="uuid"` |
| `email` | string | 是 | — |
| `display_name` | string | 是 | — |
| `status` | string | 是 | — |
| `platform_roles` | array&lt;string&gt; | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "display_name": {
      "title": "Display Name",
      "type": "string"
    },
    "email": {
      "title": "Email",
      "type": "string"
    },
    "platform_roles": {
      "items": {
        "type": "string"
      },
      "title": "Platform Roles",
      "type": "array"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "email",
    "display_name",
    "status",
    "platform_roles"
  ],
  "title": "AuthenticatedUser",
  "type": "object"
}
```

<a id="schema-AvailableModelConnectionView"></a>

## AvailableModelConnectionView

The user's selectable model subset; no upstream address or secret.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | 是 | `format="uuid"` |
| `revision` | integer | 是 | — |
| `name` | string | 是 | — |
| `provider` | string | 是 | — |
| `models` | array&lt;string&gt; | 是 | — |
| `default_model` | string | 是 | — |
| `is_default` | boolean | 是 | — |

```json
{
  "description": "The user's selectable model subset; no upstream address or secret.",
  "properties": {
    "connection_id": {
      "format": "uuid",
      "title": "Connection Id",
      "type": "string"
    },
    "default_model": {
      "title": "Default Model",
      "type": "string"
    },
    "is_default": {
      "title": "Is Default",
      "type": "boolean"
    },
    "models": {
      "items": {
        "type": "string"
      },
      "title": "Models",
      "type": "array"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "provider": {
      "title": "Provider",
      "type": "string"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    }
  },
  "required": [
    "connection_id",
    "revision",
    "name",
    "provider",
    "models",
    "default_model",
    "is_default"
  ],
  "title": "AvailableModelConnectionView",
  "type": "object"
}
```

<a id="schema-BindingConfiguration"></a>

## BindingConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `parameters` | object | 否 | `additionalProperties=true` |
| `instance_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `export_id` | string anyOf null | 否 | `anyOf=[{"maxLength": 100, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `mcp_connection_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "export_id": {
      "anyOf": [
        {
          "maxLength": 100,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Export Id"
    },
    "instance_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Instance Id"
    },
    "mcp_connection_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Mcp Connection Id"
    },
    "parameters": {
      "additionalProperties": true,
      "title": "Parameters",
      "type": "object"
    }
  },
  "title": "BindingConfiguration",
  "type": "object"
}
```

<a id="schema-BindingConnect"></a>

## BindingConnect

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=1.0` |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `environment_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `transport` | string | 否 | `default="streamable_http"`; `enum=["streamable_http", "sse", "stdio"]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "environment_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment Id"
    },
    "expected_revision": {
      "minimum": 1.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "transport": {
      "default": "streamable_http",
      "enum": [
        "streamable_http",
        "sse",
        "stdio"
      ],
      "title": "Transport",
      "type": "string"
    }
  },
  "required": [
    "expected_revision"
  ],
  "title": "BindingConnect",
  "type": "object"
}
```

<a id="schema-BindingCreate"></a>

## BindingCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `pattern="^[a-zA-Z][a-zA-Z0-9_-]{0,63}$"` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration` | [BindingConfiguration](schemas.md#schema-BindingConfiguration) | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/BindingConfiguration"
    },
    "name": {
      "pattern": "^[a-zA-Z][a-zA-Z0-9_-]{0,63}$",
      "title": "Name",
      "type": "string"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "name",
    "version_id"
  ],
  "title": "BindingCreate",
  "type": "object"
}
```

<a id="schema-BindingUpdate"></a>

## BindingUpdate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=1.0` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 否 | `default="enabled"`; `enum=["enabled", "disabled"]` |
| `configuration` | [BindingConfiguration](schemas.md#schema-BindingConfiguration) | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/BindingConfiguration"
    },
    "expected_revision": {
      "minimum": 1.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "status": {
      "default": "enabled",
      "enum": [
        "enabled",
        "disabled"
      ],
      "title": "Status",
      "type": "string"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "expected_revision",
    "version_id"
  ],
  "title": "BindingUpdate",
  "type": "object"
}
```

<a id="schema-BindingView"></a>

## BindingView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `owner_user_id` | string (uuid) | 是 | `format="uuid"` |
| `name` | string | 是 | — |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `version_digest` | string | 是 | — |
| `revision` | integer | 是 | — |
| `status` | string | 是 | — |
| `configuration` | [BindingConfiguration](schemas.md#schema-BindingConfiguration) | 是 | — |

```json
{
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/BindingConfiguration"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "owner_user_id": {
      "format": "uuid",
      "title": "Owner User Id",
      "type": "string"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "version_digest": {
      "title": "Version Digest",
      "type": "string"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "owner_user_id",
    "name",
    "version_id",
    "version_digest",
    "revision",
    "status",
    "configuration"
  ],
  "title": "BindingView",
  "type": "object"
}
```

<a id="schema-CancelRequest"></a>

## CancelRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `expected_revision` | integer | 是 | `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    }
  },
  "required": [
    "command_id",
    "expected_revision"
  ],
  "title": "CancelRequest",
  "type": "object"
}
```

<a id="schema-CapabilityDefinitionPage"></a>

## CapabilityDefinitionPage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `items` | array&lt;[CapabilityDefinitionView](schemas.md#schema-CapabilityDefinitionView)&gt; | 是 | — |
| `total` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/CapabilityDefinitionView"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "CapabilityDefinitionPage",
  "type": "object"
}
```

<a id="schema-CapabilityDefinitionView"></a>

## CapabilityDefinitionView

One definition as the catalog exposes it; content lives in versions.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `definition_id` | string (uuid) | 是 | `format="uuid"` |
| `type` | string | 是 | — |
| `key` | string | 是 | — |
| `name` | string | 是 | — |
| `description` | string | 是 | — |
| `visibility` | string | 是 | — |
| `status` | string | 是 | — |
| `source` | string | 是 | — |
| `created_by_user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `latest_version` | integer | 是 | — |
| `has_published_version` | boolean | 是 | — |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "description": "One definition as the catalog exposes it; content lives in versions.",
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Created By User Id"
    },
    "definition_id": {
      "format": "uuid",
      "title": "Definition Id",
      "type": "string"
    },
    "description": {
      "title": "Description",
      "type": "string"
    },
    "has_published_version": {
      "title": "Has Published Version",
      "type": "boolean"
    },
    "key": {
      "title": "Key",
      "type": "string"
    },
    "latest_version": {
      "title": "Latest Version",
      "type": "integer"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "type": {
      "title": "Type",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "visibility": {
      "title": "Visibility",
      "type": "string"
    }
  },
  "required": [
    "definition_id",
    "type",
    "key",
    "name",
    "description",
    "visibility",
    "status",
    "source",
    "created_by_user_id",
    "latest_version",
    "has_published_version",
    "created_at",
    "updated_at"
  ],
  "title": "CapabilityDefinitionView",
  "type": "object"
}
```

<a id="schema-CapabilitySelection"></a>

## CapabilitySelection

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `binding_id` | string (uuid) | 是 | `format="uuid"` |
| `parameters` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "binding_id": {
      "format": "uuid",
      "title": "Binding Id",
      "type": "string"
    },
    "parameters": {
      "additionalProperties": true,
      "title": "Parameters",
      "type": "object"
    }
  },
  "required": [
    "binding_id"
  ],
  "title": "CapabilitySelection",
  "type": "object"
}
```

<a id="schema-CapabilityVersionView"></a>

## CapabilityVersionView

One immutable-or-draft version with its full declared content.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `definition_id` | string (uuid) | 是 | `format="uuid"` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `version` | integer | 是 | — |
| `status` | string | 是 | — |
| `configuration` | object | 是 | `additionalProperties=true` |
| `resource_manifest` | array&lt;object&gt; | 是 | — |
| `dependencies` | array&lt;object&gt; | 是 | — |
| `flow_templates` | array&lt;object&gt; | 是 | — |
| `digest` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `package_digest` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `package_size` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |
| `package_filename` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `published_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `published_by_user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "description": "One immutable-or-draft version with its full declared content.",
  "properties": {
    "configuration": {
      "additionalProperties": true,
      "title": "Configuration",
      "type": "object"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "definition_id": {
      "format": "uuid",
      "title": "Definition Id",
      "type": "string"
    },
    "dependencies": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Dependencies",
      "type": "array"
    },
    "digest": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Digest"
    },
    "flow_templates": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Flow Templates",
      "type": "array"
    },
    "package_digest": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Package Digest"
    },
    "package_filename": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Package Filename"
    },
    "package_size": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Package Size"
    },
    "published_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Published At"
    },
    "published_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Published By User Id"
    },
    "resource_manifest": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Resource Manifest",
      "type": "array"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "definition_id",
    "version_id",
    "version",
    "status",
    "configuration",
    "resource_manifest",
    "dependencies",
    "flow_templates",
    "digest",
    "package_digest",
    "package_size",
    "package_filename",
    "published_at",
    "published_by_user_id",
    "created_at"
  ],
  "title": "CapabilityVersionView",
  "type": "object"
}
```

<a id="schema-CapacityKindView"></a>

## CapacityKindView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `kind` | string | 是 | — |
| `limit_value` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |
| `reserved_value` | integer | 是 | — |

```json
{
  "properties": {
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "limit_value": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Limit Value"
    },
    "reserved_value": {
      "title": "Reserved Value",
      "type": "integer"
    }
  },
  "required": [
    "kind",
    "limit_value",
    "reserved_value"
  ],
  "title": "CapacityKindView",
  "type": "object"
}
```

<a id="schema-CapacityLimitUpdateRequest"></a>

## CapacityLimitUpdateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `limit_value` | integer anyOf null | 否 | `anyOf=[{"minimum": 0.0, "type": "integer"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "limit_value": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Limit Value"
    }
  },
  "title": "CapacityLimitUpdateRequest",
  "type": "object"
}
```

<a id="schema-CapacitySnapshotView"></a>

## CapacitySnapshotView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `user_id` | string | 是 | — |
| `kinds` | array&lt;[CapacityKindView](schemas.md#schema-CapacityKindView)&gt; | 是 | — |

```json
{
  "properties": {
    "kinds": {
      "items": {
        "$ref": "#/components/schemas/CapacityKindView"
      },
      "title": "Kinds",
      "type": "array"
    },
    "user_id": {
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "kinds"
  ],
  "title": "CapacitySnapshotView",
  "type": "object"
}
```

<a id="schema-ChangePasswordRequest"></a>

## ChangePasswordRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `current_password` | string | 是 | `maxLength=256`; `minLength=1` |
| `new_password` | string | 是 | `maxLength=256`; `minLength=8` |

```json
{
  "additionalProperties": false,
  "properties": {
    "current_password": {
      "maxLength": 256,
      "minLength": 1,
      "title": "Current Password",
      "type": "string"
    },
    "new_password": {
      "maxLength": 256,
      "minLength": 8,
      "title": "New Password",
      "type": "string"
    }
  },
  "required": [
    "current_password",
    "new_password"
  ],
  "title": "ChangePasswordRequest",
  "type": "object"
}
```

<a id="schema-ChannelAccountCreate"></a>

## ChannelAccountCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `channel_type` | string | 是 | `maxLength=50`; `minLength=1` |
| `external_account_id` | string | 是 | `maxLength=200`; `minLength=1` |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "channel_type": {
      "maxLength": 50,
      "minLength": 1,
      "title": "Channel Type",
      "type": "string"
    },
    "configuration": {
      "additionalProperties": true,
      "title": "Configuration",
      "type": "object"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "external_account_id": {
      "maxLength": 200,
      "minLength": 1,
      "title": "External Account Id",
      "type": "string"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "channel_type",
    "external_account_id",
    "installation_id",
    "version_id"
  ],
  "title": "ChannelAccountCreate",
  "type": "object"
}
```

<a id="schema-ChannelAccountView"></a>

## ChannelAccountView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `owner_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `channel_type` | string | 是 | — |
| `external_account_id` | string | 是 | — |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |
| `epoch` | integer | 是 | — |
| `configuration` | object | 是 | `additionalProperties=true` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "channel_type": {
      "title": "Channel Type",
      "type": "string"
    },
    "configuration": {
      "additionalProperties": true,
      "title": "Configuration",
      "type": "object"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "epoch": {
      "title": "Epoch",
      "type": "integer"
    },
    "external_account_id": {
      "title": "External Account Id",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "owner_id": {
      "format": "uuid",
      "title": "Owner Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "owner_id",
    "configuration_id",
    "channel_type",
    "external_account_id",
    "installation_id",
    "version_id",
    "status",
    "epoch",
    "configuration",
    "created_at",
    "updated_at"
  ],
  "title": "ChannelAccountView",
  "type": "object"
}
```

<a id="schema-ChannelAttachment"></a>

## ChannelAttachment

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `file_id` | string (uuid) | 是 | `format="uuid"` |
| `version` | integer | 是 | `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "file_id": {
      "format": "uuid",
      "title": "File Id",
      "type": "string"
    },
    "version": {
      "minimum": 1.0,
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "file_id",
    "version"
  ],
  "title": "ChannelAttachment",
  "type": "object"
}
```

<a id="schema-ChannelConfiguration"></a>

## ChannelConfiguration

The vendor protocol one channel definition speaks.

A channel declares one vendor protocol and the adapter operations
that speak it. The declaration is static: an entrypoint is an
identifier the package must carry as a declared resource, and no
vendor client is constructed, no signature is verified and no
message is exchanged at publish time.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `protocol` | string | 是 | `maxLength=50`; `minLength=2` |
| `signature_algorithm` | string | 否 | `const="hmac-sha256"`; `default="hmac-sha256"` |
| `operations` | array&lt;[ChannelOperation](schemas.md#schema-ChannelOperation)&gt; | 是 | `maxItems=4`; `minItems=1` |
| `parameters_schema` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "description": "The vendor protocol one channel definition speaks.\n\nA channel declares one vendor protocol and the adapter operations\nthat speak it. The declaration is static: an entrypoint is an\nidentifier the package must carry as a declared resource, and no\nvendor client is constructed, no signature is verified and no\nmessage is exchanged at publish time.",
  "properties": {
    "operations": {
      "items": {
        "$ref": "#/components/schemas/ChannelOperation"
      },
      "maxItems": 4,
      "minItems": 1,
      "title": "Operations",
      "type": "array"
    },
    "parameters_schema": {
      "additionalProperties": true,
      "title": "Parameters Schema",
      "type": "object"
    },
    "protocol": {
      "maxLength": 50,
      "minLength": 2,
      "title": "Protocol",
      "type": "string"
    },
    "signature_algorithm": {
      "const": "hmac-sha256",
      "default": "hmac-sha256",
      "title": "Signature Algorithm",
      "type": "string"
    }
  },
  "required": [
    "protocol",
    "operations"
  ],
  "title": "ChannelConfiguration",
  "type": "object"
}
```

<a id="schema-ChannelConnectionAccess"></a>

## ChannelConnectionAccess

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `account_id` | string (uuid) | 是 | `format="uuid"` |
| `epoch` | integer | 是 | — |
| `configuration` | object | 是 | `additionalProperties=true` |

```json
{
  "properties": {
    "account_id": {
      "format": "uuid",
      "title": "Account Id",
      "type": "string"
    },
    "configuration": {
      "additionalProperties": true,
      "title": "Configuration",
      "type": "object"
    },
    "epoch": {
      "title": "Epoch",
      "type": "integer"
    }
  },
  "required": [
    "account_id",
    "epoch",
    "configuration"
  ],
  "title": "ChannelConnectionAccess",
  "type": "object"
}
```

<a id="schema-ChannelConnectionChange"></a>

## ChannelConnectionChange

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_epoch` | integer | 是 | `minimum=1.0` |
| `enabled` | boolean | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "expected_epoch": {
      "minimum": 1.0,
      "title": "Expected Epoch",
      "type": "integer"
    }
  },
  "required": [
    "expected_epoch",
    "enabled"
  ],
  "title": "ChannelConnectionChange",
  "type": "object"
}
```

<a id="schema-ChannelDeclaration"></a>

## ChannelDeclaration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `type` | string | 是 | `const="channel"` |
| `configuration` | [ChannelConfiguration](schemas.md#schema-ChannelConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ChannelConfiguration"
    },
    "type": {
      "const": "channel",
      "title": "Type",
      "type": "string"
    }
  },
  "required": [
    "type",
    "configuration"
  ],
  "title": "ChannelDeclaration",
  "type": "object"
}
```

<a id="schema-ChannelDeliveryClaim"></a>

## ChannelDeliveryClaim

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `account_id` | string (uuid) | 是 | `format="uuid"` |
| `connection_epoch` | integer | 是 | — |
| `idempotency_key` | string | 是 | — |
| `conversation_key` | string | 是 | — |
| `payload_digest` | string | 是 | — |
| `status` | string | 是 | — |
| `attempts` | integer | 是 | — |
| `external_message_id` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `failure_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |
| `payload` | object | 是 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "account_id": {
      "format": "uuid",
      "title": "Account Id",
      "type": "string"
    },
    "attempts": {
      "title": "Attempts",
      "type": "integer"
    },
    "connection_epoch": {
      "title": "Connection Epoch",
      "type": "integer"
    },
    "conversation_key": {
      "title": "Conversation Key",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "external_message_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Message Id"
    },
    "failure_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Code"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "idempotency_key": {
      "title": "Idempotency Key",
      "type": "string"
    },
    "payload": {
      "additionalProperties": true,
      "title": "Payload",
      "type": "object"
    },
    "payload_digest": {
      "title": "Payload Digest",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "account_id",
    "connection_epoch",
    "idempotency_key",
    "conversation_key",
    "payload_digest",
    "status",
    "attempts",
    "external_message_id",
    "failure_code",
    "created_at",
    "updated_at",
    "payload"
  ],
  "title": "ChannelDeliveryClaim",
  "type": "object"
}
```

<a id="schema-ChannelDeliveryCreate"></a>

## ChannelDeliveryCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `idempotency_key` | string | 是 | `maxLength=240`; `minLength=1` |
| `conversation_key` | string | 是 | `maxLength=400`; `minLength=1` |
| `payload` | object | 是 | `additionalProperties=true` |
| `attachments` | array&lt;[ChannelAttachment](schemas.md#schema-ChannelAttachment)&gt; | 否 | `maxItems=10` |

```json
{
  "additionalProperties": false,
  "properties": {
    "attachments": {
      "items": {
        "$ref": "#/components/schemas/ChannelAttachment"
      },
      "maxItems": 10,
      "title": "Attachments",
      "type": "array"
    },
    "conversation_key": {
      "maxLength": 400,
      "minLength": 1,
      "title": "Conversation Key",
      "type": "string"
    },
    "idempotency_key": {
      "maxLength": 240,
      "minLength": 1,
      "title": "Idempotency Key",
      "type": "string"
    },
    "payload": {
      "additionalProperties": true,
      "title": "Payload",
      "type": "object"
    }
  },
  "required": [
    "idempotency_key",
    "conversation_key",
    "payload"
  ],
  "title": "ChannelDeliveryCreate",
  "type": "object"
}
```

<a id="schema-ChannelDeliveryResult"></a>

## ChannelDeliveryResult

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `status` | string | 是 | `pattern="^(sent&#124;failed&#124;needs_attention)$"` |
| `external_message_id` | string anyOf null | 否 | `anyOf=[{"maxLength": 240, "type": "string"}, {"type": "null"}]` |
| `failure_code` | string anyOf null | 否 | `anyOf=[{"maxLength": 100, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "external_message_id": {
      "anyOf": [
        {
          "maxLength": 240,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Message Id"
    },
    "failure_code": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Code"
    },
    "status": {
      "pattern": "^(sent|failed|needs_attention)$",
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status"
  ],
  "title": "ChannelDeliveryResult",
  "type": "object"
}
```

<a id="schema-ChannelDeliveryView"></a>

## ChannelDeliveryView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `account_id` | string (uuid) | 是 | `format="uuid"` |
| `connection_epoch` | integer | 是 | — |
| `idempotency_key` | string | 是 | — |
| `conversation_key` | string | 是 | — |
| `payload_digest` | string | 是 | — |
| `status` | string | 是 | — |
| `attempts` | integer | 是 | — |
| `external_message_id` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `failure_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "account_id": {
      "format": "uuid",
      "title": "Account Id",
      "type": "string"
    },
    "attempts": {
      "title": "Attempts",
      "type": "integer"
    },
    "connection_epoch": {
      "title": "Connection Epoch",
      "type": "integer"
    },
    "conversation_key": {
      "title": "Conversation Key",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "external_message_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Message Id"
    },
    "failure_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Code"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "idempotency_key": {
      "title": "Idempotency Key",
      "type": "string"
    },
    "payload_digest": {
      "title": "Payload Digest",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "account_id",
    "connection_epoch",
    "idempotency_key",
    "conversation_key",
    "payload_digest",
    "status",
    "attempts",
    "external_message_id",
    "failure_code",
    "created_at",
    "updated_at"
  ],
  "title": "ChannelDeliveryView",
  "type": "object"
}
```

<a id="schema-ChannelIdentityMappingCreate"></a>

## ChannelIdentityMappingCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `user_id` | string (uuid) | 是 | `format="uuid"` |
| `external_identity` | string | 是 | `maxLength=200`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "external_identity": {
      "maxLength": 200,
      "minLength": 1,
      "title": "External Identity",
      "type": "string"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "external_identity"
  ],
  "title": "ChannelIdentityMappingCreate",
  "type": "object"
}
```

<a id="schema-ChannelIdentityMappingView"></a>

## ChannelIdentityMappingView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `user_id` | string (uuid) | 是 | `format="uuid"` |
| `external_identity` | string | 是 | `maxLength=200`; `minLength=1` |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `account_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "account_id": {
      "format": "uuid",
      "title": "Account Id",
      "type": "string"
    },
    "external_identity": {
      "maxLength": 200,
      "minLength": 1,
      "title": "External Identity",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "external_identity",
    "id",
    "account_id",
    "status"
  ],
  "title": "ChannelIdentityMappingView",
  "type": "object"
}
```

<a id="schema-ChannelInboxView"></a>

## ChannelInboxView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `account_id` | string (uuid) | 是 | `format="uuid"` |
| `connection_epoch` | integer | 是 | — |
| `external_message_id` | string | 是 | — |
| `conversation_key` | string | 是 | — |
| `payload_digest` | string | 是 | — |
| `status` | string | 是 | — |
| `flow_run_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `failure_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `received_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "account_id": {
      "format": "uuid",
      "title": "Account Id",
      "type": "string"
    },
    "connection_epoch": {
      "title": "Connection Epoch",
      "type": "integer"
    },
    "conversation_key": {
      "title": "Conversation Key",
      "type": "string"
    },
    "external_message_id": {
      "title": "External Message Id",
      "type": "string"
    },
    "failure_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Code"
    },
    "flow_run_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Flow Run Id"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "payload_digest": {
      "title": "Payload Digest",
      "type": "string"
    },
    "received_at": {
      "format": "date-time",
      "title": "Received At",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "account_id",
    "connection_epoch",
    "external_message_id",
    "conversation_key",
    "payload_digest",
    "status",
    "flow_run_id",
    "failure_code",
    "received_at"
  ],
  "title": "ChannelInboxView",
  "type": "object"
}
```

<a id="schema-ChannelMappingStatus"></a>

## ChannelMappingStatus

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `status` | string | 是 | `enum=["active", "revoked"]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "status": {
      "enum": [
        "active",
        "revoked"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status"
  ],
  "title": "ChannelMappingStatus",
  "type": "object"
}
```

<a id="schema-ChannelOperation"></a>

## ChannelOperation

One adapter operation with its identifier and typed I/O schemas.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `enum=["receive", "send", "connect", "disconnect"]` |
| `entrypoint` | string | 是 | `maxLength=300`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `input_schema` | object | 否 | `additionalProperties=true` |
| `output_schema` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "description": "One adapter operation with its identifier and typed I/O schemas.",
  "properties": {
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
    },
    "entrypoint": {
      "maxLength": 300,
      "minLength": 1,
      "title": "Entrypoint",
      "type": "string"
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "name": {
      "enum": [
        "receive",
        "send",
        "connect",
        "disconnect"
      ],
      "title": "Name",
      "type": "string"
    },
    "output_schema": {
      "additionalProperties": true,
      "title": "Output Schema",
      "type": "object"
    }
  },
  "required": [
    "name",
    "entrypoint"
  ],
  "title": "ChannelOperation",
  "type": "object"
}
```

<a id="schema-ChannelReceiveRequest"></a>

## ChannelReceiveRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `epoch` | integer | 是 | `minimum=1.0` |
| `external_message_id` | string | 是 | `maxLength=240`; `minLength=1` |
| `conversation_key` | string | 是 | `maxLength=400`; `minLength=1` |
| `external_identity` | string | 是 | `maxLength=200`; `minLength=1` |
| `payload` | object | 是 | `additionalProperties=true` |
| `attachments` | array&lt;[ChannelAttachment](schemas.md#schema-ChannelAttachment)&gt; | 否 | `maxItems=10` |

```json
{
  "additionalProperties": false,
  "properties": {
    "attachments": {
      "items": {
        "$ref": "#/components/schemas/ChannelAttachment"
      },
      "maxItems": 10,
      "title": "Attachments",
      "type": "array"
    },
    "conversation_key": {
      "maxLength": 400,
      "minLength": 1,
      "title": "Conversation Key",
      "type": "string"
    },
    "epoch": {
      "minimum": 1.0,
      "title": "Epoch",
      "type": "integer"
    },
    "external_identity": {
      "maxLength": 200,
      "minLength": 1,
      "title": "External Identity",
      "type": "string"
    },
    "external_message_id": {
      "maxLength": 240,
      "minLength": 1,
      "title": "External Message Id",
      "type": "string"
    },
    "payload": {
      "additionalProperties": true,
      "title": "Payload",
      "type": "object"
    }
  },
  "required": [
    "epoch",
    "external_message_id",
    "conversation_key",
    "external_identity",
    "payload"
  ],
  "title": "ChannelReceiveRequest",
  "type": "object"
}
```

<a id="schema-ChannelReceiveView"></a>

## ChannelReceiveView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `inbox_id` | string (uuid) | 是 | `format="uuid"` |
| `accepted` | boolean | 是 | — |
| `status` | string | 是 | — |

```json
{
  "properties": {
    "accepted": {
      "title": "Accepted",
      "type": "boolean"
    },
    "inbox_id": {
      "format": "uuid",
      "title": "Inbox Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "inbox_id",
    "accepted",
    "status"
  ],
  "title": "ChannelReceiveView",
  "type": "object"
}
```

<a id="schema-ChannelReconciliation"></a>

## ChannelReconciliation

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `status` | string | 是 | `enum=["needs_attention", "sent", "failed"]` |
| `evidence` | string | 是 | `maxLength=2000`; `minLength=1` |
| `external_message_id` | string anyOf null | 否 | `anyOf=[{"maxLength": 240, "minLength": 1, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "evidence": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Evidence",
      "type": "string"
    },
    "external_message_id": {
      "anyOf": [
        {
          "maxLength": 240,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Message Id"
    },
    "status": {
      "enum": [
        "needs_attention",
        "sent",
        "failed"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status",
    "evidence"
  ],
  "title": "ChannelReconciliation",
  "type": "object"
}
```

<a id="schema-ChannelRouteCreate"></a>

## ChannelRouteCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `conversation_key` | string | 是 | `maxLength=400`; `minLength=1` |
| `configuration_id` | string (uuid) | 是 | `format="uuid"` |
| `flow_revision_id` | string (uuid) | 是 | `format="uuid"` |
| `enabled` | boolean | 否 | `default=true` |
| `input_mapping` | object | 否 | `additionalProperties={"type": "string"}` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "format": "uuid",
      "title": "Configuration Id",
      "type": "string"
    },
    "conversation_key": {
      "maxLength": 400,
      "minLength": 1,
      "title": "Conversation Key",
      "type": "string"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "flow_revision_id": {
      "format": "uuid",
      "title": "Flow Revision Id",
      "type": "string"
    },
    "input_mapping": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Input Mapping",
      "type": "object"
    }
  },
  "required": [
    "conversation_key",
    "configuration_id",
    "flow_revision_id"
  ],
  "title": "ChannelRouteCreate",
  "type": "object"
}
```

<a id="schema-ChannelRouteView"></a>

## ChannelRouteView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `conversation_key` | string | 是 | `maxLength=400`; `minLength=1` |
| `configuration_id` | string (uuid) | 是 | `format="uuid"` |
| `flow_revision_id` | string (uuid) | 是 | `format="uuid"` |
| `enabled` | boolean | 否 | `default=true` |
| `input_mapping` | object | 否 | `additionalProperties={"type": "string"}` |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `account_id` | string (uuid) | 是 | `format="uuid"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "account_id": {
      "format": "uuid",
      "title": "Account Id",
      "type": "string"
    },
    "configuration_id": {
      "format": "uuid",
      "title": "Configuration Id",
      "type": "string"
    },
    "conversation_key": {
      "maxLength": 400,
      "minLength": 1,
      "title": "Conversation Key",
      "type": "string"
    },
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "flow_revision_id": {
      "format": "uuid",
      "title": "Flow Revision Id",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "input_mapping": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Input Mapping",
      "type": "object"
    }
  },
  "required": [
    "conversation_key",
    "configuration_id",
    "flow_revision_id",
    "id",
    "account_id"
  ],
  "title": "ChannelRouteView",
  "type": "object"
}
```

<a id="schema-CleanupPreview"></a>

## CleanupPreview

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `namespace_id` | string (uuid) | 是 | `format="uuid"` |
| `revision` | integer | 是 | — |
| `used_bytes` | integer | 是 | — |
| `entry_count` | integer | 是 | — |
| `entries` | array&lt;object&gt; | 是 | — |
| `preview_digest` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "entries": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Entries",
      "type": "array"
    },
    "entry_count": {
      "title": "Entry Count",
      "type": "integer"
    },
    "namespace_id": {
      "format": "uuid",
      "title": "Namespace Id",
      "type": "string"
    },
    "preview_digest": {
      "title": "Preview Digest",
      "type": "string"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "used_bytes": {
      "title": "Used Bytes",
      "type": "integer"
    }
  },
  "required": [
    "namespace_id",
    "revision",
    "used_bytes",
    "entry_count",
    "entries",
    "preview_digest"
  ],
  "title": "CleanupPreview",
  "type": "object"
}
```

<a id="schema-CleanupRequest"></a>

## CleanupRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `preview_digest` | string | 是 | `pattern="^[a-f0-9]{64}$"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "preview_digest": {
      "pattern": "^[a-f0-9]{64}$",
      "title": "Preview Digest",
      "type": "string"
    }
  },
  "required": [
    "expected_revision",
    "preview_digest"
  ],
  "title": "CleanupRequest",
  "type": "object"
}
```

<a id="schema-CleanupResult"></a>

## CleanupResult

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `cleaned` | boolean | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "cleaned": {
      "title": "Cleaned",
      "type": "boolean"
    }
  },
  "required": [
    "cleaned"
  ],
  "title": "CleanupResult",
  "type": "object"
}
```

<a id="schema-ConfigurationCloseRequest"></a>

## ConfigurationCloseRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    }
  },
  "required": [
    "expected_revision"
  ],
  "title": "ConfigurationCloseRequest",
  "type": "object"
}
```

<a id="schema-ConfigurationClosureView"></a>

## ConfigurationClosureView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |
| `detail` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "format": "uuid",
      "title": "Configuration Id",
      "type": "string"
    },
    "detail": {
      "additionalProperties": true,
      "title": "Detail",
      "type": "object"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "configuration_id",
    "status"
  ],
  "title": "ConfigurationClosureView",
  "type": "object"
}
```

<a id="schema-ConfigurationCreate"></a>

## ConfigurationCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `maxLength=120`; `minLength=1` |
| `definition_revision_id` | string (uuid) | 是 | `format="uuid"` |
| `root_directory_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `specification` | [ConfigurationSpec](schemas.md#schema-ConfigurationSpec) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/ConfigurationSpec"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "definition_revision_id": {
      "format": "uuid",
      "title": "Definition Revision Id",
      "type": "string"
    },
    "name": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "root_directory_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Root Directory Id"
    },
    "specification": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ConfigurationSpec"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "required": [
    "name",
    "definition_revision_id"
  ],
  "title": "ConfigurationCreate",
  "type": "object"
}
```

<a id="schema-ConfigurationHook"></a>

## ConfigurationHook

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `binding_id` | string (uuid) | 是 | `format="uuid"` |
| `stages` | array&lt;string&gt; | 是 | `maxItems=9`; `minItems=1` |
| `order` | integer | 否 | `default=0`; `minimum=0.0` |
| `budget` | [HookBudget](schemas.md#schema-HookBudget) | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "binding_id": {
      "format": "uuid",
      "title": "Binding Id",
      "type": "string"
    },
    "budget": {
      "$ref": "#/components/schemas/HookBudget"
    },
    "order": {
      "default": 0,
      "minimum": 0.0,
      "title": "Order",
      "type": "integer"
    },
    "stages": {
      "items": {
        "type": "string"
      },
      "maxItems": 9,
      "minItems": 1,
      "title": "Stages",
      "type": "array"
    }
  },
  "required": [
    "binding_id",
    "stages"
  ],
  "title": "ConfigurationHook",
  "type": "object"
}
```

<a id="schema-ConfigurationInvitationCreate"></a>

## ConfigurationInvitationCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `user_id` | string (uuid) | 是 | `format="uuid"` |
| `permissions` | array&lt;string&gt; | 是 | `maxItems=4`; `minItems=1` |
| `directory_access` | boolean | 否 | `default=false` |
| `expires_in_seconds` | integer | 否 | `default=604800`; `maximum=2592000.0`; `minimum=60.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "directory_access": {
      "default": false,
      "title": "Directory Access",
      "type": "boolean"
    },
    "expires_in_seconds": {
      "default": 604800,
      "maximum": 2592000.0,
      "minimum": 60.0,
      "title": "Expires In Seconds",
      "type": "integer"
    },
    "permissions": {
      "items": {
        "enum": [
          "read",
          "use",
          "edit",
          "manage"
        ],
        "type": "string"
      },
      "maxItems": 4,
      "minItems": 1,
      "title": "Permissions",
      "type": "array"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "permissions"
  ],
  "title": "ConfigurationInvitationCreate",
  "type": "object"
}
```

<a id="schema-ConfigurationInvitationView"></a>

## ConfigurationInvitationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "format": "uuid",
      "title": "Configuration Id",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "configuration_id",
    "status"
  ],
  "title": "ConfigurationInvitationView",
  "type": "object"
}
```

<a id="schema-ConfigurationMembershipView"></a>

## ConfigurationMembershipView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration_id` | string (uuid) | 是 | `format="uuid"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "format": "uuid",
      "title": "Configuration Id",
      "type": "string"
    }
  },
  "required": [
    "configuration_id"
  ],
  "title": "ConfigurationMembershipView",
  "type": "object"
}
```

<a id="schema-ConfigurationRevisionView"></a>

## ConfigurationRevisionView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_id` | string (uuid) | 是 | `format="uuid"` |
| `number` | integer | 是 | — |
| `specification` | [ConfigurationSpec](schemas.md#schema-ConfigurationSpec) | 是 | — |
| `digest` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "format": "uuid",
      "title": "Configuration Id",
      "type": "string"
    },
    "digest": {
      "title": "Digest",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "number": {
      "title": "Number",
      "type": "integer"
    },
    "specification": {
      "$ref": "#/components/schemas/ConfigurationSpec"
    }
  },
  "required": [
    "id",
    "configuration_id",
    "number",
    "specification",
    "digest"
  ],
  "title": "ConfigurationRevisionView",
  "type": "object"
}
```

<a id="schema-ConfigurationRunCreate"></a>

## ConfigurationRunCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration_revision` | integer | 是 | `minimum=1.0` |
| `session_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `input` | string anyOf array&lt;object&gt; | 是 | `anyOf=[{"type": "string"}, {"items": {"additionalProperties": true, "type": "object"}, "type": "array"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_revision": {
      "minimum": 1.0,
      "title": "Configuration Revision",
      "type": "integer"
    },
    "input": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        }
      ],
      "title": "Input"
    },
    "session_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Session Id"
    }
  },
  "required": [
    "configuration_revision",
    "input"
  ],
  "title": "ConfigurationRunCreate",
  "type": "object"
}
```

<a id="schema-ConfigurationSpec"></a>

## ConfigurationSpec

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `models` | object | 否 | `additionalProperties={"$ref": "#/components/schemas/ModelConfiguration"}`; `maxProperties=100` |
| `capabilities` | object | 否 | `additionalProperties={"$ref": "#/components/schemas/CapabilitySelection"}`; `maxProperties=100` |
| `hooks` | array&lt;[ConfigurationHook](schemas.md#schema-ConfigurationHook)&gt; | 否 | `maxItems=64` |
| `environment_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `assets` | array&lt;[AssetRef](schemas.md#schema-AssetRef)&gt; | 否 | `maxItems=100` |
| `outputs` | array&lt;[RunOutput](schemas.md#schema-RunOutput)&gt; | 否 | `maxItems=100` |

```json
{
  "additionalProperties": false,
  "properties": {
    "assets": {
      "items": {
        "$ref": "#/components/schemas/AssetRef"
      },
      "maxItems": 100,
      "title": "Assets",
      "type": "array"
    },
    "capabilities": {
      "additionalProperties": {
        "$ref": "#/components/schemas/CapabilitySelection"
      },
      "maxProperties": 100,
      "title": "Capabilities",
      "type": "object"
    },
    "environment_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment Id"
    },
    "hooks": {
      "items": {
        "$ref": "#/components/schemas/ConfigurationHook"
      },
      "maxItems": 64,
      "title": "Hooks",
      "type": "array"
    },
    "models": {
      "additionalProperties": {
        "$ref": "#/components/schemas/ModelConfiguration"
      },
      "maxProperties": 100,
      "title": "Models",
      "type": "object"
    },
    "outputs": {
      "items": {
        "$ref": "#/components/schemas/RunOutput"
      },
      "maxItems": 100,
      "title": "Outputs",
      "type": "array"
    }
  },
  "title": "ConfigurationSpec",
  "type": "object"
}
```

<a id="schema-ConfigurationUpdate"></a>

## ConfigurationUpdate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `specification` | [ConfigurationSpec](schemas.md#schema-ConfigurationSpec) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "specification": {
      "$ref": "#/components/schemas/ConfigurationSpec"
    }
  },
  "required": [
    "expected_revision",
    "specification"
  ],
  "title": "ConfigurationUpdate",
  "type": "object"
}
```

<a id="schema-ConfigurationView"></a>

## ConfigurationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `owner_user_id` | string (uuid) | 是 | `format="uuid"` |
| `definition_revision_id` | string (uuid) | 是 | `format="uuid"` |
| `root_directory_id` | string (uuid) | 是 | `format="uuid"` |
| `name` | string | 是 | — |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "definition_revision_id": {
      "format": "uuid",
      "title": "Definition Revision Id",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "owner_user_id": {
      "format": "uuid",
      "title": "Owner User Id",
      "type": "string"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "root_directory_id": {
      "format": "uuid",
      "title": "Root Directory Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "owner_user_id",
    "definition_revision_id",
    "root_directory_id",
    "name",
    "status",
    "revision"
  ],
  "title": "ConfigurationView",
  "type": "object"
}
```

<a id="schema-CreateInvitationRequest"></a>

## CreateInvitationRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `email` | string (email) | 是 | `format="email"`; `maxLength=320` |
| `ttl_seconds` | integer anyOf null | 否 | `anyOf=[{"maximum": 2592000.0, "minimum": 3600.0, "type": "integer"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "email": {
      "format": "email",
      "maxLength": 320,
      "title": "Email",
      "type": "string"
    },
    "ttl_seconds": {
      "anyOf": [
        {
          "maximum": 2592000.0,
          "minimum": 3600.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ttl Seconds"
    }
  },
  "required": [
    "email"
  ],
  "title": "CreateInvitationRequest",
  "type": "object"
}
```

<a id="schema-DefinitionCreate"></a>

## DefinitionCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `maxLength=120`; `minLength=1` |
| `kind` | string | 否 | `default="flow"`; `enum=["agent_graph", "flow"]` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `spec` | [AgentGraphSpec](schemas.md#schema-AgentGraphSpec) anyOf [FlowDefinitionSpec-Input](schemas.md#schema-FlowDefinitionSpec-Input) | 是 | `anyOf=[{"$ref": "#/components/schemas/AgentGraphSpec"}, {"$ref": "#/components/schemas/FlowDefinitionSpec-Input"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "kind": {
      "default": "flow",
      "enum": [
        "agent_graph",
        "flow"
      ],
      "title": "Kind",
      "type": "string"
    },
    "name": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "spec": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AgentGraphSpec"
        },
        {
          "$ref": "#/components/schemas/FlowDefinitionSpec-Input"
        }
      ],
      "title": "Spec"
    },
    "visibility": {
      "default": "private",
      "enum": [
        "private",
        "public"
      ],
      "title": "Visibility",
      "type": "string"
    }
  },
  "required": [
    "name",
    "spec"
  ],
  "title": "DefinitionCreate",
  "type": "object"
}
```

<a id="schema-DefinitionGrantRequest"></a>

## DefinitionGrantRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `user_id` | string (uuid) | 是 | `format="uuid"` |
| `permissions` | array&lt;string&gt; | 是 | `maxItems=4`; `minItems=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "permissions": {
      "items": {
        "enum": [
          "read",
          "use",
          "edit",
          "manage"
        ],
        "type": "string"
      },
      "maxItems": 4,
      "minItems": 1,
      "title": "Permissions",
      "type": "array"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "permissions"
  ],
  "title": "DefinitionGrantRequest",
  "type": "object"
}
```

<a id="schema-DefinitionInput"></a>

## DefinitionInput

Common definition metadata; the discriminated declaration chooses its type.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `maxLength=100`; `minLength=1` |
| `name` | string | 是 | `maxLength=160`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `source` | string | 否 | `default="user"`; `enum=["user", "system"]` |
| `declaration` | [McpDeclaration](schemas.md#schema-McpDeclaration) oneOf [ToolDeclaration](schemas.md#schema-ToolDeclaration) oneOf [SkillDeclaration](schemas.md#schema-SkillDeclaration) oneOf [ChannelDeclaration](schemas.md#schema-ChannelDeclaration) oneOf [ExtensionDeclaration](schemas.md#schema-ExtensionDeclaration) | 是 | `discriminator={"mapping": {"channel": "#/components/schemas/ChannelDeclaration", "extension": "#/components/schemas/ExtensionDeclaration", "mcp": "#/components/schemas/McpDeclaration", "skill": "#/components/schemas/SkillDeclaration", "tool": "#/components/schemas/ToolDeclaration"}, "propertyName": "type"}`; `oneOf=[{"$ref": "#/components/schemas/McpDeclaration"}, {"$ref": "#/components/schemas/ToolDeclaration"}, {"$ref": "#/components/schemas/SkillDeclaration"}, {"$ref": "#/components/schemas/ChannelDeclaration"}, {"$ref": "#/components/schemas/ExtensionDeclaration"}]` |
| `resource_manifest` | array&lt;object&gt; | 否 | `maxItems=512` |
| `dependencies` | array&lt;object&gt; | 否 | `maxItems=128` |
| `flow_templates` | array&lt;object&gt; | 否 | `maxItems=512` |

```json
{
  "additionalProperties": false,
  "description": "Common definition metadata; the discriminated declaration chooses its type.",
  "properties": {
    "declaration": {
      "discriminator": {
        "mapping": {
          "channel": "#/components/schemas/ChannelDeclaration",
          "extension": "#/components/schemas/ExtensionDeclaration",
          "mcp": "#/components/schemas/McpDeclaration",
          "skill": "#/components/schemas/SkillDeclaration",
          "tool": "#/components/schemas/ToolDeclaration"
        },
        "propertyName": "type"
      },
      "oneOf": [
        {
          "$ref": "#/components/schemas/McpDeclaration"
        },
        {
          "$ref": "#/components/schemas/ToolDeclaration"
        },
        {
          "$ref": "#/components/schemas/SkillDeclaration"
        },
        {
          "$ref": "#/components/schemas/ChannelDeclaration"
        },
        {
          "$ref": "#/components/schemas/ExtensionDeclaration"
        }
      ],
      "title": "Declaration"
    },
    "dependencies": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 128,
      "title": "Dependencies",
      "type": "array"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
    },
    "flow_templates": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 512,
      "title": "Flow Templates",
      "type": "array"
    },
    "key": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Key",
      "type": "string"
    },
    "name": {
      "maxLength": 160,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "resource_manifest": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 512,
      "title": "Resource Manifest",
      "type": "array"
    },
    "source": {
      "default": "user",
      "enum": [
        "user",
        "system"
      ],
      "title": "Source",
      "type": "string"
    },
    "visibility": {
      "default": "private",
      "enum": [
        "private",
        "public"
      ],
      "title": "Visibility",
      "type": "string"
    }
  },
  "required": [
    "key",
    "name",
    "declaration"
  ],
  "title": "DefinitionInput",
  "type": "object"
}
```

<a id="schema-DefinitionRevisionView"></a>

## DefinitionRevisionView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `definition_id` | string (uuid) | 是 | `format="uuid"` |
| `number` | integer | 是 | — |
| `digest` | string | 是 | — |
| `spec` | [AgentGraphSpec](schemas.md#schema-AgentGraphSpec) anyOf [FlowDefinitionSpec-Output](schemas.md#schema-FlowDefinitionSpec-Output) | 是 | `anyOf=[{"$ref": "#/components/schemas/AgentGraphSpec"}, {"$ref": "#/components/schemas/FlowDefinitionSpec-Output"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "definition_id": {
      "format": "uuid",
      "title": "Definition Id",
      "type": "string"
    },
    "digest": {
      "title": "Digest",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "number": {
      "title": "Number",
      "type": "integer"
    },
    "spec": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AgentGraphSpec"
        },
        {
          "$ref": "#/components/schemas/FlowDefinitionSpec-Output"
        }
      ],
      "title": "Spec"
    }
  },
  "required": [
    "id",
    "definition_id",
    "number",
    "digest",
    "spec"
  ],
  "title": "DefinitionRevisionView",
  "type": "object"
}
```

<a id="schema-DefinitionUpdate"></a>

## DefinitionUpdate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=1.0` |
| `name` | string | 是 | `maxLength=120`; `minLength=1` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `spec` | [AgentGraphSpec](schemas.md#schema-AgentGraphSpec) anyOf [FlowDefinitionSpec-Input](schemas.md#schema-FlowDefinitionSpec-Input) | 是 | `anyOf=[{"$ref": "#/components/schemas/AgentGraphSpec"}, {"$ref": "#/components/schemas/FlowDefinitionSpec-Input"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 1.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "name": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "spec": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AgentGraphSpec"
        },
        {
          "$ref": "#/components/schemas/FlowDefinitionSpec-Input"
        }
      ],
      "title": "Spec"
    },
    "visibility": {
      "default": "private",
      "enum": [
        "private",
        "public"
      ],
      "title": "Visibility",
      "type": "string"
    }
  },
  "required": [
    "expected_revision",
    "name",
    "spec"
  ],
  "title": "DefinitionUpdate",
  "type": "object"
}
```

<a id="schema-DefinitionView"></a>

## DefinitionView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `name` | string | 是 | — |
| `owner_id` | string (uuid) | 是 | `format="uuid"` |
| `kind` | string | 是 | — |
| `visibility` | string | 是 | — |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |
| `draft` | [AgentGraphSpec](schemas.md#schema-AgentGraphSpec) anyOf [FlowDefinitionSpec-Output](schemas.md#schema-FlowDefinitionSpec-Output) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/AgentGraphSpec"}, {"$ref": "#/components/schemas/FlowDefinitionSpec-Output"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "draft": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/AgentGraphSpec"
        },
        {
          "$ref": "#/components/schemas/FlowDefinitionSpec-Output"
        },
        {
          "type": "null"
        }
      ],
      "title": "Draft"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "owner_id": {
      "format": "uuid",
      "title": "Owner Id",
      "type": "string"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "visibility": {
      "title": "Visibility",
      "type": "string"
    }
  },
  "required": [
    "id",
    "name",
    "owner_id",
    "kind",
    "visibility",
    "status",
    "revision"
  ],
  "title": "DefinitionView",
  "type": "object"
}
```

<a id="schema-DeploymentRequest"></a>

## DeploymentRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `operation_id` | string | 是 | `maxLength=200`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "operation_id": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Operation Id",
      "type": "string"
    }
  },
  "required": [
    "expected_revision",
    "operation_id"
  ],
  "title": "DeploymentRequest",
  "type": "object"
}
```

<a id="schema-DeploymentView"></a>

## DeploymentView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `instance_id` | string (uuid) | 是 | `format="uuid"` |
| `instance_revision` | integer | 是 | — |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |
| `error_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "instance_id": {
      "format": "uuid",
      "title": "Instance Id",
      "type": "string"
    },
    "instance_revision": {
      "title": "Instance Revision",
      "type": "integer"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "instance_id",
    "instance_revision",
    "status",
    "revision",
    "error_code"
  ],
  "title": "DeploymentView",
  "type": "object"
}
```

<a id="schema-DirectoryCreateRequest"></a>

## DirectoryCreateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `path` | string | 是 | `maxLength=1000`; `minLength=1` |
| `parent_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "parent_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parent Id"
    },
    "path": {
      "maxLength": 1000,
      "minLength": 1,
      "title": "Path",
      "type": "string"
    }
  },
  "required": [
    "path"
  ],
  "title": "DirectoryCreateRequest",
  "type": "object"
}
```

<a id="schema-DirectoryView"></a>

## DirectoryView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `directory_id` | string (uuid) | 是 | `format="uuid"` |
| `path` | string | 是 | — |
| `parent_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "directory_id": {
      "format": "uuid",
      "title": "Directory Id",
      "type": "string"
    },
    "parent_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parent Id"
    },
    "path": {
      "title": "Path",
      "type": "string"
    }
  },
  "required": [
    "directory_id",
    "path",
    "parent_id",
    "created_at"
  ],
  "title": "DirectoryView",
  "type": "object"
}
```

<a id="schema-DraftInput"></a>

## DraftInput

Complete static draft input; publishing freezes this validated content.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `declaration` | [McpDeclaration](schemas.md#schema-McpDeclaration) oneOf [ToolDeclaration](schemas.md#schema-ToolDeclaration) oneOf [SkillDeclaration](schemas.md#schema-SkillDeclaration) oneOf [ChannelDeclaration](schemas.md#schema-ChannelDeclaration) oneOf [ExtensionDeclaration](schemas.md#schema-ExtensionDeclaration) | 是 | `discriminator={"mapping": {"channel": "#/components/schemas/ChannelDeclaration", "extension": "#/components/schemas/ExtensionDeclaration", "mcp": "#/components/schemas/McpDeclaration", "skill": "#/components/schemas/SkillDeclaration", "tool": "#/components/schemas/ToolDeclaration"}, "propertyName": "type"}`; `oneOf=[{"$ref": "#/components/schemas/McpDeclaration"}, {"$ref": "#/components/schemas/ToolDeclaration"}, {"$ref": "#/components/schemas/SkillDeclaration"}, {"$ref": "#/components/schemas/ChannelDeclaration"}, {"$ref": "#/components/schemas/ExtensionDeclaration"}]` |
| `resource_manifest` | array&lt;object&gt; | 否 | `maxItems=512` |
| `dependencies` | array&lt;object&gt; | 否 | `maxItems=128` |
| `flow_templates` | array&lt;object&gt; | 否 | `maxItems=512` |

```json
{
  "additionalProperties": false,
  "description": "Complete static draft input; publishing freezes this validated content.",
  "properties": {
    "declaration": {
      "discriminator": {
        "mapping": {
          "channel": "#/components/schemas/ChannelDeclaration",
          "extension": "#/components/schemas/ExtensionDeclaration",
          "mcp": "#/components/schemas/McpDeclaration",
          "skill": "#/components/schemas/SkillDeclaration",
          "tool": "#/components/schemas/ToolDeclaration"
        },
        "propertyName": "type"
      },
      "oneOf": [
        {
          "$ref": "#/components/schemas/McpDeclaration"
        },
        {
          "$ref": "#/components/schemas/ToolDeclaration"
        },
        {
          "$ref": "#/components/schemas/SkillDeclaration"
        },
        {
          "$ref": "#/components/schemas/ChannelDeclaration"
        },
        {
          "$ref": "#/components/schemas/ExtensionDeclaration"
        }
      ],
      "title": "Declaration"
    },
    "dependencies": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 128,
      "title": "Dependencies",
      "type": "array"
    },
    "flow_templates": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 512,
      "title": "Flow Templates",
      "type": "array"
    },
    "resource_manifest": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 512,
      "title": "Resource Manifest",
      "type": "array"
    }
  },
  "required": [
    "declaration"
  ],
  "title": "DraftInput",
  "type": "object"
}
```

<a id="schema-EnvironmentCommandRequest"></a>

## EnvironmentCommandRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command` | array&lt;string&gt; | 是 | `maxItems=100`; `minItems=1` |
| `timeout_seconds` | integer | 否 | `default=60`; `maximum=3600.0`; `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "command": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "minItems": 1,
      "title": "Command",
      "type": "array"
    },
    "timeout_seconds": {
      "default": 60,
      "maximum": 3600.0,
      "minimum": 1.0,
      "title": "Timeout Seconds",
      "type": "integer"
    }
  },
  "required": [
    "command"
  ],
  "title": "EnvironmentCommandRequest",
  "type": "object"
}
```

<a id="schema-EnvironmentCreateRequest"></a>

## EnvironmentCreateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `root_directory_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `image` | string | 是 | `maxLength=500`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "image": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Image",
      "type": "string"
    },
    "root_directory_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Root Directory Id"
    }
  },
  "required": [
    "image"
  ],
  "title": "EnvironmentCreateRequest",
  "type": "object"
}
```

<a id="schema-EnvironmentFileDownloadRequest"></a>

## EnvironmentFileDownloadRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `path` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "path": {
      "title": "Path",
      "type": "string"
    }
  },
  "required": [
    "path"
  ],
  "title": "EnvironmentFileDownloadRequest",
  "type": "object"
}
```

<a id="schema-EnvironmentFileUploadRequest"></a>

## EnvironmentFileUploadRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `path` | string | 是 | — |
| `content_base64` | string | 是 | `maxLength=6000000` |

```json
{
  "additionalProperties": false,
  "properties": {
    "content_base64": {
      "maxLength": 6000000,
      "title": "Content Base64",
      "type": "string"
    },
    "path": {
      "title": "Path",
      "type": "string"
    }
  },
  "required": [
    "path",
    "content_base64"
  ],
  "title": "EnvironmentFileUploadRequest",
  "type": "object"
}
```

<a id="schema-EnvironmentView"></a>

## EnvironmentView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `environment_id` | string (uuid) | 是 | `format="uuid"` |
| `scope_id` | string (uuid) | 是 | `format="uuid"` |
| `owner_ref` | string | 是 | — |
| `status` | string | 是 | — |
| `error_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "properties": {
    "environment_id": {
      "format": "uuid",
      "title": "Environment Id",
      "type": "string"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "owner_ref": {
      "title": "Owner Ref",
      "type": "string"
    },
    "scope_id": {
      "format": "uuid",
      "title": "Scope Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "environment_id",
    "scope_id",
    "owner_ref",
    "status",
    "error_code"
  ],
  "title": "EnvironmentView",
  "type": "object"
}
```

<a id="schema-ErrorInfo"></a>

## ErrorInfo

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `code` | string | 是 | — |
| `message` | string | 是 | — |
| `request_id` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `details` | object anyOf array&lt;object&gt; anyOf null | 否 | `anyOf=[{"additionalProperties": true, "type": "object"}, {"items": {"additionalProperties": true, "type": "object"}, "type": "array"}, {"type": "null"}]` |

```json
{
  "properties": {
    "code": {
      "title": "Code",
      "type": "string"
    },
    "details": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Details"
    },
    "message": {
      "title": "Message",
      "type": "string"
    },
    "request_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Request Id"
    }
  },
  "required": [
    "code",
    "message",
    "request_id"
  ],
  "title": "ErrorInfo",
  "type": "object"
}
```

<a id="schema-ErrorResponse"></a>

## ErrorResponse

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `error` | [ErrorInfo](schemas.md#schema-ErrorInfo) | 是 | — |

```json
{
  "properties": {
    "error": {
      "$ref": "#/components/schemas/ErrorInfo"
    }
  },
  "required": [
    "error"
  ],
  "title": "ErrorResponse",
  "type": "object"
}
```

<a id="schema-EventPage"></a>

## EventPage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `events` | array&lt;[EventView](schemas.md#schema-EventView)&gt; | 是 | — |
| `next_cursor` | integer | 是 | — |

```json
{
  "properties": {
    "events": {
      "items": {
        "$ref": "#/components/schemas/EventView"
      },
      "title": "Events",
      "type": "array"
    },
    "next_cursor": {
      "title": "Next Cursor",
      "type": "integer"
    }
  },
  "required": [
    "events",
    "next_cursor"
  ],
  "title": "EventPage",
  "type": "object"
}
```

<a id="schema-EventView"></a>

## EventView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `sequence` | integer | 是 | — |
| `kind` | string | 是 | — |
| `payload` | object | 是 | `additionalProperties=true` |

```json
{
  "properties": {
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "payload": {
      "additionalProperties": true,
      "title": "Payload",
      "type": "object"
    },
    "sequence": {
      "title": "Sequence",
      "type": "integer"
    }
  },
  "required": [
    "sequence",
    "kind",
    "payload"
  ],
  "title": "EventView",
  "type": "object"
}
```

<a id="schema-ExecutionLimits"></a>

## ExecutionLimits

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `max_turns` | integer | 否 | `default=20`; `maximum=100.0`; `minimum=1.0` |
| `execution_timeout_seconds` | integer | 否 | `default=3600`; `maximum=86400.0`; `minimum=1.0` |
| `approval_timeout_seconds` | integer | 否 | `default=86400`; `maximum=604800.0`; `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "approval_timeout_seconds": {
      "default": 86400,
      "maximum": 604800.0,
      "minimum": 1.0,
      "title": "Approval Timeout Seconds",
      "type": "integer"
    },
    "execution_timeout_seconds": {
      "default": 3600,
      "maximum": 86400.0,
      "minimum": 1.0,
      "title": "Execution Timeout Seconds",
      "type": "integer"
    },
    "max_turns": {
      "default": 20,
      "maximum": 100.0,
      "minimum": 1.0,
      "title": "Max Turns",
      "type": "integer"
    }
  },
  "title": "ExecutionLimits",
  "type": "object"
}
```

<a id="schema-ExecutionRequest"></a>

## ExecutionRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration_source` | string anyOf null | 否 | `anyOf=[{"pattern": "^[a-fA-F0-9-]{36}$", "type": "string"}, {"type": "null"}]` |
| `parent_invocation_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `hooks` | array&lt;[InstalledHook](schemas.md#schema-InstalledHook)&gt; | 否 | `maxItems=64` |
| `assets` | array&lt;[AssetRef](schemas.md#schema-AssetRef)&gt; | 否 | `maxItems=100` |
| `outputs` | array&lt;[RunOutput](schemas.md#schema-RunOutput)&gt; | 否 | `maxItems=100` |
| `session_id` | string (uuid) | 是 | `format="uuid"` |
| `input` | string anyOf array&lt;object&gt; | 是 | `anyOf=[{"type": "string"}, {"items": {"additionalProperties": true, "type": "object"}, "type": "array"}]` |
| `entry_agent` | string | 是 | `pattern="^[a-zA-Z0-9_-]{1,64}$"` |
| `agents` | object | 是 | `additionalProperties={"$ref": "#/components/schemas/AgentConfiguration"}`; `maxProperties=100`; `minProperties=1` |
| `environment_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limits` | [ExecutionLimits](schemas.md#schema-ExecutionLimits) | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "agents": {
      "additionalProperties": {
        "$ref": "#/components/schemas/AgentConfiguration"
      },
      "maxProperties": 100,
      "minProperties": 1,
      "title": "Agents",
      "type": "object"
    },
    "assets": {
      "items": {
        "$ref": "#/components/schemas/AssetRef"
      },
      "maxItems": 100,
      "title": "Assets",
      "type": "array"
    },
    "configuration_source": {
      "anyOf": [
        {
          "pattern": "^[a-fA-F0-9-]{36}$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Source"
    },
    "entry_agent": {
      "pattern": "^[a-zA-Z0-9_-]{1,64}$",
      "title": "Entry Agent",
      "type": "string"
    },
    "environment_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment Id"
    },
    "hooks": {
      "items": {
        "$ref": "#/components/schemas/InstalledHook"
      },
      "maxItems": 64,
      "title": "Hooks",
      "type": "array"
    },
    "input": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "items": {
            "additionalProperties": true,
            "type": "object"
          },
          "type": "array"
        }
      ],
      "title": "Input"
    },
    "limits": {
      "$ref": "#/components/schemas/ExecutionLimits"
    },
    "outputs": {
      "items": {
        "$ref": "#/components/schemas/RunOutput"
      },
      "maxItems": 100,
      "title": "Outputs",
      "type": "array"
    },
    "parent_invocation_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parent Invocation Id"
    },
    "session_id": {
      "format": "uuid",
      "title": "Session Id",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "input",
    "entry_agent",
    "agents"
  ],
  "title": "ExecutionRequest",
  "type": "object"
}
```

<a id="schema-ExtensionAuditAppend"></a>

## ExtensionAuditAppend

One extension-reported fact; actor and scope come from the verified call.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `action` | string | 是 | `maxLength=200`; `minLength=1` |
| `target_type` | string | 是 | `maxLength=200`; `minLength=1` |
| `target_id` | string | 是 | `maxLength=200`; `minLength=1` |
| `summary` | string | 是 | `maxLength=2000`; `minLength=1` |
| `details` | object | 否 | `additionalProperties=true` |
| `evidence_refs` | array&lt;string&gt; | 否 | `maxItems=20` |
| `idempotency_key` | string | 是 | `maxLength=200`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "description": "One extension-reported fact; actor and scope come from the verified call.",
  "properties": {
    "action": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Action",
      "type": "string"
    },
    "details": {
      "additionalProperties": true,
      "title": "Details",
      "type": "object"
    },
    "evidence_refs": {
      "items": {
        "type": "string"
      },
      "maxItems": 20,
      "title": "Evidence Refs",
      "type": "array"
    },
    "idempotency_key": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Idempotency Key",
      "type": "string"
    },
    "summary": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Summary",
      "type": "string"
    },
    "target_id": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Target Id",
      "type": "string"
    },
    "target_type": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Target Type",
      "type": "string"
    }
  },
  "required": [
    "action",
    "target_type",
    "target_id",
    "summary",
    "idempotency_key"
  ],
  "title": "ExtensionAuditAppend",
  "type": "object"
}
```

<a id="schema-ExtensionConfiguration"></a>

## ExtensionConfiguration

The export contract one extension definition publishes.

An extension declares the exports its package installs with — typed
operations with their effects, hooks bound to fixed stages, and event
consumers — plus the permissions those exports need. The declaration
is frozen at publish; nothing is imported, registered or run here.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `operations` | array&lt;[ExtensionOperation](schemas.md#schema-ExtensionOperation)&gt; | 否 | `maxItems=256` |
| `hooks` | array&lt;[ExtensionHook](schemas.md#schema-ExtensionHook)&gt; | 否 | `maxItems=256` |
| `events` | array&lt;[ExtensionEvent](schemas.md#schema-ExtensionEvent)&gt; | 否 | `maxItems=256` |
| `permissions` | array&lt;string&gt; | 否 | `maxItems=128` |

```json
{
  "additionalProperties": false,
  "description": "The export contract one extension definition publishes.\n\nAn extension declares the exports its package installs with — typed\noperations with their effects, hooks bound to fixed stages, and event\nconsumers — plus the permissions those exports need. The declaration\nis frozen at publish; nothing is imported, registered or run here.",
  "properties": {
    "events": {
      "items": {
        "$ref": "#/components/schemas/ExtensionEvent"
      },
      "maxItems": 256,
      "title": "Events",
      "type": "array"
    },
    "hooks": {
      "items": {
        "$ref": "#/components/schemas/ExtensionHook"
      },
      "maxItems": 256,
      "title": "Hooks",
      "type": "array"
    },
    "operations": {
      "items": {
        "$ref": "#/components/schemas/ExtensionOperation"
      },
      "maxItems": 256,
      "title": "Operations",
      "type": "array"
    },
    "permissions": {
      "items": {
        "type": "string"
      },
      "maxItems": 128,
      "title": "Permissions",
      "type": "array"
    }
  },
  "title": "ExtensionConfiguration",
  "type": "object"
}
```

<a id="schema-ExtensionDeclaration"></a>

## ExtensionDeclaration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `type` | string | 是 | `const="extension"` |
| `configuration` | [ExtensionConfiguration](schemas.md#schema-ExtensionConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ExtensionConfiguration"
    },
    "type": {
      "const": "extension",
      "title": "Type",
      "type": "string"
    }
  },
  "required": [
    "type",
    "configuration"
  ],
  "title": "ExtensionDeclaration",
  "type": "object"
}
```

<a id="schema-ExtensionEvent"></a>

## ExtensionEvent

One declared event consumer with its operation and idempotency fields.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `export_id` | string | 是 | `maxLength=100`; `minLength=1` |
| `description` | string | 是 | `maxLength=2000`; `minLength=1` |
| `source_types` | array&lt;string&gt; | 是 | `minItems=1` |
| `source_versions` | array&lt;string&gt; | 是 | `minItems=1` |
| `operation_export_id` | string | 是 | `maxLength=100`; `minLength=1` |
| `filter_scope` | array&lt;string&gt; | 否 | — |
| `idempotency_key_fields` | array&lt;string&gt; | 是 | `minItems=1` |

```json
{
  "additionalProperties": false,
  "description": "One declared event consumer with its operation and idempotency fields.",
  "properties": {
    "description": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Description",
      "type": "string"
    },
    "export_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Export Id",
      "type": "string"
    },
    "filter_scope": {
      "items": {
        "type": "string"
      },
      "title": "Filter Scope",
      "type": "array"
    },
    "idempotency_key_fields": {
      "items": {
        "type": "string"
      },
      "minItems": 1,
      "title": "Idempotency Key Fields",
      "type": "array"
    },
    "operation_export_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Operation Export Id",
      "type": "string"
    },
    "source_types": {
      "items": {
        "type": "string"
      },
      "minItems": 1,
      "title": "Source Types",
      "type": "array"
    },
    "source_versions": {
      "items": {
        "type": "string"
      },
      "minItems": 1,
      "title": "Source Versions",
      "type": "array"
    }
  },
  "required": [
    "export_id",
    "description",
    "source_types",
    "source_versions",
    "operation_export_id",
    "idempotency_key_fields"
  ],
  "title": "ExtensionEvent",
  "type": "object"
}
```

<a id="schema-ExtensionHook"></a>

## ExtensionHook

One declared hook bound to fixed stages with a failure policy.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `export_id` | string | 是 | `maxLength=100`; `minLength=1` |
| `description` | string | 是 | `maxLength=2000`; `minLength=1` |
| `stages` | array&lt;string&gt; | 是 | `maxItems=9`; `minItems=1` |
| `input_schema` | object | 否 | `additionalProperties=true` |
| `patch_fields` | array&lt;string&gt; | 否 | `maxItems=32` |
| `failure_policy` | string | 否 | `default="block"`; `enum=["block", "observe"]` |
| `permissions` | array&lt;string&gt; | 否 | `maxItems=128` |
| `timeout_seconds` | integer | 否 | `default=60`; `maximum=3600.0`; `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "description": "One declared hook bound to fixed stages with a failure policy.",
  "properties": {
    "description": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Description",
      "type": "string"
    },
    "export_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Export Id",
      "type": "string"
    },
    "failure_policy": {
      "default": "block",
      "enum": [
        "block",
        "observe"
      ],
      "title": "Failure Policy",
      "type": "string"
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "patch_fields": {
      "items": {
        "type": "string"
      },
      "maxItems": 32,
      "title": "Patch Fields",
      "type": "array"
    },
    "permissions": {
      "items": {
        "type": "string"
      },
      "maxItems": 128,
      "title": "Permissions",
      "type": "array"
    },
    "stages": {
      "items": {
        "type": "string"
      },
      "maxItems": 9,
      "minItems": 1,
      "title": "Stages",
      "type": "array"
    },
    "timeout_seconds": {
      "default": 60,
      "maximum": 3600.0,
      "minimum": 1.0,
      "title": "Timeout Seconds",
      "type": "integer"
    }
  },
  "required": [
    "export_id",
    "description",
    "stages"
  ],
  "title": "ExtensionHook",
  "type": "object"
}
```

<a id="schema-ExtensionOperation"></a>

## ExtensionOperation

One declared operation: typed I/O, effects, timeout and permissions.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `export_id` | string | 是 | `maxLength=100`; `minLength=1` |
| `description` | string | 是 | `maxLength=2000`; `minLength=1` |
| `input_schema` | object | 否 | `additionalProperties=true` |
| `output_schema` | object | 否 | `additionalProperties=true` |
| `effects` | array&lt;string&gt; | 否 | `maxItems=7` |
| `timeout_seconds` | integer | 否 | `default=60`; `maximum=3600.0`; `minimum=1.0` |
| `permissions` | array&lt;string&gt; | 否 | `maxItems=128` |

```json
{
  "additionalProperties": false,
  "description": "One declared operation: typed I/O, effects, timeout and permissions.",
  "properties": {
    "description": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Description",
      "type": "string"
    },
    "effects": {
      "items": {
        "type": "string"
      },
      "maxItems": 7,
      "title": "Effects",
      "type": "array"
    },
    "export_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Export Id",
      "type": "string"
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "output_schema": {
      "additionalProperties": true,
      "title": "Output Schema",
      "type": "object"
    },
    "permissions": {
      "items": {
        "type": "string"
      },
      "maxItems": 128,
      "title": "Permissions",
      "type": "array"
    },
    "timeout_seconds": {
      "default": 60,
      "maximum": 3600.0,
      "minimum": 1.0,
      "title": "Timeout Seconds",
      "type": "integer"
    }
  },
  "required": [
    "export_id",
    "description"
  ],
  "title": "ExtensionOperation",
  "type": "object"
}
```

<a id="schema-ExtensionReconcileRequest"></a>

## ExtensionReconcileRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `outcome` | string | 是 | `enum=["completed", "not_executed", "failed"]` |
| `evidence` | object | 是 | `additionalProperties=true`; `minProperties=1` |
| `result` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "evidence": {
      "additionalProperties": true,
      "minProperties": 1,
      "title": "Evidence",
      "type": "object"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "outcome": {
      "enum": [
        "completed",
        "not_executed",
        "failed"
      ],
      "title": "Outcome",
      "type": "string"
    },
    "result": {
      "additionalProperties": true,
      "title": "Result",
      "type": "object"
    }
  },
  "required": [
    "expected_revision",
    "outcome",
    "evidence"
  ],
  "title": "ExtensionReconcileRequest",
  "type": "object"
}
```

<a id="schema-FileCreateRequest"></a>

## FileCreateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `directory_id` | string (uuid) | 是 | `format="uuid"` |
| `name` | string | 是 | `maxLength=260`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "directory_id": {
      "format": "uuid",
      "title": "Directory Id",
      "type": "string"
    },
    "name": {
      "maxLength": 260,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    }
  },
  "required": [
    "directory_id",
    "name"
  ],
  "title": "FileCreateRequest",
  "type": "object"
}
```

<a id="schema-FilePage"></a>

## FilePage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `items` | array&lt;[FileView](schemas.md#schema-FileView)&gt; | 是 | — |
| `total` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "items": {
      "items": {
        "$ref": "#/components/schemas/FileView"
      },
      "title": "Items",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "items",
    "total"
  ],
  "title": "FilePage",
  "type": "object"
}
```

<a id="schema-FileVersionView"></a>

## FileVersionView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `file_id` | string (uuid) | 是 | `format="uuid"` |
| `version` | integer | 是 | — |
| `digest` | string | 是 | — |
| `size` | integer | 是 | — |
| `media_type` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `created_by_user_id` | string (uuid) | 是 | `format="uuid"` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "created_by_user_id": {
      "format": "uuid",
      "title": "Created By User Id",
      "type": "string"
    },
    "digest": {
      "title": "Digest",
      "type": "string"
    },
    "file_id": {
      "format": "uuid",
      "title": "File Id",
      "type": "string"
    },
    "media_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Media Type"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "version": {
      "title": "Version",
      "type": "integer"
    }
  },
  "required": [
    "file_id",
    "version",
    "digest",
    "size",
    "media_type",
    "created_by_user_id",
    "created_at"
  ],
  "title": "FileVersionView",
  "type": "object"
}
```

<a id="schema-FileView"></a>

## FileView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `file_id` | string (uuid) | 是 | `format="uuid"` |
| `directory_id` | string (uuid) | 是 | `format="uuid"` |
| `name` | string | 是 | — |
| `owner_user_id` | string (uuid) | 是 | `format="uuid"` |
| `latest_version` | integer | 是 | — |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "directory_id": {
      "format": "uuid",
      "title": "Directory Id",
      "type": "string"
    },
    "file_id": {
      "format": "uuid",
      "title": "File Id",
      "type": "string"
    },
    "latest_version": {
      "title": "Latest Version",
      "type": "integer"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "owner_user_id": {
      "format": "uuid",
      "title": "Owner User Id",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "file_id",
    "directory_id",
    "name",
    "owner_user_id",
    "latest_version",
    "created_at",
    "updated_at"
  ],
  "title": "FileView",
  "type": "object"
}
```

<a id="schema-FlowAuthoringContract"></a>

## FlowAuthoringContract

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `node_kinds` | array&lt;string&gt; | 是 | — |
| `mapping_roots` | array&lt;string&gt; | 是 | — |
| `max_nodes` | integer | 是 | — |
| `max_nested_depth` | integer | 是 | — |
| `definition_schema` | object | 是 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "definition_schema": {
      "additionalProperties": true,
      "title": "Definition Schema",
      "type": "object"
    },
    "mapping_roots": {
      "items": {
        "type": "string"
      },
      "title": "Mapping Roots",
      "type": "array"
    },
    "max_nested_depth": {
      "title": "Max Nested Depth",
      "type": "integer"
    },
    "max_nodes": {
      "title": "Max Nodes",
      "type": "integer"
    },
    "node_kinds": {
      "items": {
        "type": "string"
      },
      "title": "Node Kinds",
      "type": "array"
    }
  },
  "required": [
    "node_kinds",
    "mapping_roots",
    "max_nodes",
    "max_nested_depth",
    "definition_schema"
  ],
  "title": "FlowAuthoringContract",
  "type": "object"
}
```

<a id="schema-FlowCondition"></a>

## FlowCondition

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `operator` | string | 是 | `enum=["eq", "ne", "lt", "le", "gt", "ge", "contains", "exists"]` |
| `left` | any | 是 | — |
| `right` | any | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "left": {
      "title": "Left"
    },
    "operator": {
      "enum": [
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "contains",
        "exists"
      ],
      "title": "Operator",
      "type": "string"
    },
    "right": {
      "title": "Right"
    }
  },
  "required": [
    "operator",
    "left"
  ],
  "title": "FlowCondition",
  "type": "object"
}
```

<a id="schema-FlowControl"></a>

## FlowControl

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `action` | string | 是 | `enum=["pause", "resume", "cancel", "retry"]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "action": {
      "enum": [
        "pause",
        "resume",
        "cancel",
        "retry"
      ],
      "title": "Action",
      "type": "string"
    },
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    }
  },
  "required": [
    "command_id",
    "expected_revision",
    "action"
  ],
  "title": "FlowControl",
  "type": "object"
}
```

<a id="schema-FlowDefinitionSpec-Input"></a>

## FlowDefinitionSpec-Input

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `graph` | [FlowGraph-Input](schemas.md#schema-FlowGraph-Input) | 是 | — |
| `limits` | [FlowLimits](schemas.md#schema-FlowLimits) | 否 | — |
| `input_schema` | object | 否 | `additionalProperties=true` |
| `output_schema` | object | 否 | `additionalProperties=true` |
| `template_source` | string anyOf null | 否 | `anyOf=[{"maxLength": 240, "type": "string"}, {"type": "null"}]` |
| `policy` | [FlowExecutionPolicy](schemas.md#schema-FlowExecutionPolicy) | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "graph": {
      "$ref": "#/components/schemas/FlowGraph-Input"
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "limits": {
      "$ref": "#/components/schemas/FlowLimits"
    },
    "output_schema": {
      "additionalProperties": true,
      "title": "Output Schema",
      "type": "object"
    },
    "policy": {
      "$ref": "#/components/schemas/FlowExecutionPolicy"
    },
    "template_source": {
      "anyOf": [
        {
          "maxLength": 240,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Template Source"
    }
  },
  "required": [
    "graph"
  ],
  "title": "FlowDefinitionSpec",
  "type": "object"
}
```

<a id="schema-FlowDefinitionSpec-Output"></a>

## FlowDefinitionSpec-Output

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `graph` | [FlowGraph-Output](schemas.md#schema-FlowGraph-Output) | 是 | — |
| `limits` | [FlowLimits](schemas.md#schema-FlowLimits) | 否 | — |
| `input_schema` | object | 否 | `additionalProperties=true` |
| `output_schema` | object | 否 | `additionalProperties=true` |
| `template_source` | string anyOf null | 否 | `anyOf=[{"maxLength": 240, "type": "string"}, {"type": "null"}]` |
| `policy` | [FlowExecutionPolicy](schemas.md#schema-FlowExecutionPolicy) | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "graph": {
      "$ref": "#/components/schemas/FlowGraph-Output"
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "limits": {
      "$ref": "#/components/schemas/FlowLimits"
    },
    "output_schema": {
      "additionalProperties": true,
      "title": "Output Schema",
      "type": "object"
    },
    "policy": {
      "$ref": "#/components/schemas/FlowExecutionPolicy"
    },
    "template_source": {
      "anyOf": [
        {
          "maxLength": 240,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Template Source"
    }
  },
  "required": [
    "graph"
  ],
  "title": "FlowDefinitionSpec",
  "type": "object"
}
```

<a id="schema-FlowEventView"></a>

## FlowEventView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `sequence` | integer | 是 | — |
| `kind` | string | 是 | — |
| `summary` | object | 是 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "sequence": {
      "title": "Sequence",
      "type": "integer"
    },
    "summary": {
      "additionalProperties": true,
      "title": "Summary",
      "type": "object"
    }
  },
  "required": [
    "sequence",
    "kind",
    "summary"
  ],
  "title": "FlowEventView",
  "type": "object"
}
```

<a id="schema-FlowExecutionPolicy"></a>

## FlowExecutionPolicy

Published effects policy; retries resume receipts without resubmitting children.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `failure` | string | 否 | `const="cancel_owned"`; `default="cancel_owned"` |
| `retry` | string | 否 | `const="resume_receipts"`; `default="resume_receipts"` |
| `cancellation` | string | 否 | `const="confirm_owned"`; `default="confirm_owned"` |
| `activity_attempts` | integer | 否 | `default=3`; `maximum=10.0`; `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "description": "Published effects policy; retries resume receipts without resubmitting children.",
  "properties": {
    "activity_attempts": {
      "default": 3,
      "maximum": 10.0,
      "minimum": 1.0,
      "title": "Activity Attempts",
      "type": "integer"
    },
    "cancellation": {
      "const": "confirm_owned",
      "default": "confirm_owned",
      "title": "Cancellation",
      "type": "string"
    },
    "failure": {
      "const": "cancel_owned",
      "default": "cancel_owned",
      "title": "Failure",
      "type": "string"
    },
    "retry": {
      "const": "resume_receipts",
      "default": "resume_receipts",
      "title": "Retry",
      "type": "string"
    }
  },
  "title": "FlowExecutionPolicy",
  "type": "object"
}
```

<a id="schema-FlowExport"></a>

## FlowExport

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `instance_id` | string (uuid) | 是 | `format="uuid"` |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_revision` | integer | 是 | `minimum=0.0` |
| `artifact` | string | 是 | `pattern="^sha256:[a-f0-9]{64}$"` |
| `export_id` | string | 是 | `maxLength=100`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "artifact": {
      "pattern": "^sha256:[a-f0-9]{64}$",
      "title": "Artifact",
      "type": "string"
    },
    "configuration_revision": {
      "minimum": 0.0,
      "title": "Configuration Revision",
      "type": "integer"
    },
    "export_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Export Id",
      "type": "string"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "instance_id": {
      "format": "uuid",
      "title": "Instance Id",
      "type": "string"
    }
  },
  "required": [
    "instance_id",
    "installation_id",
    "configuration_revision",
    "artifact",
    "export_id"
  ],
  "title": "FlowExport",
  "type": "object"
}
```

<a id="schema-FlowGraph-Input"></a>

## FlowGraph-Input

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `nodes` | array&lt;[FlowNode-Input](schemas.md#schema-FlowNode-Input)&gt; | 是 | `maxItems=256`; `minItems=1` |
| `output` | any | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "nodes": {
      "items": {
        "$ref": "#/components/schemas/FlowNode-Input"
      },
      "maxItems": 256,
      "minItems": 1,
      "title": "Nodes",
      "type": "array"
    },
    "output": {
      "title": "Output"
    }
  },
  "required": [
    "nodes"
  ],
  "title": "FlowGraph",
  "type": "object"
}
```

<a id="schema-FlowGraph-Output"></a>

## FlowGraph-Output

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `nodes` | array&lt;[FlowNode-Output](schemas.md#schema-FlowNode-Output)&gt; | 是 | `maxItems=256`; `minItems=1` |
| `output` | any | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "nodes": {
      "items": {
        "$ref": "#/components/schemas/FlowNode-Output"
      },
      "maxItems": 256,
      "minItems": 1,
      "title": "Nodes",
      "type": "array"
    },
    "output": {
      "title": "Output"
    }
  },
  "required": [
    "nodes"
  ],
  "title": "FlowGraph",
  "type": "object"
}
```

<a id="schema-FlowLimits"></a>

## FlowLimits

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `max_steps` | integer | 否 | `default=256`; `maximum=4096.0`; `minimum=1.0` |
| `max_depth` | integer | 否 | `default=8`; `maximum=16.0`; `minimum=1.0` |
| `max_concurrency` | integer | 否 | `default=8`; `maximum=32.0`; `minimum=1.0` |
| `timeout_seconds` | integer | 否 | `default=3600`; `maximum=604800.0`; `minimum=1.0` |
| `model_tokens` | integer | 否 | `default=1000000`; `maximum=100000000.0`; `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "max_concurrency": {
      "default": 8,
      "maximum": 32.0,
      "minimum": 1.0,
      "title": "Max Concurrency",
      "type": "integer"
    },
    "max_depth": {
      "default": 8,
      "maximum": 16.0,
      "minimum": 1.0,
      "title": "Max Depth",
      "type": "integer"
    },
    "max_steps": {
      "default": 256,
      "maximum": 4096.0,
      "minimum": 1.0,
      "title": "Max Steps",
      "type": "integer"
    },
    "model_tokens": {
      "default": 1000000,
      "maximum": 100000000.0,
      "minimum": 0.0,
      "title": "Model Tokens",
      "type": "integer"
    },
    "timeout_seconds": {
      "default": 3600,
      "maximum": 604800.0,
      "minimum": 1.0,
      "title": "Timeout Seconds",
      "type": "integer"
    }
  },
  "title": "FlowLimits",
  "type": "object"
}
```

<a id="schema-FlowNode-Input"></a>

## FlowNode-Input

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `pattern="^[a-zA-Z][a-zA-Z0-9_-]{0,63}$"` |
| `kind` | string | 是 | `enum=["agent", "operation", "condition", "parallel", "join", "foreach", "repeat", "subflow", "wait", "expand"]` |
| `depends_on` | array&lt;string&gt; | 否 | `maxItems=256` |
| `arguments` | object | 否 | `additionalProperties=true` |
| `input_schema` | object | 否 | `additionalProperties=true` |
| `output_schema` | object | 否 | `additionalProperties=true` |
| `condition` | [FlowCondition](schemas.md#schema-FlowCondition) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/FlowCondition"}, {"type": "null"}]` |
| `run_if` | [FlowCondition](schemas.md#schema-FlowCondition) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/FlowCondition"}, {"type": "null"}]` |
| `export` | [FlowExport](schemas.md#schema-FlowExport) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/FlowExport"}, {"type": "null"}]` |
| `configuration_revision_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `revision_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `body` | [FlowGraph-Input](schemas.md#schema-FlowGraph-Input) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/FlowGraph-Input"}, {"type": "null"}]` |
| `max_iterations` | integer | 否 | `default=1`; `maximum=256.0`; `minimum=1.0` |
| `concurrency` | integer | 否 | `default=1`; `maximum=32.0`; `minimum=1.0` |
| `join` | string | 否 | `default="all"`; `enum=["all", "any", "quorum"]` |
| `quorum` | integer | 否 | `default=1`; `maximum=256.0`; `minimum=1.0` |
| `cancel_remaining` | boolean | 否 | `default=true` |
| `wait_kind` | string | 否 | `default="timer"`; `enum=["timer", "signal", "approval", "external"]` |
| `wait_seconds` | integer | 否 | `default=0`; `maximum=604800.0`; `minimum=0.0` |
| `signal_name` | string anyOf null | 否 | `anyOf=[{"maxLength": 100, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `approval_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `approver_user_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `external_step` | string anyOf null | 否 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `allowed_kinds` | array&lt;string&gt; | 否 | `maxItems=10` |
| `allowed_references` | array&lt;string&gt; | 否 | `maxItems=256` |
| `max_expanded_nodes` | integer | 否 | `default=32`; `maximum=256.0`; `minimum=1.0` |
| `approval_required` | boolean | 否 | `default=false` |

```json
{
  "additionalProperties": false,
  "properties": {
    "allowed_kinds": {
      "items": {
        "enum": [
          "agent",
          "operation",
          "condition",
          "parallel",
          "join",
          "foreach",
          "repeat",
          "subflow",
          "wait",
          "expand"
        ],
        "type": "string"
      },
      "maxItems": 10,
      "title": "Allowed Kinds",
      "type": "array"
    },
    "allowed_references": {
      "items": {
        "type": "string"
      },
      "maxItems": 256,
      "title": "Allowed References",
      "type": "array"
    },
    "approval_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approval Id"
    },
    "approval_required": {
      "default": false,
      "title": "Approval Required",
      "type": "boolean"
    },
    "approver_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approver User Id"
    },
    "arguments": {
      "additionalProperties": true,
      "title": "Arguments",
      "type": "object"
    },
    "body": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlowGraph-Input"
        },
        {
          "type": "null"
        }
      ]
    },
    "cancel_remaining": {
      "default": true,
      "title": "Cancel Remaining",
      "type": "boolean"
    },
    "concurrency": {
      "default": 1,
      "maximum": 32.0,
      "minimum": 1.0,
      "title": "Concurrency",
      "type": "integer"
    },
    "condition": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlowCondition"
        },
        {
          "type": "null"
        }
      ]
    },
    "configuration_revision_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Revision Id"
    },
    "depends_on": {
      "items": {
        "type": "string"
      },
      "maxItems": 256,
      "title": "Depends On",
      "type": "array"
    },
    "export": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlowExport"
        },
        {
          "type": "null"
        }
      ]
    },
    "external_step": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Step"
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "join": {
      "default": "all",
      "enum": [
        "all",
        "any",
        "quorum"
      ],
      "title": "Join",
      "type": "string"
    },
    "key": {
      "pattern": "^[a-zA-Z][a-zA-Z0-9_-]{0,63}$",
      "title": "Key",
      "type": "string"
    },
    "kind": {
      "enum": [
        "agent",
        "operation",
        "condition",
        "parallel",
        "join",
        "foreach",
        "repeat",
        "subflow",
        "wait",
        "expand"
      ],
      "title": "Kind",
      "type": "string"
    },
    "max_expanded_nodes": {
      "default": 32,
      "maximum": 256.0,
      "minimum": 1.0,
      "title": "Max Expanded Nodes",
      "type": "integer"
    },
    "max_iterations": {
      "default": 1,
      "maximum": 256.0,
      "minimum": 1.0,
      "title": "Max Iterations",
      "type": "integer"
    },
    "output_schema": {
      "additionalProperties": true,
      "title": "Output Schema",
      "type": "object"
    },
    "quorum": {
      "default": 1,
      "maximum": 256.0,
      "minimum": 1.0,
      "title": "Quorum",
      "type": "integer"
    },
    "revision_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Revision Id"
    },
    "run_if": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlowCondition"
        },
        {
          "type": "null"
        }
      ]
    },
    "signal_name": {
      "anyOf": [
        {
          "maxLength": 100,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Signal Name"
    },
    "wait_kind": {
      "default": "timer",
      "enum": [
        "timer",
        "signal",
        "approval",
        "external"
      ],
      "title": "Wait Kind",
      "type": "string"
    },
    "wait_seconds": {
      "default": 0,
      "maximum": 604800.0,
      "minimum": 0.0,
      "title": "Wait Seconds",
      "type": "integer"
    }
  },
  "required": [
    "key",
    "kind"
  ],
  "title": "FlowNode",
  "type": "object"
}
```

<a id="schema-FlowNode-Output"></a>

## FlowNode-Output

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `pattern="^[a-zA-Z][a-zA-Z0-9_-]{0,63}$"` |
| `kind` | string | 是 | `enum=["agent", "operation", "condition", "parallel", "join", "foreach", "repeat", "subflow", "wait", "expand"]` |
| `depends_on` | array&lt;string&gt; | 否 | `maxItems=256` |
| `arguments` | object | 否 | `additionalProperties=true` |
| `input_schema` | object | 否 | `additionalProperties=true` |
| `output_schema` | object | 否 | `additionalProperties=true` |
| `condition` | [FlowCondition](schemas.md#schema-FlowCondition) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/FlowCondition"}, {"type": "null"}]` |
| `run_if` | [FlowCondition](schemas.md#schema-FlowCondition) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/FlowCondition"}, {"type": "null"}]` |
| `export` | [FlowExport](schemas.md#schema-FlowExport) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/FlowExport"}, {"type": "null"}]` |
| `configuration_revision_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `revision_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `body` | [FlowGraph-Output](schemas.md#schema-FlowGraph-Output) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/FlowGraph-Output"}, {"type": "null"}]` |
| `max_iterations` | integer | 否 | `default=1`; `maximum=256.0`; `minimum=1.0` |
| `concurrency` | integer | 否 | `default=1`; `maximum=32.0`; `minimum=1.0` |
| `join` | string | 否 | `default="all"`; `enum=["all", "any", "quorum"]` |
| `quorum` | integer | 否 | `default=1`; `maximum=256.0`; `minimum=1.0` |
| `cancel_remaining` | boolean | 否 | `default=true` |
| `wait_kind` | string | 否 | `default="timer"`; `enum=["timer", "signal", "approval", "external"]` |
| `wait_seconds` | integer | 否 | `default=0`; `maximum=604800.0`; `minimum=0.0` |
| `signal_name` | string anyOf null | 否 | `anyOf=[{"maxLength": 100, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `approval_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `approver_user_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `external_step` | string anyOf null | 否 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `allowed_kinds` | array&lt;string&gt; | 否 | `maxItems=10` |
| `allowed_references` | array&lt;string&gt; | 否 | `maxItems=256` |
| `max_expanded_nodes` | integer | 否 | `default=32`; `maximum=256.0`; `minimum=1.0` |
| `approval_required` | boolean | 否 | `default=false` |

```json
{
  "additionalProperties": false,
  "properties": {
    "allowed_kinds": {
      "items": {
        "enum": [
          "agent",
          "operation",
          "condition",
          "parallel",
          "join",
          "foreach",
          "repeat",
          "subflow",
          "wait",
          "expand"
        ],
        "type": "string"
      },
      "maxItems": 10,
      "title": "Allowed Kinds",
      "type": "array"
    },
    "allowed_references": {
      "items": {
        "type": "string"
      },
      "maxItems": 256,
      "title": "Allowed References",
      "type": "array"
    },
    "approval_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approval Id"
    },
    "approval_required": {
      "default": false,
      "title": "Approval Required",
      "type": "boolean"
    },
    "approver_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approver User Id"
    },
    "arguments": {
      "additionalProperties": true,
      "title": "Arguments",
      "type": "object"
    },
    "body": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlowGraph-Output"
        },
        {
          "type": "null"
        }
      ]
    },
    "cancel_remaining": {
      "default": true,
      "title": "Cancel Remaining",
      "type": "boolean"
    },
    "concurrency": {
      "default": 1,
      "maximum": 32.0,
      "minimum": 1.0,
      "title": "Concurrency",
      "type": "integer"
    },
    "condition": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlowCondition"
        },
        {
          "type": "null"
        }
      ]
    },
    "configuration_revision_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Revision Id"
    },
    "depends_on": {
      "items": {
        "type": "string"
      },
      "maxItems": 256,
      "title": "Depends On",
      "type": "array"
    },
    "export": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlowExport"
        },
        {
          "type": "null"
        }
      ]
    },
    "external_step": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "External Step"
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "join": {
      "default": "all",
      "enum": [
        "all",
        "any",
        "quorum"
      ],
      "title": "Join",
      "type": "string"
    },
    "key": {
      "pattern": "^[a-zA-Z][a-zA-Z0-9_-]{0,63}$",
      "title": "Key",
      "type": "string"
    },
    "kind": {
      "enum": [
        "agent",
        "operation",
        "condition",
        "parallel",
        "join",
        "foreach",
        "repeat",
        "subflow",
        "wait",
        "expand"
      ],
      "title": "Kind",
      "type": "string"
    },
    "max_expanded_nodes": {
      "default": 32,
      "maximum": 256.0,
      "minimum": 1.0,
      "title": "Max Expanded Nodes",
      "type": "integer"
    },
    "max_iterations": {
      "default": 1,
      "maximum": 256.0,
      "minimum": 1.0,
      "title": "Max Iterations",
      "type": "integer"
    },
    "output_schema": {
      "additionalProperties": true,
      "title": "Output Schema",
      "type": "object"
    },
    "quorum": {
      "default": 1,
      "maximum": 256.0,
      "minimum": 1.0,
      "title": "Quorum",
      "type": "integer"
    },
    "revision_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Revision Id"
    },
    "run_if": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/FlowCondition"
        },
        {
          "type": "null"
        }
      ]
    },
    "signal_name": {
      "anyOf": [
        {
          "maxLength": 100,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Signal Name"
    },
    "wait_kind": {
      "default": "timer",
      "enum": [
        "timer",
        "signal",
        "approval",
        "external"
      ],
      "title": "Wait Kind",
      "type": "string"
    },
    "wait_seconds": {
      "default": 0,
      "maximum": 604800.0,
      "minimum": 0.0,
      "title": "Wait Seconds",
      "type": "integer"
    }
  },
  "required": [
    "key",
    "kind"
  ],
  "title": "FlowNode",
  "type": "object"
}
```

<a id="schema-FlowPlanProposal"></a>

## FlowPlanProposal

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `step_path` | string | 是 | `maxLength=500`; `minLength=1` |
| `source_invocation_id` | string (uuid) | 是 | `format="uuid"` |
| `graph` | [FlowGraph-Input](schemas.md#schema-FlowGraph-Input) | 是 | — |
| `approval_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "approval_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approval Id"
    },
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "graph": {
      "$ref": "#/components/schemas/FlowGraph-Input"
    },
    "source_invocation_id": {
      "format": "uuid",
      "title": "Source Invocation Id",
      "type": "string"
    },
    "step_path": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Step Path",
      "type": "string"
    }
  },
  "required": [
    "command_id",
    "expected_revision",
    "step_path",
    "source_invocation_id",
    "graph"
  ],
  "title": "FlowPlanProposal",
  "type": "object"
}
```

<a id="schema-FlowPlanReview"></a>

## FlowPlanReview

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `step_path` | string | 是 | `maxLength=500`; `minLength=1` |
| `source_invocation_id` | string (uuid) | 是 | `format="uuid"` |
| `graph` | [FlowGraph-Input](schemas.md#schema-FlowGraph-Input) | 是 | — |
| `approval_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `approver_user_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "approval_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approval Id"
    },
    "approver_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Approver User Id"
    },
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "graph": {
      "$ref": "#/components/schemas/FlowGraph-Input"
    },
    "source_invocation_id": {
      "format": "uuid",
      "title": "Source Invocation Id",
      "type": "string"
    },
    "step_path": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Step Path",
      "type": "string"
    }
  },
  "required": [
    "command_id",
    "expected_revision",
    "step_path",
    "source_invocation_id",
    "graph"
  ],
  "title": "FlowPlanReview",
  "type": "object"
}
```

<a id="schema-FlowPlanReviewView"></a>

## FlowPlanReviewView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `approval_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "approval_id": {
      "format": "uuid",
      "title": "Approval Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "approval_id",
    "status"
  ],
  "title": "FlowPlanReviewView",
  "type": "object"
}
```

<a id="schema-FlowPublishRequest"></a>

## FlowPublishRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 1.0,
      "title": "Expected Revision",
      "type": "integer"
    }
  },
  "required": [
    "expected_revision"
  ],
  "title": "FlowPublishRequest",
  "type": "object"
}
```

<a id="schema-FlowRunCreate"></a>

## FlowRunCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `revision_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `input` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "input": {
      "additionalProperties": true,
      "title": "Input",
      "type": "object"
    },
    "revision_id": {
      "format": "uuid",
      "title": "Revision Id",
      "type": "string"
    }
  },
  "required": [
    "revision_id"
  ],
  "title": "FlowRunCreate",
  "type": "object"
}
```

<a id="schema-FlowRunView"></a>

## FlowRunView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `revision_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `configuration_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `initiated_by_id` | string (uuid) | 是 | `format="uuid"` |
| `parent_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `root_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |
| `output` | any | 否 | — |
| `error_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "initiated_by_id": {
      "format": "uuid",
      "title": "Initiated By Id",
      "type": "string"
    },
    "output": {
      "title": "Output"
    },
    "parent_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Parent Id"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "revision_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Revision Id"
    },
    "root_id": {
      "format": "uuid",
      "title": "Root Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "revision_id",
    "configuration_id",
    "initiated_by_id",
    "parent_id",
    "root_id",
    "status",
    "revision",
    "error_code"
  ],
  "title": "FlowRunView",
  "type": "object"
}
```

<a id="schema-FlowSignal"></a>

## FlowSignal

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `step_path` | string | 是 | `maxLength=500`; `minLength=1` |
| `generation` | integer | 是 | `maximum=256.0`; `minimum=1.0` |
| `name` | string | 是 | `maxLength=100`; `minLength=1` |
| `value` | any | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "generation": {
      "maximum": 256.0,
      "minimum": 1.0,
      "title": "Generation",
      "type": "integer"
    },
    "name": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "step_path": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Step Path",
      "type": "string"
    },
    "value": {
      "title": "Value"
    }
  },
  "required": [
    "command_id",
    "step_path",
    "generation",
    "name"
  ],
  "title": "FlowSignal",
  "type": "object"
}
```

<a id="schema-FlowStepView"></a>

## FlowStepView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `path` | string | 是 | — |
| `generation` | integer | 是 | — |
| `status` | string | 是 | — |
| `child_kind` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `child_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `error_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "child_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Child Id"
    },
    "child_kind": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Child Kind"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "generation": {
      "title": "Generation",
      "type": "integer"
    },
    "path": {
      "title": "Path",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "path",
    "generation",
    "status",
    "child_kind",
    "child_id",
    "error_code"
  ],
  "title": "FlowStepView",
  "type": "object"
}
```

<a id="schema-FlowValidationResult"></a>

## FlowValidationResult

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `valid` | boolean | 是 | — |
| `revision` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "valid": {
      "title": "Valid",
      "type": "boolean"
    }
  },
  "required": [
    "valid",
    "revision"
  ],
  "title": "FlowValidationResult",
  "type": "object"
}
```

<a id="schema-GrantCreateRequest"></a>

## GrantCreateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `grantee_user_id` | string (uuid) | 是 | `format="uuid"` |
| `permissions` | array&lt;string&gt; | 是 | `maxItems=8`; `minItems=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "grantee_user_id": {
      "format": "uuid",
      "title": "Grantee User Id",
      "type": "string"
    },
    "permissions": {
      "items": {
        "enum": [
          "read",
          "write",
          "delete",
          "use_in_execution"
        ],
        "type": "string"
      },
      "maxItems": 8,
      "minItems": 1,
      "title": "Permissions",
      "type": "array"
    }
  },
  "required": [
    "grantee_user_id",
    "permissions"
  ],
  "title": "GrantCreateRequest",
  "type": "object"
}
```

<a id="schema-GrantView"></a>

## GrantView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `grant_id` | string (uuid) | 是 | `format="uuid"` |
| `grantee_user_id` | string (uuid) | 是 | `format="uuid"` |
| `permissions` | array&lt;string&gt; | 是 | — |
| `created_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "grant_id": {
      "format": "uuid",
      "title": "Grant Id",
      "type": "string"
    },
    "grantee_user_id": {
      "format": "uuid",
      "title": "Grantee User Id",
      "type": "string"
    },
    "permissions": {
      "items": {
        "type": "string"
      },
      "title": "Permissions",
      "type": "array"
    }
  },
  "required": [
    "grant_id",
    "grantee_user_id",
    "permissions",
    "created_at"
  ],
  "title": "GrantView",
  "type": "object"
}
```

<a id="schema-HealthView"></a>

## HealthView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `status` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status"
  ],
  "title": "HealthView",
  "type": "object"
}
```

<a id="schema-HistoryAppendRequest"></a>

## HistoryAppendRequest

The items one append stores, in order.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `items` | array&lt;object&gt; | 是 | `maxItems=100`; `minItems=1` |

```json
{
  "additionalProperties": false,
  "description": "The items one append stores, in order.",
  "properties": {
    "items": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 100,
      "minItems": 1,
      "title": "Items",
      "type": "array"
    }
  },
  "required": [
    "items"
  ],
  "title": "HistoryAppendRequest",
  "type": "object"
}
```

<a id="schema-HistoryAppendView"></a>

## HistoryAppendView

How many items the append stored.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `appended` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "How many items the append stored.",
  "properties": {
    "appended": {
      "title": "Appended",
      "type": "integer"
    }
  },
  "required": [
    "appended"
  ],
  "title": "HistoryAppendView",
  "type": "object"
}
```

<a id="schema-HistoryItemView"></a>

## HistoryItemView

The item one pop removed, or nothing when the history was empty.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `item` | object anyOf null | 是 | `anyOf=[{"additionalProperties": true, "type": "object"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The item one pop removed, or nothing when the history was empty.",
  "properties": {
    "item": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Item"
    }
  },
  "required": [
    "item"
  ],
  "title": "HistoryItemView",
  "type": "object"
}
```

<a id="schema-HistoryPage"></a>

## HistoryPage

One bounded read of the native history, oldest first.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `items` | array&lt;object&gt; | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "One bounded read of the native history, oldest first.",
  "properties": {
    "items": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Items",
      "type": "array"
    }
  },
  "required": [
    "items"
  ],
  "title": "HistoryPage",
  "type": "object"
}
```

<a id="schema-HookBudget"></a>

## HookBudget

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `max_invocations` | integer | 否 | `default=128`; `maximum=1024.0`; `minimum=1.0` |
| `timeout_seconds` | integer | 否 | `default=30`; `maximum=120.0`; `minimum=1.0` |
| `model_tokens` | integer | 否 | `default=0`; `maximum=262144.0`; `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "max_invocations": {
      "default": 128,
      "maximum": 1024.0,
      "minimum": 1.0,
      "title": "Max Invocations",
      "type": "integer"
    },
    "model_tokens": {
      "default": 0,
      "maximum": 262144.0,
      "minimum": 0.0,
      "title": "Model Tokens",
      "type": "integer"
    },
    "timeout_seconds": {
      "default": 30,
      "maximum": 120.0,
      "minimum": 1.0,
      "title": "Timeout Seconds",
      "type": "integer"
    }
  },
  "title": "HookBudget",
  "type": "object"
}
```

<a id="schema-HostCallView"></a>

## HostCallView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `operation_id` | string | 是 | — |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "operation_id": {
      "title": "Operation Id",
      "type": "string"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "operation_id",
    "status",
    "revision"
  ],
  "title": "HostCallView",
  "type": "object"
}
```

<a id="schema-InstallRequest"></a>

## InstallRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `operation_id` | string | 是 | `maxLength=200`; `minLength=1` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `permission_ceiling` | array&lt;string&gt; | 否 | `maxItems=128` |

```json
{
  "additionalProperties": false,
  "properties": {
    "operation_id": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Operation Id",
      "type": "string"
    },
    "permission_ceiling": {
      "items": {
        "type": "string"
      },
      "maxItems": 128,
      "title": "Permission Ceiling",
      "type": "array"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "operation_id",
    "version_id"
  ],
  "title": "InstallRequest",
  "type": "object"
}
```

<a id="schema-InstallationView"></a>

## InstallationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `namespace` | string | 是 | — |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |
| `artifact` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `permission_ceiling` | array&lt;string&gt; | 是 | — |
| `error_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "artifact": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Artifact"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "namespace": {
      "title": "Namespace",
      "type": "string"
    },
    "permission_ceiling": {
      "items": {
        "type": "string"
      },
      "title": "Permission Ceiling",
      "type": "array"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "id",
    "version_id",
    "namespace",
    "status",
    "revision",
    "artifact",
    "permission_ceiling",
    "error_code"
  ],
  "title": "InstallationView",
  "type": "object"
}
```

<a id="schema-InstalledExport"></a>

## InstalledExport

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `instance_id` | string (uuid) | 是 | `format="uuid"` |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_revision` | integer | 是 | `minimum=0.0` |
| `artifact` | string | 是 | `pattern="^sha256:[a-f0-9]{64}$"` |
| `export_id` | string | 是 | `maxLength=100`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "artifact": {
      "pattern": "^sha256:[a-f0-9]{64}$",
      "title": "Artifact",
      "type": "string"
    },
    "configuration_revision": {
      "minimum": 0.0,
      "title": "Configuration Revision",
      "type": "integer"
    },
    "export_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Export Id",
      "type": "string"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "instance_id": {
      "format": "uuid",
      "title": "Instance Id",
      "type": "string"
    }
  },
  "required": [
    "instance_id",
    "installation_id",
    "configuration_revision",
    "artifact",
    "export_id"
  ],
  "title": "InstalledExport",
  "type": "object"
}
```

<a id="schema-InstalledHook"></a>

## InstalledHook

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `instance_id` | string (uuid) | 是 | `format="uuid"` |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_revision` | integer | 是 | `minimum=0.0` |
| `artifact` | string | 是 | `pattern="^sha256:[a-f0-9]{64}$"` |
| `export_id` | string | 是 | `maxLength=100`; `minLength=1` |
| `binding_id` | string | 是 | `maxLength=128`; `minLength=1` |
| `order` | integer | 否 | `default=0`; `minimum=0.0` |
| `stages` | array&lt;string&gt; | 是 | `maxItems=9`; `minItems=1` |
| `failure_policy` | string | 否 | `default="block"`; `enum=["block", "observe"]` |
| `budget` | [HookBudget](schemas.md#schema-HookBudget) | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "artifact": {
      "pattern": "^sha256:[a-f0-9]{64}$",
      "title": "Artifact",
      "type": "string"
    },
    "binding_id": {
      "maxLength": 128,
      "minLength": 1,
      "title": "Binding Id",
      "type": "string"
    },
    "budget": {
      "$ref": "#/components/schemas/HookBudget"
    },
    "configuration_revision": {
      "minimum": 0.0,
      "title": "Configuration Revision",
      "type": "integer"
    },
    "export_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Export Id",
      "type": "string"
    },
    "failure_policy": {
      "default": "block",
      "enum": [
        "block",
        "observe"
      ],
      "title": "Failure Policy",
      "type": "string"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "instance_id": {
      "format": "uuid",
      "title": "Instance Id",
      "type": "string"
    },
    "order": {
      "default": 0,
      "minimum": 0.0,
      "title": "Order",
      "type": "integer"
    },
    "stages": {
      "items": {
        "type": "string"
      },
      "maxItems": 9,
      "minItems": 1,
      "title": "Stages",
      "type": "array"
    }
  },
  "required": [
    "instance_id",
    "installation_id",
    "configuration_revision",
    "artifact",
    "export_id",
    "binding_id",
    "stages"
  ],
  "title": "InstalledHook",
  "type": "object"
}
```

<a id="schema-InstanceConfiguration"></a>

## InstanceConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `environment_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `configuration` | object | 否 | `additionalProperties=true` |
| `permissions` | array&lt;string&gt; | 否 | `maxItems=128` |
| `model_connections` | array&lt;string&gt; | 否 | `maxItems=64` |
| `state_limit_bytes` | integer | 否 | `default=10485760`; `maximum=1073741824.0`; `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "additionalProperties": true,
      "title": "Configuration",
      "type": "object"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "environment_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment Id"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "model_connections": {
      "items": {
        "type": "string"
      },
      "maxItems": 64,
      "title": "Model Connections",
      "type": "array"
    },
    "permissions": {
      "items": {
        "type": "string"
      },
      "maxItems": 128,
      "title": "Permissions",
      "type": "array"
    },
    "state_limit_bytes": {
      "default": 10485760,
      "maximum": 1073741824.0,
      "minimum": 0.0,
      "title": "State Limit Bytes",
      "type": "integer"
    }
  },
  "required": [
    "installation_id"
  ],
  "title": "InstanceConfiguration",
  "type": "object"
}
```

<a id="schema-InstanceUpdate"></a>

## InstanceUpdate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `environment_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `configuration` | object | 否 | `additionalProperties=true` |
| `permissions` | array&lt;string&gt; | 否 | `maxItems=128` |
| `model_connections` | array&lt;string&gt; | 否 | `maxItems=64` |
| `state_limit_bytes` | integer | 否 | `default=10485760`; `maximum=1073741824.0`; `minimum=0.0` |
| `expected_revision` | integer | 是 | `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "additionalProperties": true,
      "title": "Configuration",
      "type": "object"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "environment_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment Id"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "model_connections": {
      "items": {
        "type": "string"
      },
      "maxItems": 64,
      "title": "Model Connections",
      "type": "array"
    },
    "permissions": {
      "items": {
        "type": "string"
      },
      "maxItems": 128,
      "title": "Permissions",
      "type": "array"
    },
    "state_limit_bytes": {
      "default": 10485760,
      "maximum": 1073741824.0,
      "minimum": 0.0,
      "title": "State Limit Bytes",
      "type": "integer"
    }
  },
  "required": [
    "installation_id",
    "expected_revision"
  ],
  "title": "InstanceUpdate",
  "type": "object"
}
```

<a id="schema-InstanceView"></a>

## InstanceView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `environment_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `configuration` | object | 否 | `additionalProperties=true` |
| `permissions` | array&lt;string&gt; | 否 | `maxItems=128` |
| `model_connections` | array&lt;string&gt; | 否 | `maxItems=64` |
| `state_limit_bytes` | integer | 否 | `default=10485760`; `maximum=1073741824.0`; `minimum=0.0` |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `owner_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "additionalProperties": true,
      "title": "Configuration",
      "type": "object"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "environment_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment Id"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "model_connections": {
      "items": {
        "type": "string"
      },
      "maxItems": 64,
      "title": "Model Connections",
      "type": "array"
    },
    "owner_id": {
      "format": "uuid",
      "title": "Owner Id",
      "type": "string"
    },
    "permissions": {
      "items": {
        "type": "string"
      },
      "maxItems": 128,
      "title": "Permissions",
      "type": "array"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "state_limit_bytes": {
      "default": 10485760,
      "maximum": 1073741824.0,
      "minimum": 0.0,
      "title": "State Limit Bytes",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "installation_id",
    "id",
    "owner_id",
    "status",
    "revision"
  ],
  "title": "InstanceView",
  "type": "object"
}
```

<a id="schema-InvitationIssueView"></a>

## InvitationIssueView

The newly issued token is returned only once to its administrator.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `invitation_id` | string (uuid) | 是 | `format="uuid"` |
| `email` | string (email) | 是 | `format="email"` |
| `status` | string | 是 | — |
| `expires_at` | string (date-time) | 是 | `format="date-time"` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `accepted_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `token_revision` | integer | 是 | — |
| `token_issued_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `invited_by_user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `token` | string (uuid) | 是 | `format="uuid"` |
| `acceptance_url` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The newly issued token is returned only once to its administrator.",
  "properties": {
    "acceptance_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Acceptance Url"
    },
    "accepted_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Accepted At"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "email": {
      "format": "email",
      "title": "Email",
      "type": "string"
    },
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "invitation_id": {
      "format": "uuid",
      "title": "Invitation Id",
      "type": "string"
    },
    "invited_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Invited By User Id"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "token": {
      "format": "uuid",
      "title": "Token",
      "type": "string"
    },
    "token_issued_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Token Issued At"
    },
    "token_revision": {
      "title": "Token Revision",
      "type": "integer"
    }
  },
  "required": [
    "invitation_id",
    "email",
    "status",
    "expires_at",
    "created_at",
    "accepted_at",
    "token_revision",
    "token_issued_at",
    "invited_by_user_id",
    "token",
    "acceptance_url"
  ],
  "title": "InvitationIssueView",
  "type": "object"
}
```

<a id="schema-InvitationPage"></a>

## InvitationPage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `invitations` | array&lt;[InvitationView](schemas.md#schema-InvitationView)&gt; | 是 | — |
| `total` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "invitations": {
      "items": {
        "$ref": "#/components/schemas/InvitationView"
      },
      "title": "Invitations",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "invitations",
    "total"
  ],
  "title": "InvitationPage",
  "type": "object"
}
```

<a id="schema-InvitationView"></a>

## InvitationView

One invitation; the presented status derives expiry, never rewrites it.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `invitation_id` | string (uuid) | 是 | `format="uuid"` |
| `email` | string (email) | 是 | `format="email"` |
| `status` | string | 是 | — |
| `expires_at` | string (date-time) | 是 | `format="date-time"` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `accepted_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `token_revision` | integer | 是 | — |
| `token_issued_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `invited_by_user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "One invitation; the presented status derives expiry, never rewrites it.",
  "properties": {
    "accepted_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Accepted At"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "email": {
      "format": "email",
      "title": "Email",
      "type": "string"
    },
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "invitation_id": {
      "format": "uuid",
      "title": "Invitation Id",
      "type": "string"
    },
    "invited_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Invited By User Id"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "token_issued_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Token Issued At"
    },
    "token_revision": {
      "title": "Token Revision",
      "type": "integer"
    }
  },
  "required": [
    "invitation_id",
    "email",
    "status",
    "expires_at",
    "created_at",
    "accepted_at",
    "token_revision",
    "token_issued_at",
    "invited_by_user_id"
  ],
  "title": "InvitationView",
  "type": "object"
}
```

<a id="schema-InvocationView"></a>

## InvocationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `instance_id` | string (uuid) | 是 | `format="uuid"` |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `namespace_id` | string (uuid) | 是 | `format="uuid"` |
| `export_id` | string | 是 | — |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |
| `effects` | array&lt;string&gt; | 是 | — |
| `result` | any | 否 | — |
| `error_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `cancel_requested` | boolean | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "cancel_requested": {
      "title": "Cancel Requested",
      "type": "boolean"
    },
    "effects": {
      "items": {
        "type": "string"
      },
      "title": "Effects",
      "type": "array"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "export_id": {
      "title": "Export Id",
      "type": "string"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "instance_id": {
      "format": "uuid",
      "title": "Instance Id",
      "type": "string"
    },
    "namespace_id": {
      "format": "uuid",
      "title": "Namespace Id",
      "type": "string"
    },
    "result": {
      "title": "Result"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "instance_id",
    "installation_id",
    "namespace_id",
    "export_id",
    "status",
    "revision",
    "effects",
    "error_code",
    "cancel_requested"
  ],
  "title": "InvocationView",
  "type": "object"
}
```

<a id="schema-InvokeRequest"></a>

## InvokeRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `export_id` | string | 是 | `maxLength=100`; `minLength=1` |
| `operation_id` | string | 是 | `maxLength=240`; `minLength=1` |
| `arguments` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "arguments": {
      "additionalProperties": true,
      "title": "Arguments",
      "type": "object"
    },
    "export_id": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Export Id",
      "type": "string"
    },
    "operation_id": {
      "maxLength": 240,
      "minLength": 1,
      "title": "Operation Id",
      "type": "string"
    }
  },
  "required": [
    "export_id",
    "operation_id"
  ],
  "title": "InvokeRequest",
  "type": "object"
}
```

<a id="schema-LoginRequest"></a>

## LoginRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `email` | string (email) | 是 | `format="email"`; `maxLength=320` |
| `password` | string | 是 | `maxLength=256`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "email": {
      "format": "email",
      "maxLength": 320,
      "title": "Email",
      "type": "string"
    },
    "password": {
      "maxLength": 256,
      "minLength": 1,
      "title": "Password",
      "type": "string"
    }
  },
  "required": [
    "email",
    "password"
  ],
  "title": "LoginRequest",
  "type": "object"
}
```

<a id="schema-MCPBinding"></a>

## MCPBinding

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | 是 | `format="uuid"` |
| `tools_digest` | string | 是 | `pattern="^[a-f0-9]{64}$"` |
| `allowed_tools` | array&lt;string&gt; | 是 | `maxItems=100`; `minItems=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "allowed_tools": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "minItems": 1,
      "title": "Allowed Tools",
      "type": "array"
    },
    "connection_id": {
      "format": "uuid",
      "title": "Connection Id",
      "type": "string"
    },
    "tools_digest": {
      "pattern": "^[a-f0-9]{64}$",
      "title": "Tools Digest",
      "type": "string"
    }
  },
  "required": [
    "connection_id",
    "tools_digest",
    "allowed_tools"
  ],
  "title": "MCPBinding",
  "type": "object"
}
```

<a id="schema-MCPCreateRequest"></a>

## MCPCreateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `name` | string | 是 | `maxLength=100`; `minLength=1` |
| `transport` | string | 是 | `enum=["streamable_http", "sse", "stdio"]` |
| `endpoint` | string anyOf null | 否 | `anyOf=[{"maxLength": 2000, "type": "string"}, {"type": "null"}]` |
| `environment_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `command` | array&lt;string&gt; | 否 | `maxItems=100` |
| `environment` | object | 否 | `additionalProperties={"type": "string"}`; `maxProperties=64` |

```json
{
  "additionalProperties": false,
  "properties": {
    "command": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Command",
      "type": "array"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "endpoint": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Endpoint"
    },
    "environment": {
      "additionalProperties": {
        "type": "string"
      },
      "maxProperties": 64,
      "title": "Environment",
      "type": "object"
    },
    "environment_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Environment Id"
    },
    "name": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "transport": {
      "enum": [
        "streamable_http",
        "sse",
        "stdio"
      ],
      "title": "Transport",
      "type": "string"
    }
  },
  "required": [
    "name",
    "transport"
  ],
  "title": "MCPCreateRequest",
  "type": "object"
}
```

<a id="schema-MCPToolsView"></a>

## MCPToolsView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | 是 | `format="uuid"` |
| `tools` | array&lt;object&gt; | 是 | — |
| `tools_digest` | string | 是 | — |

```json
{
  "properties": {
    "connection_id": {
      "format": "uuid",
      "title": "Connection Id",
      "type": "string"
    },
    "tools": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Tools",
      "type": "array"
    },
    "tools_digest": {
      "title": "Tools Digest",
      "type": "string"
    }
  },
  "required": [
    "connection_id",
    "tools",
    "tools_digest"
  ],
  "title": "MCPToolsView",
  "type": "object"
}
```

<a id="schema-MCPView"></a>

## MCPView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | 是 | `format="uuid"` |
| `name` | string | 是 | — |
| `transport` | string | 是 | — |
| `status` | string | 是 | — |
| `tools` | array&lt;object&gt; | 是 | — |
| `tools_digest` | string | 否 | `default=""` |
| `error_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "properties": {
    "connection_id": {
      "format": "uuid",
      "title": "Connection Id",
      "type": "string"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "tools": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Tools",
      "type": "array"
    },
    "tools_digest": {
      "default": "",
      "title": "Tools Digest",
      "type": "string"
    },
    "transport": {
      "title": "Transport",
      "type": "string"
    }
  },
  "required": [
    "connection_id",
    "name",
    "transport",
    "status",
    "tools",
    "error_code"
  ],
  "title": "MCPView",
  "type": "object"
}
```

<a id="schema-McpConfiguration"></a>

## McpConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `transport` | string | 是 | `enum=["remote", "stdio"]` |
| `endpoint` | string anyOf null | 否 | `anyOf=[{"maxLength": 2000, "type": "string"}, {"type": "null"}]` |
| `command` | array&lt;string&gt; anyOf null | 否 | `anyOf=[{"items": {"type": "string"}, "maxItems": 64, "type": "array"}, {"type": "null"}]` |
| `arguments` | array&lt;string&gt; | 否 | `maxItems=256` |
| `environment` | object | 否 | `additionalProperties={"type": "string"}` |
| `tools` | array&lt;[McpToolDeclaration](schemas.md#schema-McpToolDeclaration)&gt; | 否 | `maxItems=256` |
| `parameters_schema` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "arguments": {
      "items": {
        "type": "string"
      },
      "maxItems": 256,
      "title": "Arguments",
      "type": "array"
    },
    "command": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 64,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Command"
    },
    "endpoint": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Endpoint"
    },
    "environment": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Environment",
      "type": "object"
    },
    "parameters_schema": {
      "additionalProperties": true,
      "title": "Parameters Schema",
      "type": "object"
    },
    "tools": {
      "items": {
        "$ref": "#/components/schemas/McpToolDeclaration"
      },
      "maxItems": 256,
      "title": "Tools",
      "type": "array"
    },
    "transport": {
      "enum": [
        "remote",
        "stdio"
      ],
      "title": "Transport",
      "type": "string"
    }
  },
  "required": [
    "transport"
  ],
  "title": "McpConfiguration",
  "type": "object"
}
```

<a id="schema-McpDeclaration"></a>

## McpDeclaration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `type` | string | 是 | `const="mcp"` |
| `configuration` | [McpConfiguration](schemas.md#schema-McpConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/McpConfiguration"
    },
    "type": {
      "const": "mcp",
      "title": "Type",
      "type": "string"
    }
  },
  "required": [
    "type",
    "configuration"
  ],
  "title": "McpDeclaration",
  "type": "object"
}
```

<a id="schema-McpToolDeclaration"></a>

## McpToolDeclaration

One advertised tool with its parameter schema; discovery is runtime.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `maxLength=100`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `parameters_schema` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "description": "One advertised tool with its parameter schema; discovery is runtime.",
  "properties": {
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
    },
    "name": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "parameters_schema": {
      "additionalProperties": true,
      "title": "Parameters Schema",
      "type": "object"
    }
  },
  "required": [
    "name"
  ],
  "title": "McpToolDeclaration",
  "type": "object"
}
```

<a id="schema-ModelConfiguration"></a>

## ModelConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | 是 | `format="uuid"` |
| `name` | string | 是 | `maxLength=200`; `minLength=1` |
| `max_output_tokens` | integer | 否 | `default=4096`; `maximum=131072.0`; `minimum=1.0` |
| `temperature` | number anyOf null | 否 | `anyOf=[{"maximum": 2.0, "minimum": 0.0, "type": "number"}, {"type": "null"}]` |
| `reasoning_effort` | string anyOf null | 否 | `anyOf=[{"enum": ["none", "minimal", "low", "medium", "high", "xhigh", "max"], "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "connection_id": {
      "format": "uuid",
      "title": "Connection Id",
      "type": "string"
    },
    "max_output_tokens": {
      "default": 4096,
      "maximum": 131072.0,
      "minimum": 1.0,
      "title": "Max Output Tokens",
      "type": "integer"
    },
    "name": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "reasoning_effort": {
      "anyOf": [
        {
          "enum": [
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reasoning Effort"
    },
    "temperature": {
      "anyOf": [
        {
          "maximum": 2.0,
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Temperature"
    }
  },
  "required": [
    "connection_id",
    "name"
  ],
  "title": "ModelConfiguration",
  "type": "object"
}
```

<a id="schema-ModelConnectionCreateRequest"></a>

## ModelConnectionCreateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `maxLength=120`; `minLength=1` |
| `api_key` | string (password) | 是 | `format="password"`; `maxLength=16384`; `minLength=1`; `writeOnly=true` |
| `base_url` | string | 否 | `default="https://api.openai.com/v1"`; `maxLength=500` |
| `default_model` | string | 是 | `maxLength=200`; `minLength=1` |
| `models` | array&lt;string&gt; | 是 | `maxItems=256`; `minItems=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "api_key": {
      "format": "password",
      "maxLength": 16384,
      "minLength": 1,
      "title": "Api Key",
      "type": "string",
      "writeOnly": true
    },
    "base_url": {
      "default": "https://api.openai.com/v1",
      "maxLength": 500,
      "title": "Base Url",
      "type": "string"
    },
    "default_model": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Default Model",
      "type": "string"
    },
    "models": {
      "items": {
        "type": "string"
      },
      "maxItems": 256,
      "minItems": 1,
      "title": "Models",
      "type": "array"
    },
    "name": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    }
  },
  "required": [
    "name",
    "api_key",
    "default_model",
    "models"
  ],
  "title": "ModelConnectionCreateRequest",
  "type": "object"
}
```

<a id="schema-ModelConnectionPage"></a>

## ModelConnectionPage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `connections` | array&lt;[ModelConnectionView](schemas.md#schema-ModelConnectionView)&gt; | 是 | — |
| `total` | integer | 是 | — |

```json
{
  "properties": {
    "connections": {
      "items": {
        "$ref": "#/components/schemas/ModelConnectionView"
      },
      "title": "Connections",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "connections",
    "total"
  ],
  "title": "ModelConnectionPage",
  "type": "object"
}
```

<a id="schema-ModelConnectionUpdateRequest"></a>

## ModelConnectionUpdateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=1.0` |
| `name` | string anyOf null | 否 | `anyOf=[{"maxLength": 120, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `api_key` | string (password) anyOf null | 否 | `anyOf=[{"format": "password", "maxLength": 16384, "minLength": 1, "type": "string", "writeOnly": true}, {"type": "null"}]` |
| `base_url` | string anyOf null | 否 | `anyOf=[{"maxLength": 500, "type": "string"}, {"type": "null"}]` |
| `default_model` | string anyOf null | 否 | `anyOf=[{"maxLength": 200, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `models` | array&lt;string&gt; anyOf null | 否 | `anyOf=[{"items": {"type": "string"}, "maxItems": 256, "minItems": 1, "type": "array"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "api_key": {
      "anyOf": [
        {
          "format": "password",
          "maxLength": 16384,
          "minLength": 1,
          "type": "string",
          "writeOnly": true
        },
        {
          "type": "null"
        }
      ],
      "title": "Api Key"
    },
    "base_url": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Base Url"
    },
    "default_model": {
      "anyOf": [
        {
          "maxLength": 200,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Default Model"
    },
    "expected_revision": {
      "minimum": 1.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "models": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "maxItems": 256,
          "minItems": 1,
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Models"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 120,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    }
  },
  "required": [
    "expected_revision"
  ],
  "title": "ModelConnectionUpdateRequest",
  "type": "object"
}
```

<a id="schema-ModelConnectionView"></a>

## ModelConnectionView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | 是 | `format="uuid"` |
| `revision` | integer | 是 | — |
| `name` | string | 是 | — |
| `provider` | string | 是 | — |
| `base_url` | string | 是 | — |
| `default_model` | string | 是 | — |
| `models` | array&lt;string&gt; | 是 | — |
| `status` | string | 是 | — |
| `last_probe_status` | string | 是 | — |
| `last_probe_detail` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `last_probe_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "properties": {
    "base_url": {
      "title": "Base Url",
      "type": "string"
    },
    "connection_id": {
      "format": "uuid",
      "title": "Connection Id",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "default_model": {
      "title": "Default Model",
      "type": "string"
    },
    "last_probe_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Probe At"
    },
    "last_probe_detail": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Probe Detail"
    },
    "last_probe_status": {
      "title": "Last Probe Status",
      "type": "string"
    },
    "models": {
      "items": {
        "type": "string"
      },
      "title": "Models",
      "type": "array"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "provider": {
      "title": "Provider",
      "type": "string"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "connection_id",
    "revision",
    "name",
    "provider",
    "base_url",
    "default_model",
    "models",
    "status",
    "last_probe_status",
    "last_probe_detail",
    "last_probe_at",
    "created_at",
    "updated_at"
  ],
  "title": "ModelConnectionView",
  "type": "object"
}
```

<a id="schema-ModelGrantRequest"></a>

## ModelGrantRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `models` | array&lt;string&gt; | 是 | `maxItems=256`; `minItems=1` |
| `is_default` | boolean | 否 | `default=false` |

```json
{
  "additionalProperties": false,
  "properties": {
    "is_default": {
      "default": false,
      "title": "Is Default",
      "type": "boolean"
    },
    "models": {
      "items": {
        "type": "string"
      },
      "maxItems": 256,
      "minItems": 1,
      "title": "Models",
      "type": "array"
    }
  },
  "required": [
    "models"
  ],
  "title": "ModelGrantRequest",
  "type": "object"
}
```

<a id="schema-ModelGrantView"></a>

## ModelGrantView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `connection_id` | string (uuid) | 是 | `format="uuid"` |
| `user_id` | string (uuid) | 是 | `format="uuid"` |
| `models` | array&lt;string&gt; | 是 | — |
| `is_default` | boolean | 是 | — |

```json
{
  "properties": {
    "connection_id": {
      "format": "uuid",
      "title": "Connection Id",
      "type": "string"
    },
    "is_default": {
      "title": "Is Default",
      "type": "boolean"
    },
    "models": {
      "items": {
        "type": "string"
      },
      "title": "Models",
      "type": "array"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "connection_id",
    "user_id",
    "models",
    "is_default"
  ],
  "title": "ModelGrantView",
  "type": "object"
}
```

<a id="schema-ModelOperationReconciliation"></a>

## ModelOperationReconciliation

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `outcome` | string | 是 | `enum=["succeeded", "not_executed"]` |
| `actual_usage` | [NormalizedUsage](schemas.md#schema-NormalizedUsage) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/NormalizedUsage"}, {"type": "null"}]` |
| `result` | object | 否 | `additionalProperties=true` |
| `evidence` | object | 是 | `additionalProperties=true`; `minProperties=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "actual_usage": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/NormalizedUsage"
        },
        {
          "type": "null"
        }
      ]
    },
    "evidence": {
      "additionalProperties": true,
      "minProperties": 1,
      "title": "Evidence",
      "type": "object"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "outcome": {
      "enum": [
        "succeeded",
        "not_executed"
      ],
      "title": "Outcome",
      "type": "string"
    },
    "result": {
      "additionalProperties": true,
      "title": "Result",
      "type": "object"
    }
  },
  "required": [
    "expected_revision",
    "outcome",
    "evidence"
  ],
  "title": "ModelOperationReconciliation",
  "type": "object"
}
```

<a id="schema-ModelOperationRequest"></a>

## ModelOperationRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `connection_id` | string (uuid) | 是 | `format="uuid"` |
| `model` | string | 是 | `maxLength=200`; `minLength=1` |
| `kind` | string | 是 | `enum=["complete", "embed"]` |
| `inputs` | array&lt;string&gt; | 是 | `maxItems=512`; `minItems=1` |
| `max_output_tokens` | integer | 否 | `default=4096`; `maximum=131072.0`; `minimum=1.0` |
| `temperature` | number anyOf null | 否 | `anyOf=[{"maximum": 2.0, "minimum": 0.0, "type": "number"}, {"type": "null"}]` |
| `reasoning_effort` | string anyOf null | 否 | `anyOf=[{"enum": ["none", "minimal", "low", "medium", "high", "xhigh", "max"], "type": "string"}, {"type": "null"}]` |
| `source_ref` | string (uuid) | 是 | `format="uuid"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "connection_id": {
      "format": "uuid",
      "title": "Connection Id",
      "type": "string"
    },
    "inputs": {
      "items": {
        "type": "string"
      },
      "maxItems": 512,
      "minItems": 1,
      "title": "Inputs",
      "type": "array"
    },
    "kind": {
      "enum": [
        "complete",
        "embed"
      ],
      "title": "Kind",
      "type": "string"
    },
    "max_output_tokens": {
      "default": 4096,
      "maximum": 131072.0,
      "minimum": 1.0,
      "title": "Max Output Tokens",
      "type": "integer"
    },
    "model": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Model",
      "type": "string"
    },
    "reasoning_effort": {
      "anyOf": [
        {
          "enum": [
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reasoning Effort"
    },
    "source_ref": {
      "format": "uuid",
      "title": "Source Ref",
      "type": "string"
    },
    "temperature": {
      "anyOf": [
        {
          "maximum": 2.0,
          "minimum": 0.0,
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Temperature"
    }
  },
  "required": [
    "connection_id",
    "model",
    "kind",
    "inputs",
    "source_ref"
  ],
  "title": "ModelOperationRequest",
  "type": "object"
}
```

<a id="schema-ModelOperationView"></a>

## ModelOperationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |
| `revision` | integer | 是 | — |
| `result` | object anyOf null | 否 | `anyOf=[{"additionalProperties": true, "type": "object"}, {"type": "null"}]` |
| `usage` | object | 是 | `additionalProperties=true` |

```json
{
  "properties": {
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "result": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Result"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "usage": {
      "additionalProperties": true,
      "title": "Usage",
      "type": "object"
    }
  },
  "required": [
    "id",
    "status",
    "revision",
    "usage"
  ],
  "title": "ModelOperationView",
  "type": "object"
}
```

<a id="schema-NormalizedUsage"></a>

## NormalizedUsage

Quantity-only operation usage; input tokens exclude the cached bucket.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `input_tokens` | integer | 否 | `default=0`; `minimum=0.0` |
| `output_tokens` | integer | 否 | `default=0`; `minimum=0.0` |
| `cached_input_tokens` | integer | 否 | `default=0`; `minimum=0.0` |
| `tool_invocations` | integer | 否 | `default=0`; `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "description": "Quantity-only operation usage; input tokens exclude the cached bucket.",
  "properties": {
    "cached_input_tokens": {
      "default": 0,
      "minimum": 0.0,
      "title": "Cached Input Tokens",
      "type": "integer"
    },
    "input_tokens": {
      "default": 0,
      "minimum": 0.0,
      "title": "Input Tokens",
      "type": "integer"
    },
    "output_tokens": {
      "default": 0,
      "minimum": 0.0,
      "title": "Output Tokens",
      "type": "integer"
    },
    "tool_invocations": {
      "default": 0,
      "minimum": 0.0,
      "title": "Tool Invocations",
      "type": "integer"
    }
  },
  "title": "NormalizedUsage",
  "type": "object"
}
```

<a id="schema-OperationConfirmation"></a>

## OperationConfirmation

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `operation_id` | string | 是 | `maxLength=240`; `minLength=1` |
| `outcome` | string | 是 | `enum=["succeeded", "not_executed"]` |
| `usage` | [NormalizedUsage](schemas.md#schema-NormalizedUsage) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/NormalizedUsage"}, {"type": "null"}]` |
| `result` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "operation_id": {
      "maxLength": 240,
      "minLength": 1,
      "title": "Operation Id",
      "type": "string"
    },
    "outcome": {
      "enum": [
        "succeeded",
        "not_executed"
      ],
      "title": "Outcome",
      "type": "string"
    },
    "result": {
      "additionalProperties": true,
      "title": "Result",
      "type": "object"
    },
    "usage": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/NormalizedUsage"
        },
        {
          "type": "null"
        }
      ]
    }
  },
  "required": [
    "operation_id",
    "outcome"
  ],
  "title": "OperationConfirmation",
  "type": "object"
}
```

<a id="schema-OperationView"></a>

## OperationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `operation_id` | string | 是 | — |
| `kind` | string | 是 | — |
| `status` | string | 是 | — |
| `accounted` | boolean | 是 | — |
| `usage` | object | 是 | `additionalProperties={"type": "integer"}` |

```json
{
  "properties": {
    "accounted": {
      "title": "Accounted",
      "type": "boolean"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "operation_id": {
      "title": "Operation Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "usage": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Usage",
      "type": "object"
    }
  },
  "required": [
    "operation_id",
    "kind",
    "status",
    "accounted",
    "usage"
  ],
  "title": "OperationView",
  "type": "object"
}
```

<a id="schema-OutputDeclaration"></a>

## OutputDeclaration

One declared execution output: a safe relative path and its media type.

The path is where a run may write back inside the granted workspace;
it is validated as a safe relative path again in the service, because
a persisted declaration is later resolved against a storage root.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `path` | string | 是 | `maxLength=1000`; `minLength=1` |
| `media_type` | string anyOf null | 否 | `anyOf=[{"maxLength": 255, "type": "string"}, {"type": "null"}]` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |

```json
{
  "additionalProperties": false,
  "description": "One declared execution output: a safe relative path and its media type.\n\nThe path is where a run may write back inside the granted workspace;\nit is validated as a safe relative path again in the service, because\na persisted declaration is later resolved against a storage root.",
  "properties": {
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
    },
    "media_type": {
      "anyOf": [
        {
          "maxLength": 255,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Media Type"
    },
    "path": {
      "maxLength": 1000,
      "minLength": 1,
      "title": "Path",
      "type": "string"
    }
  },
  "required": [
    "path"
  ],
  "title": "OutputDeclaration",
  "type": "object"
}
```

<a id="schema-PlatformAdmissionView"></a>

## PlatformAdmissionView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `drained` | boolean | 是 | — |
| `active` | object | 是 | `additionalProperties={"type": "integer"}` |

```json
{
  "properties": {
    "active": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Active",
      "type": "object"
    },
    "drained": {
      "title": "Drained",
      "type": "boolean"
    }
  },
  "required": [
    "drained",
    "active"
  ],
  "title": "PlatformAdmissionView",
  "type": "object"
}
```

<a id="schema-PlatformOperationCreate"></a>

## PlatformOperationCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `owner` | string | 是 | `maxLength=80`; `minLength=1` |
| `command` | string | 是 | `maxLength=120`; `minLength=1` |
| `target` | object | 否 | `additionalProperties=true` |
| `idempotency_key` | string | 是 | `maxLength=200`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "command": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Command",
      "type": "string"
    },
    "idempotency_key": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Idempotency Key",
      "type": "string"
    },
    "owner": {
      "maxLength": 80,
      "minLength": 1,
      "title": "Owner",
      "type": "string"
    },
    "target": {
      "additionalProperties": true,
      "title": "Target",
      "type": "object"
    }
  },
  "required": [
    "owner",
    "command",
    "idempotency_key"
  ],
  "title": "PlatformOperationCreate",
  "type": "object"
}
```

<a id="schema-PlatformOperationView"></a>

## PlatformOperationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `idempotency_key` | string | 是 | — |
| `owner` | string | 是 | — |
| `command` | string | 是 | — |
| `target` | object | 是 | `additionalProperties=true` |
| `status` | string | 是 | — |
| `result` | object | 是 | `additionalProperties=true` |
| `failure_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `requested_by_user_id` | string (uuid) | 是 | `format="uuid"` |
| `error_detail` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "properties": {
    "command": {
      "title": "Command",
      "type": "string"
    },
    "error_detail": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Detail"
    },
    "failure_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Code"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "idempotency_key": {
      "title": "Idempotency Key",
      "type": "string"
    },
    "owner": {
      "title": "Owner",
      "type": "string"
    },
    "requested_by_user_id": {
      "format": "uuid",
      "title": "Requested By User Id",
      "type": "string"
    },
    "result": {
      "additionalProperties": true,
      "title": "Result",
      "type": "object"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "target": {
      "additionalProperties": true,
      "title": "Target",
      "type": "object"
    }
  },
  "required": [
    "id",
    "idempotency_key",
    "owner",
    "command",
    "target",
    "status",
    "result",
    "failure_code",
    "requested_by_user_id",
    "error_detail"
  ],
  "title": "PlatformOperationView",
  "type": "object"
}
```

<a id="schema-PolicyDecision"></a>

## PolicyDecision

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `decision` | string | 是 | `enum=["allow", "require_approval", "deny"]` |

```json
{
  "properties": {
    "decision": {
      "enum": [
        "allow",
        "require_approval",
        "deny"
      ],
      "title": "Decision",
      "type": "string"
    }
  },
  "required": [
    "decision"
  ],
  "title": "PolicyDecision",
  "type": "object"
}
```

<a id="schema-PolicyEvaluation"></a>

## PolicyEvaluation

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `action` | string | 是 | `enum=["tool.invoke", "runtime.command"]` |
| `name` | string | 是 | `maxLength=200`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
    "action": {
      "enum": [
        "tool.invoke",
        "runtime.command"
      ],
      "title": "Action",
      "type": "string"
    },
    "name": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    }
  },
  "required": [
    "action",
    "name"
  ],
  "title": "PolicyEvaluation",
  "type": "object"
}
```

<a id="schema-PolicyView"></a>

## PolicyView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `key` | string | 是 | — |
| `revision` | integer | 是 | — |
| `enabled` | boolean | 是 | — |
| `policy` | [RiskPolicy](schemas.md#schema-RiskPolicy) | 是 | — |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "properties": {
    "enabled": {
      "title": "Enabled",
      "type": "boolean"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "key": {
      "title": "Key",
      "type": "string"
    },
    "policy": {
      "$ref": "#/components/schemas/RiskPolicy"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "key",
    "revision",
    "enabled",
    "policy",
    "updated_at"
  ],
  "title": "PolicyView",
  "type": "object"
}
```

<a id="schema-PolicyWrite"></a>

## PolicyWrite

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `enabled` | boolean | 否 | `default=true` |
| `policy` | [RiskPolicy](schemas.md#schema-RiskPolicy) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "enabled": {
      "default": true,
      "title": "Enabled",
      "type": "boolean"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "policy": {
      "$ref": "#/components/schemas/RiskPolicy"
    }
  },
  "required": [
    "expected_revision",
    "policy"
  ],
  "title": "PolicyWrite",
  "type": "object"
}
```

<a id="schema-PublicationReviewDecision"></a>

## PublicationReviewDecision

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `decision` | string | 是 | `enum=["approved", "rejected"]` |
| `decision_note` | string | 否 | `default=""`; `maxLength=4000` |

```json
{
  "additionalProperties": false,
  "properties": {
    "decision": {
      "enum": [
        "approved",
        "rejected"
      ],
      "title": "Decision",
      "type": "string"
    },
    "decision_note": {
      "default": "",
      "maxLength": 4000,
      "title": "Decision Note",
      "type": "string"
    }
  },
  "required": [
    "decision"
  ],
  "title": "PublicationReviewDecision",
  "type": "object"
}
```

<a id="schema-PublicationReviewSubmission"></a>

## PublicationReviewSubmission

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `submission_note` | string | 否 | `default=""`; `maxLength=4000` |

```json
{
  "additionalProperties": false,
  "properties": {
    "submission_note": {
      "default": "",
      "maxLength": 4000,
      "title": "Submission Note",
      "type": "string"
    }
  },
  "title": "PublicationReviewSubmission",
  "type": "object"
}
```

<a id="schema-PublicationReviewView"></a>

## PublicationReviewView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `review_id` | string (uuid) | 是 | `format="uuid"` |
| `definition_id` | string (uuid) | 是 | `format="uuid"` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `version_number` | integer | 是 | — |
| `capability_type` | string | 是 | — |
| `key` | string | 是 | — |
| `name` | string | 是 | — |
| `status` | string | 是 | — |
| `requested_by_user_id` | string (uuid) | 是 | `format="uuid"` |
| `submission_note` | string | 是 | — |
| `reviewed_by_user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `decision_note` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `reviewed_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `version` | [CapabilityVersionView](schemas.md#schema-CapabilityVersionView) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "capability_type": {
      "title": "Capability Type",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "decision_note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Decision Note"
    },
    "definition_id": {
      "format": "uuid",
      "title": "Definition Id",
      "type": "string"
    },
    "key": {
      "title": "Key",
      "type": "string"
    },
    "name": {
      "title": "Name",
      "type": "string"
    },
    "requested_by_user_id": {
      "format": "uuid",
      "title": "Requested By User Id",
      "type": "string"
    },
    "review_id": {
      "format": "uuid",
      "title": "Review Id",
      "type": "string"
    },
    "reviewed_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed At"
    },
    "reviewed_by_user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reviewed By User Id"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "submission_note": {
      "title": "Submission Note",
      "type": "string"
    },
    "version": {
      "$ref": "#/components/schemas/CapabilityVersionView"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    },
    "version_number": {
      "title": "Version Number",
      "type": "integer"
    }
  },
  "required": [
    "review_id",
    "definition_id",
    "version_id",
    "version_number",
    "capability_type",
    "key",
    "name",
    "status",
    "requested_by_user_id",
    "submission_note",
    "reviewed_by_user_id",
    "decision_note",
    "created_at",
    "reviewed_at",
    "version"
  ],
  "title": "PublicationReviewView",
  "type": "object"
}
```

<a id="schema-QuotaLimitsRequest"></a>

## QuotaLimitsRequest

The administrator-writable allowances of one account.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `token_limit` | integer anyOf null | 否 | `anyOf=[{"minimum": 0.0, "type": "integer"}, {"type": "null"}]` |
| `storage_limit_bytes` | integer anyOf null | 否 | `anyOf=[{"minimum": 0.0, "type": "integer"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The administrator-writable allowances of one account.",
  "properties": {
    "storage_limit_bytes": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Storage Limit Bytes"
    },
    "token_limit": {
      "anyOf": [
        {
          "minimum": 0.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Token Limit"
    }
  },
  "title": "QuotaLimitsRequest",
  "type": "object"
}
```

<a id="schema-QuotaView"></a>

## QuotaView

One account's limits and current ledger position.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `token_limit` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |
| `used_tokens` | integer | 是 | — |
| `reserved_tokens` | integer | 是 | — |
| `available_tokens` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |
| `storage_limit_bytes` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |
| `used_bytes` | integer | 是 | — |
| `reserved_bytes` | integer | 是 | — |
| `available_bytes` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "One account's limits and current ledger position.",
  "properties": {
    "available_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Available Bytes"
    },
    "available_tokens": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Available Tokens"
    },
    "reserved_bytes": {
      "title": "Reserved Bytes",
      "type": "integer"
    },
    "reserved_tokens": {
      "title": "Reserved Tokens",
      "type": "integer"
    },
    "storage_limit_bytes": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Storage Limit Bytes"
    },
    "token_limit": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Token Limit"
    },
    "used_bytes": {
      "title": "Used Bytes",
      "type": "integer"
    },
    "used_tokens": {
      "title": "Used Tokens",
      "type": "integer"
    }
  },
  "required": [
    "token_limit",
    "used_tokens",
    "reserved_tokens",
    "available_tokens",
    "storage_limit_bytes",
    "used_bytes",
    "reserved_bytes",
    "available_bytes"
  ],
  "title": "QuotaView",
  "type": "object"
}
```

<a id="schema-ReconcileRequest"></a>

## ReconcileRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `evidence` | object | 是 | `additionalProperties=true`; `minProperties=1` |
| `operations` | array&lt;[OperationConfirmation](schemas.md#schema-OperationConfirmation)&gt; | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "evidence": {
      "additionalProperties": true,
      "minProperties": 1,
      "title": "Evidence",
      "type": "object"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "operations": {
      "items": {
        "$ref": "#/components/schemas/OperationConfirmation"
      },
      "title": "Operations",
      "type": "array"
    }
  },
  "required": [
    "command_id",
    "expected_revision",
    "evidence"
  ],
  "title": "ReconcileRequest",
  "type": "object"
}
```

<a id="schema-RegisterRequest"></a>

## RegisterRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `email` | string (email) | 是 | `format="email"`; `maxLength=320` |
| `display_name` | string | 是 | `maxLength=100`; `minLength=1` |
| `password` | string | 是 | `maxLength=256`; `minLength=8` |

```json
{
  "additionalProperties": false,
  "properties": {
    "display_name": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Display Name",
      "type": "string"
    },
    "email": {
      "format": "email",
      "maxLength": 320,
      "title": "Email",
      "type": "string"
    },
    "password": {
      "maxLength": 256,
      "minLength": 8,
      "title": "Password",
      "type": "string"
    }
  },
  "required": [
    "email",
    "display_name",
    "password"
  ],
  "title": "RegisterRequest",
  "type": "object"
}
```

<a id="schema-ResumeRequest"></a>

## ResumeRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `approval_id` | string (uuid) | 是 | `format="uuid"` |
| `expected_revision` | integer | 是 | `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "approval_id": {
      "format": "uuid",
      "title": "Approval Id",
      "type": "string"
    },
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    }
  },
  "required": [
    "command_id",
    "approval_id",
    "expected_revision"
  ],
  "title": "ResumeRequest",
  "type": "object"
}
```

<a id="schema-RevisionRequest"></a>

## RevisionRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    }
  },
  "required": [
    "expected_revision"
  ],
  "title": "RevisionRequest",
  "type": "object"
}
```

<a id="schema-RiskPolicy"></a>

## RiskPolicy

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `default` | string | 否 | `default="allow"`; `enum=["allow", "require_approval", "deny"]` |
| `rules` | array&lt;[RiskRule](schemas.md#schema-RiskRule)&gt; | 否 | `maxItems=100` |

```json
{
  "additionalProperties": false,
  "properties": {
    "default": {
      "default": "allow",
      "enum": [
        "allow",
        "require_approval",
        "deny"
      ],
      "title": "Default",
      "type": "string"
    },
    "rules": {
      "items": {
        "$ref": "#/components/schemas/RiskRule"
      },
      "maxItems": 100,
      "title": "Rules",
      "type": "array"
    }
  },
  "title": "RiskPolicy",
  "type": "object"
}
```

<a id="schema-RiskRule"></a>

## RiskRule

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `action` | string | 是 | `enum=["tool.invoke", "runtime.command"]` |
| `name` | string anyOf null | 否 | `anyOf=[{"maxLength": 200, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `decision` | string | 是 | `enum=["allow", "require_approval", "deny"]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "action": {
      "enum": [
        "tool.invoke",
        "runtime.command"
      ],
      "title": "Action",
      "type": "string"
    },
    "decision": {
      "enum": [
        "allow",
        "require_approval",
        "deny"
      ],
      "title": "Decision",
      "type": "string"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 200,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    }
  },
  "required": [
    "action",
    "decision"
  ],
  "title": "RiskRule",
  "type": "object"
}
```

<a id="schema-RunOutput"></a>

## RunOutput

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `path` | string | 是 | `maxLength=240`; `minLength=1` |
| `name` | string | 是 | `maxLength=240`; `minLength=1` |
| `media_type` | string anyOf null | 否 | `anyOf=[{"maxLength": 200, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "media_type": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Media Type"
    },
    "name": {
      "maxLength": 240,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "path": {
      "maxLength": 240,
      "minLength": 1,
      "title": "Path",
      "type": "string"
    }
  },
  "required": [
    "path",
    "name"
  ],
  "title": "RunOutput",
  "type": "object"
}
```

<a id="schema-RunView"></a>

## RunView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `run_id` | string (uuid) | 是 | `format="uuid"` |
| `session_id` | string (uuid) | 是 | `format="uuid"` |
| `sequence` | integer | 是 | — |
| `status` | string | 是 | — |
| `output` | any | 否 | — |
| `artifacts` | array&lt;string (uuid)&gt; | 否 | — |
| `usage` | object | 否 | `additionalProperties={"type": "integer"}` |
| `error_code` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `revision` | integer | 是 | — |
| `created_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "properties": {
    "artifacts": {
      "items": {
        "format": "uuid",
        "type": "string"
      },
      "title": "Artifacts",
      "type": "array"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "error_code": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Error Code"
    },
    "output": {
      "title": "Output"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "run_id": {
      "format": "uuid",
      "title": "Run Id",
      "type": "string"
    },
    "sequence": {
      "title": "Sequence",
      "type": "integer"
    },
    "session_id": {
      "format": "uuid",
      "title": "Session Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "usage": {
      "additionalProperties": {
        "type": "integer"
      },
      "title": "Usage",
      "type": "object"
    }
  },
  "required": [
    "run_id",
    "session_id",
    "sequence",
    "status",
    "error_code",
    "revision",
    "created_at"
  ],
  "title": "RunView",
  "type": "object"
}
```

<a id="schema-RuntimeControlRequest"></a>

## RuntimeControlRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `expected_revision` | integer | 是 | `minimum=0.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    }
  },
  "required": [
    "command_id",
    "expected_revision"
  ],
  "title": "RuntimeControlRequest",
  "type": "object"
}
```

<a id="schema-RuntimeOperationView"></a>

## RuntimeOperationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `operation_id` | string | 是 | — |
| `status` | string | 是 | — |
| `revision` | integer | 否 | `default=0` |
| `result` | object anyOf null | 否 | `anyOf=[{"additionalProperties": true, "type": "object"}, {"type": "null"}]` |

```json
{
  "properties": {
    "operation_id": {
      "title": "Operation Id",
      "type": "string"
    },
    "result": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "title": "Result"
    },
    "revision": {
      "default": 0,
      "title": "Revision",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "operation_id",
    "status"
  ],
  "title": "RuntimeOperationView",
  "type": "object"
}
```

<a id="schema-RuntimePolicyRequest"></a>

## RuntimePolicyRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `frozen_input_versions` | array&lt;integer&gt; | 否 | `maxItems=256` |
| `relative_paths` | array&lt;string&gt; | 否 | `maxItems=256` |
| `output_declarations` | array&lt;[OutputDeclaration](schemas.md#schema-OutputDeclaration)&gt; | 否 | `maxItems=256` |
| `use_in_execution` | boolean | 否 | `default=false` |

```json
{
  "additionalProperties": false,
  "properties": {
    "frozen_input_versions": {
      "items": {
        "type": "integer"
      },
      "maxItems": 256,
      "title": "Frozen Input Versions",
      "type": "array"
    },
    "output_declarations": {
      "items": {
        "$ref": "#/components/schemas/OutputDeclaration"
      },
      "maxItems": 256,
      "title": "Output Declarations",
      "type": "array"
    },
    "relative_paths": {
      "items": {
        "type": "string"
      },
      "maxItems": 256,
      "title": "Relative Paths",
      "type": "array"
    },
    "use_in_execution": {
      "default": false,
      "title": "Use In Execution",
      "type": "boolean"
    }
  },
  "title": "RuntimePolicyRequest",
  "type": "object"
}
```

<a id="schema-RuntimePolicyView"></a>

## RuntimePolicyView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `file_id` | string (uuid) | 是 | `format="uuid"` |
| `frozen_input_versions` | array&lt;integer&gt; | 是 | — |
| `relative_paths` | array&lt;string&gt; | 是 | — |
| `output_declarations` | array&lt;[OutputDeclaration](schemas.md#schema-OutputDeclaration)&gt; | 是 | — |
| `use_in_execution` | boolean | 是 | — |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "file_id": {
      "format": "uuid",
      "title": "File Id",
      "type": "string"
    },
    "frozen_input_versions": {
      "items": {
        "type": "integer"
      },
      "title": "Frozen Input Versions",
      "type": "array"
    },
    "output_declarations": {
      "items": {
        "$ref": "#/components/schemas/OutputDeclaration"
      },
      "title": "Output Declarations",
      "type": "array"
    },
    "relative_paths": {
      "items": {
        "type": "string"
      },
      "title": "Relative Paths",
      "type": "array"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "use_in_execution": {
      "title": "Use In Execution",
      "type": "boolean"
    }
  },
  "required": [
    "file_id",
    "frozen_input_versions",
    "relative_paths",
    "output_declarations",
    "use_in_execution",
    "updated_at"
  ],
  "title": "RuntimePolicyView",
  "type": "object"
}
```

<a id="schema-RuntimeReconcileRequest"></a>

## RuntimeReconcileRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `command_id` | string (uuid) | 是 | `format="uuid"` |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `unresolved_operations` | array&lt;string&gt; | 是 | `minItems=1` |
| `evidence` | object | 是 | `additionalProperties=true`; `minProperties=1` |
| `confirmed_status` | string | 是 | `enum=["ready", "closed"]` |
| `result` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "command_id": {
      "format": "uuid",
      "title": "Command Id",
      "type": "string"
    },
    "confirmed_status": {
      "enum": [
        "ready",
        "closed"
      ],
      "title": "Confirmed Status",
      "type": "string"
    },
    "evidence": {
      "additionalProperties": true,
      "minProperties": 1,
      "title": "Evidence",
      "type": "object"
    },
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "result": {
      "additionalProperties": true,
      "title": "Result",
      "type": "object"
    },
    "unresolved_operations": {
      "items": {
        "type": "string"
      },
      "minItems": 1,
      "title": "Unresolved Operations",
      "type": "array"
    }
  },
  "required": [
    "command_id",
    "expected_revision",
    "unresolved_operations",
    "evidence",
    "confirmed_status"
  ],
  "title": "RuntimeReconcileRequest",
  "type": "object"
}
```

<a id="schema-SessionCreateRequest"></a>

## SessionCreateRequest

The scope a new session is opened in; the user scope is the default.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The scope a new session is opened in; the user scope is the default.",
  "properties": {
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    }
  },
  "title": "SessionCreateRequest",
  "type": "object"
}
```

<a id="schema-SessionGrant"></a>

## SessionGrant

A session grant returned by registration and login.

``session_token`` is the secret the client presents as ``X-Session-ID``;
it is shown once here and never stored, so a database leak alone cannot
authenticate. The public row id appears in the session listings.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `session_token` | string (uuid) | 是 | `format="uuid"` |
| `expires_at` | string (date-time) | 是 | `format="date-time"` |
| `user` | [AuthenticatedUser](schemas.md#schema-AuthenticatedUser) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "A session grant returned by registration and login.\n\n``session_token`` is the secret the client presents as ``X-Session-ID``;\nit is shown once here and never stored, so a database leak alone cannot\nauthenticate. The public row id appears in the session listings.",
  "properties": {
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "session_token": {
      "format": "uuid",
      "title": "Session Token",
      "type": "string"
    },
    "user": {
      "$ref": "#/components/schemas/AuthenticatedUser"
    }
  },
  "required": [
    "session_token",
    "expires_at",
    "user"
  ],
  "title": "SessionGrant",
  "type": "object"
}
```

<a id="schema-SessionInfo"></a>

## SessionInfo

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `session_id` | string (uuid) | 是 | `format="uuid"` |
| `expires_at` | string (date-time) | 是 | `format="date-time"` |
| `revoked_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "expires_at": {
      "format": "date-time",
      "title": "Expires At",
      "type": "string"
    },
    "revoked_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Revoked At"
    },
    "session_id": {
      "format": "uuid",
      "title": "Session Id",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "expires_at",
    "revoked_at"
  ],
  "title": "SessionInfo",
  "type": "object"
}
```

<a id="schema-SessionView"></a>

## SessionView

One model session as stored.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `session_id` | string (uuid) | 是 | `format="uuid"` |
| `scope_id` | string (uuid) | 是 | `format="uuid"` |
| `principal_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `owner_ref` | string | 是 | — |
| `status` | string | 是 | `enum=["open", "closing", "closed", "blocked"]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "description": "One model session as stored.",
  "properties": {
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "owner_ref": {
      "title": "Owner Ref",
      "type": "string"
    },
    "principal_id": {
      "format": "uuid",
      "title": "Principal Id",
      "type": "string"
    },
    "scope_id": {
      "format": "uuid",
      "title": "Scope Id",
      "type": "string"
    },
    "session_id": {
      "format": "uuid",
      "title": "Session Id",
      "type": "string"
    },
    "status": {
      "enum": [
        "open",
        "closing",
        "closed",
        "blocked"
      ],
      "title": "Status",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "session_id",
    "scope_id",
    "principal_id",
    "configuration_id",
    "owner_ref",
    "status",
    "created_at",
    "updated_at"
  ],
  "title": "SessionView",
  "type": "object"
}
```

<a id="schema-SkillConfiguration"></a>

## SkillConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `frontmatter` | [SkillFrontmatter](schemas.md#schema-SkillFrontmatter) | 是 | — |
| `instructions` | string | 是 | `minLength=1` |
| `parameters_schema` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "frontmatter": {
      "$ref": "#/components/schemas/SkillFrontmatter"
    },
    "instructions": {
      "minLength": 1,
      "title": "Instructions",
      "type": "string"
    },
    "parameters_schema": {
      "additionalProperties": true,
      "title": "Parameters Schema",
      "type": "object"
    }
  },
  "required": [
    "frontmatter",
    "instructions"
  ],
  "title": "SkillConfiguration",
  "type": "object"
}
```

<a id="schema-SkillDeclaration"></a>

## SkillDeclaration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `type` | string | 是 | `const="skill"` |
| `configuration` | [SkillConfiguration](schemas.md#schema-SkillConfiguration) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/SkillConfiguration"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/SkillConfiguration"
        },
        {
          "type": "null"
        }
      ]
    },
    "type": {
      "const": "skill",
      "title": "Type",
      "type": "string"
    }
  },
  "required": [
    "type"
  ],
  "title": "SkillDeclaration",
  "type": "object"
}
```

<a id="schema-SkillFrontmatter"></a>

## SkillFrontmatter

The standard SKILL.md metadata block.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `maxLength=100`; `minLength=1` |
| `description` | string | 是 | `maxLength=2000`; `minLength=1` |
| `license` | string anyOf null | 否 | `anyOf=[{"maxLength": 100, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": true,
  "description": "The standard SKILL.md metadata block.",
  "properties": {
    "description": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Description",
      "type": "string"
    },
    "license": {
      "anyOf": [
        {
          "maxLength": 100,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "License"
    },
    "name": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    }
  },
  "required": [
    "name",
    "description"
  ],
  "title": "SkillFrontmatter",
  "type": "object"
}
```

<a id="schema-StatePage"></a>

## StatePage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `entries` | array&lt;object&gt; | 是 | — |
| `next_cursor` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "entries": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "title": "Entries",
      "type": "array"
    },
    "next_cursor": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Next Cursor"
    }
  },
  "required": [
    "entries",
    "next_cursor"
  ],
  "title": "StatePage",
  "type": "object"
}
```

<a id="schema-StateRestoreRequest"></a>

## StateRestoreRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=0.0` |
| `snapshot` | [StateSnapshot](schemas.md#schema-StateSnapshot) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 0.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "snapshot": {
      "$ref": "#/components/schemas/StateSnapshot"
    }
  },
  "required": [
    "expected_revision",
    "snapshot"
  ],
  "title": "StateRestoreRequest",
  "type": "object"
}
```

<a id="schema-StateRestoreResult"></a>

## StateRestoreResult

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `restored_entries` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "restored_entries": {
      "title": "Restored Entries",
      "type": "integer"
    }
  },
  "required": [
    "restored_entries"
  ],
  "title": "StateRestoreResult",
  "type": "object"
}
```

<a id="schema-StateSnapshot"></a>

## StateSnapshot

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `installation_id` | string (uuid) | 是 | `format="uuid"` |
| `version_id` | string (uuid) | 是 | `format="uuid"` |
| `revision` | integer | 是 | `minimum=0.0` |
| `entries` | array&lt;object&gt; | 是 | `maxItems=10000` |

```json
{
  "additionalProperties": false,
  "properties": {
    "entries": {
      "items": {
        "additionalProperties": true,
        "type": "object"
      },
      "maxItems": 10000,
      "title": "Entries",
      "type": "array"
    },
    "installation_id": {
      "format": "uuid",
      "title": "Installation Id",
      "type": "string"
    },
    "revision": {
      "minimum": 0.0,
      "title": "Revision",
      "type": "integer"
    },
    "version_id": {
      "format": "uuid",
      "title": "Version Id",
      "type": "string"
    }
  },
  "required": [
    "installation_id",
    "version_id",
    "revision",
    "entries"
  ],
  "title": "StateSnapshot",
  "type": "object"
}
```

<a id="schema-ToolDeclaration"></a>

## ToolDeclaration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `type` | string | 是 | `const="tool"` |
| `configuration` | [opsmesh__capabilities__tools__schemas__ToolConfiguration](schemas.md#schema-opsmesh__capabilities__tools__schemas__ToolConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/opsmesh__capabilities__tools__schemas__ToolConfiguration"
    },
    "type": {
      "const": "tool",
      "title": "Type",
      "type": "string"
    }
  },
  "required": [
    "type",
    "configuration"
  ],
  "title": "ToolDeclaration",
  "type": "object"
}
```

<a id="schema-ToolOperation"></a>

## ToolOperation

One callable operation with its schemas, effects and approval demand.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `maxLength=100`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `input_schema` | object | 否 | `additionalProperties=true` |
| `output_schema` | object | 否 | `additionalProperties=true` |
| `effects` | array&lt;string&gt; | 否 | `maxItems=7` |
| `requires_approval` | boolean | 否 | `default=false` |
| `timeout_seconds` | integer | 否 | `default=60`; `maximum=3600.0`; `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "description": "One callable operation with its schemas, effects and approval demand.",
  "properties": {
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
    },
    "effects": {
      "items": {
        "type": "string"
      },
      "maxItems": 7,
      "title": "Effects",
      "type": "array"
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "name": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "output_schema": {
      "additionalProperties": true,
      "title": "Output Schema",
      "type": "object"
    },
    "requires_approval": {
      "default": false,
      "title": "Requires Approval",
      "type": "boolean"
    },
    "timeout_seconds": {
      "default": 60,
      "maximum": 3600.0,
      "minimum": 1.0,
      "title": "Timeout Seconds",
      "type": "integer"
    }
  },
  "required": [
    "name"
  ],
  "title": "ToolOperation",
  "type": "object"
}
```

<a id="schema-TriggerAccepted"></a>

## TriggerAccepted

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `status` | string | 是 | — |
| `flow_run_id` | string (uuid) | 是 | `format="uuid"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "flow_run_id": {
      "format": "uuid",
      "title": "Flow Run Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status",
    "flow_run_id"
  ],
  "title": "TriggerAccepted",
  "type": "object"
}
```

<a id="schema-TriggerConfiguration"></a>

## TriggerConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `source_kind` | string | 否 | `default="extension"`; `enum=["extension", "run", "platform"]` |
| `event_type` | string | 否 | `default=""`; `maxLength=200` |
| `instance_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `interval_seconds` | integer anyOf null | 否 | `anyOf=[{"maximum": 31536000.0, "minimum": 10.0, "type": "integer"}, {"type": "null"}]` |
| `cron` | string anyOf null | 否 | `anyOf=[{"maxLength": 200, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `time_zone` | string | 否 | `default="UTC"`; `maxLength=100` |
| `input` | object | 否 | `additionalProperties=true` |
| `payload_schema` | object | 否 | `additionalProperties=true` |
| `replay_seconds` | integer | 否 | `default=300`; `maximum=3600.0`; `minimum=10.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "cron": {
      "anyOf": [
        {
          "maxLength": 200,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cron"
    },
    "event_type": {
      "default": "",
      "maxLength": 200,
      "title": "Event Type",
      "type": "string"
    },
    "input": {
      "additionalProperties": true,
      "title": "Input",
      "type": "object"
    },
    "instance_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Instance Id"
    },
    "interval_seconds": {
      "anyOf": [
        {
          "maximum": 31536000.0,
          "minimum": 10.0,
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "Interval Seconds"
    },
    "payload_schema": {
      "additionalProperties": true,
      "title": "Payload Schema",
      "type": "object"
    },
    "replay_seconds": {
      "default": 300,
      "maximum": 3600.0,
      "minimum": 10.0,
      "title": "Replay Seconds",
      "type": "integer"
    },
    "source_kind": {
      "default": "extension",
      "enum": [
        "extension",
        "run",
        "platform"
      ],
      "title": "Source Kind",
      "type": "string"
    },
    "time_zone": {
      "default": "UTC",
      "maxLength": 100,
      "title": "Time Zone",
      "type": "string"
    }
  },
  "title": "TriggerConfiguration",
  "type": "object"
}
```

<a id="schema-TriggerControl"></a>

## TriggerControl

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `expected_revision` | integer | 是 | `minimum=1.0` |
| `status` | string | 是 | `enum=["enabled", "disabled"]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "expected_revision": {
      "minimum": 1.0,
      "title": "Expected Revision",
      "type": "integer"
    },
    "status": {
      "enum": [
        "enabled",
        "disabled"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "expected_revision",
    "status"
  ],
  "title": "TriggerControl",
  "type": "object"
}
```

<a id="schema-TriggerCreate"></a>

## TriggerCreate

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `revision_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_id` | string (uuid) anyOf null | 否 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `kind` | string | 是 | `enum=["event", "calendar", "webhook"]` |
| `configuration` | [TriggerConfiguration](schemas.md#schema-TriggerConfiguration) | 是 | — |
| `webhook_secret` | string anyOf null | 否 | `anyOf=[{"maxLength": 200, "minLength": 32, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/TriggerConfiguration"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "kind": {
      "enum": [
        "event",
        "calendar",
        "webhook"
      ],
      "title": "Kind",
      "type": "string"
    },
    "revision_id": {
      "format": "uuid",
      "title": "Revision Id",
      "type": "string"
    },
    "webhook_secret": {
      "anyOf": [
        {
          "maxLength": 200,
          "minLength": 32,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Webhook Secret"
    }
  },
  "required": [
    "revision_id",
    "kind",
    "configuration"
  ],
  "title": "TriggerCreate",
  "type": "object"
}
```

<a id="schema-TriggerView"></a>

## TriggerView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `id` | string (uuid) | 是 | `format="uuid"` |
| `revision_id` | string (uuid) | 是 | `format="uuid"` |
| `configuration_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `kind` | string | 是 | — |
| `revision` | integer | 是 | — |
| `status` | string | 是 | — |
| `configuration` | [TriggerConfiguration](schemas.md#schema-TriggerConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/TriggerConfiguration"
    },
    "configuration_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Configuration Id"
    },
    "id": {
      "format": "uuid",
      "title": "Id",
      "type": "string"
    },
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "revision": {
      "title": "Revision",
      "type": "integer"
    },
    "revision_id": {
      "format": "uuid",
      "title": "Revision Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "id",
    "revision_id",
    "configuration_id",
    "kind",
    "revision",
    "status",
    "configuration"
  ],
  "title": "TriggerView",
  "type": "object"
}
```

<a id="schema-UpdateDefinitionRequest"></a>

## UpdateDefinitionRequest

The definition-level metadata an author may change.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string anyOf null | 否 | `anyOf=[{"maxLength": 160, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `description` | string anyOf null | 否 | `anyOf=[{"maxLength": 2000, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The definition-level metadata an author may change.",
  "properties": {
    "description": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Description"
    },
    "name": {
      "anyOf": [
        {
          "maxLength": 160,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Name"
    }
  },
  "title": "UpdateDefinitionRequest",
  "type": "object"
}
```

<a id="schema-UpdateDefinitionStatusRequest"></a>

## UpdateDefinitionStatusRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `status` | string | 是 | `enum=["active", "disabled", "deleted"]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "status": {
      "enum": [
        "active",
        "disabled",
        "deleted"
      ],
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "status"
  ],
  "title": "UpdateDefinitionStatusRequest",
  "type": "object"
}
```

<a id="schema-UpdateDefinitionVisibilityRequest"></a>

## UpdateDefinitionVisibilityRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `visibility` | string | 是 | `enum=["private", "public"]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "visibility": {
      "enum": [
        "private",
        "public"
      ],
      "title": "Visibility",
      "type": "string"
    }
  },
  "required": [
    "visibility"
  ],
  "title": "UpdateDefinitionVisibilityRequest",
  "type": "object"
}
```

<a id="schema-UpdateProfileRequest"></a>

## UpdateProfileRequest

The fields an account may change on its own profile.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `display_name` | string anyOf null | 否 | `anyOf=[{"maxLength": 100, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `work_root` | string anyOf null | 否 | `anyOf=[{"maxLength": 500, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The fields an account may change on its own profile.",
  "properties": {
    "display_name": {
      "anyOf": [
        {
          "maxLength": 100,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Display Name"
    },
    "work_root": {
      "anyOf": [
        {
          "maxLength": 500,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Work Root"
    }
  },
  "title": "UpdateProfileRequest",
  "type": "object"
}
```

<a id="schema-UserPage"></a>

## UserPage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `users` | array&lt;[UserProfileView](schemas.md#schema-UserProfileView)&gt; | 是 | — |
| `total` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "total": {
      "title": "Total",
      "type": "integer"
    },
    "users": {
      "items": {
        "$ref": "#/components/schemas/UserProfileView"
      },
      "title": "Users",
      "type": "array"
    }
  },
  "required": [
    "users",
    "total"
  ],
  "title": "UserPage",
  "type": "object"
}
```

<a id="schema-UserProfileView"></a>

## UserProfileView

One account profile; avatar bytes are fetched from their own route.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `user_id` | string (uuid) | 是 | `format="uuid"` |
| `email` | string (email) | 是 | `format="email"` |
| `display_name` | string | 是 | — |
| `status` | string | 是 | — |
| `platform_roles` | array&lt;string&gt; | 是 | — |
| `work_root` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `has_avatar` | boolean | 是 | — |
| `avatar_content_type` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `avatar_updated_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `updated_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "description": "One account profile; avatar bytes are fetched from their own route.",
  "properties": {
    "avatar_content_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avatar Content Type"
    },
    "avatar_updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Avatar Updated At"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "display_name": {
      "title": "Display Name",
      "type": "string"
    },
    "email": {
      "format": "email",
      "title": "Email",
      "type": "string"
    },
    "has_avatar": {
      "title": "Has Avatar",
      "type": "boolean"
    },
    "platform_roles": {
      "items": {
        "type": "string"
      },
      "title": "Platform Roles",
      "type": "array"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "updated_at": {
      "format": "date-time",
      "title": "Updated At",
      "type": "string"
    },
    "user_id": {
      "format": "uuid",
      "title": "User Id",
      "type": "string"
    },
    "work_root": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Work Root"
    }
  },
  "required": [
    "user_id",
    "email",
    "display_name",
    "status",
    "platform_roles",
    "work_root",
    "has_avatar",
    "avatar_content_type",
    "avatar_updated_at",
    "created_at",
    "updated_at"
  ],
  "title": "UserProfileView",
  "type": "object"
}
```

<a id="schema-VersionDiffView"></a>

## VersionDiffView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `from_version` | integer | 是 | — |
| `to_version` | integer | 是 | — |
| `identical` | boolean | 是 | — |
| `from_size` | integer | 是 | — |
| `to_size` | integer | 是 | — |
| `size_delta` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "from_size": {
      "title": "From Size",
      "type": "integer"
    },
    "from_version": {
      "title": "From Version",
      "type": "integer"
    },
    "identical": {
      "title": "Identical",
      "type": "boolean"
    },
    "size_delta": {
      "title": "Size Delta",
      "type": "integer"
    },
    "to_size": {
      "title": "To Size",
      "type": "integer"
    },
    "to_version": {
      "title": "To Version",
      "type": "integer"
    }
  },
  "required": [
    "from_version",
    "to_version",
    "identical",
    "from_size",
    "to_size",
    "size_delta"
  ],
  "title": "VersionDiffView",
  "type": "object"
}
```

<a id="schema-VersionView"></a>

## VersionView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `service` | string | 是 | — |
| `version` | string | 是 | — |
| `environment` | string | 是 | — |
| `build` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "build": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Build"
    },
    "environment": {
      "title": "Environment",
      "type": "string"
    },
    "service": {
      "title": "Service",
      "type": "string"
    },
    "version": {
      "title": "Version",
      "type": "string"
    }
  },
  "required": [
    "service",
    "version",
    "environment",
    "build"
  ],
  "title": "VersionView",
  "type": "object"
}
```

<a id="schema-opsmesh__capabilities__tools__schemas__ToolConfiguration"></a>

## opsmesh__capabilities__tools__schemas__ToolConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `execution_mode` | string | 是 | — |
| `entrypoint` | string | 是 | `maxLength=300`; `minLength=1` |
| `operations` | array&lt;[ToolOperation](schemas.md#schema-ToolOperation)&gt; | 是 | `maxItems=256`; `minItems=1` |
| `parameters_schema` | object | 否 | `additionalProperties=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "entrypoint": {
      "maxLength": 300,
      "minLength": 1,
      "title": "Entrypoint",
      "type": "string"
    },
    "execution_mode": {
      "title": "Execution Mode",
      "type": "string"
    },
    "operations": {
      "items": {
        "$ref": "#/components/schemas/ToolOperation"
      },
      "maxItems": 256,
      "minItems": 1,
      "title": "Operations",
      "type": "array"
    },
    "parameters_schema": {
      "additionalProperties": true,
      "title": "Parameters Schema",
      "type": "object"
    }
  },
  "required": [
    "execution_mode",
    "entrypoint",
    "operations"
  ],
  "title": "ToolConfiguration",
  "type": "object"
}
```

<a id="schema-opsmesh__orchestration__execution__contracts__ToolConfiguration"></a>

## opsmesh__orchestration__execution__contracts__ToolConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | `pattern="^[a-zA-Z0-9_-]{1,64}$"` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `input_schema` | object | 是 | `additionalProperties=true` |
| `command` | array&lt;string&gt; | 否 | `maxItems=100` |
| `extension` | [InstalledExport](schemas.md#schema-InstalledExport) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/InstalledExport"}, {"type": "null"}]` |
| `approval_required` | boolean | 否 | `default=true` |

```json
{
  "additionalProperties": false,
  "properties": {
    "approval_required": {
      "default": true,
      "title": "Approval Required",
      "type": "boolean"
    },
    "command": {
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "title": "Command",
      "type": "array"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
    },
    "extension": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/InstalledExport"
        },
        {
          "type": "null"
        }
      ]
    },
    "input_schema": {
      "additionalProperties": true,
      "title": "Input Schema",
      "type": "object"
    },
    "name": {
      "pattern": "^[a-zA-Z0-9_-]{1,64}$",
      "title": "Name",
      "type": "string"
    }
  },
  "required": [
    "name",
    "input_schema"
  ],
  "title": "ToolConfiguration",
  "type": "object"
}
```
