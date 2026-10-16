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

<a id="schema-AgentAsTool"></a>

## AgentAsTool

One member exposed to another member as a callable tool.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `source` | string | 是 | `maxLength=100`; `minLength=1` |
| `tool` | string | 是 | `maxLength=100`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "description": "One member exposed to another member as a callable tool.",
  "properties": {
    "source": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Source",
      "type": "string"
    },
    "tool": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Tool",
      "type": "string"
    }
  },
  "required": [
    "source",
    "tool"
  ],
  "title": "AgentAsTool",
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
| `approver_user_id` | string (uuid) | 是 | `format="uuid"` |
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
    "approver_user_id": {
      "format": "uuid",
      "title": "Approver User Id",
      "type": "string"
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

One cursor page of audit events ordered by chain sequence.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `events` | array&lt;[AuditEventView](schemas.md#schema-AuditEventView)&gt; | 是 | — |
| `next_after_sequence` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "One cursor page of audit events ordered by chain sequence.",
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

One sanitized audit fact; the digest columns prove its chain position.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `event_id` | string (uuid) | 是 | `format="uuid"` |
| `sequence` | integer | 是 | — |
| `occurred_at` | string (date-time) | 是 | `format="date-time"` |
| `actor_type` | string | 是 | — |
| `actor_id` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `workspace_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `project_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
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
| `previous_hash` | string | 是 | — |
| `event_hash` | string | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "One sanitized audit fact; the digest columns prove its chain position.",
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
    "details": {
      "additionalProperties": true,
      "title": "Details",
      "type": "object"
    },
    "event_hash": {
      "title": "Event Hash",
      "type": "string"
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
    "previous_hash": {
      "title": "Previous Hash",
      "type": "string"
    },
    "project_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Project Id"
    },
    "sequence": {
      "title": "Sequence",
      "type": "integer"
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
    "workspace_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Workspace Id"
    }
  },
  "required": [
    "event_id",
    "sequence",
    "occurred_at",
    "actor_type",
    "actor_id",
    "workspace_id",
    "project_id",
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
    "source",
    "previous_hash",
    "event_hash"
  ],
  "title": "AuditEventView",
  "type": "object"
}
```

<a id="schema-AuditVerificationRequest"></a>

## AuditVerificationRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `from_sequence` | integer | 是 | `minimum=1.0` |
| `to_sequence` | integer | 是 | `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "from_sequence": {
      "minimum": 1.0,
      "title": "From Sequence",
      "type": "integer"
    },
    "to_sequence": {
      "minimum": 1.0,
      "title": "To Sequence",
      "type": "integer"
    }
  },
  "required": [
    "from_sequence",
    "to_sequence"
  ],
  "title": "AuditVerificationRequest",
  "type": "object"
}
```

<a id="schema-AuditVerificationView"></a>

## AuditVerificationView

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `verification_id` | string (uuid) | 是 | `format="uuid"` |
| `status` | string | 是 | — |
| `from_sequence` | integer | 是 | — |
| `to_sequence` | integer | 是 | — |
| `checked_count` | integer | 是 | — |
| `first_broken_sequence` | integer anyOf null | 是 | `anyOf=[{"type": "integer"}, {"type": "null"}]` |
| `failure_reason` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "properties": {
    "checked_count": {
      "title": "Checked Count",
      "type": "integer"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "failure_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Reason"
    },
    "first_broken_sequence": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "First Broken Sequence"
    },
    "from_sequence": {
      "title": "From Sequence",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "to_sequence": {
      "title": "To Sequence",
      "type": "integer"
    },
    "verification_id": {
      "format": "uuid",
      "title": "Verification Id",
      "type": "string"
    }
  },
  "required": [
    "verification_id",
    "status",
    "from_sequence",
    "to_sequence",
    "checked_count",
    "first_broken_sequence",
    "failure_reason",
    "created_at"
  ],
  "title": "AuditVerificationView",
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

<a id="schema-CapabilitySlot"></a>

## CapabilitySlot

One named capability slot an expert fills through fixed dependencies.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `slot` | string | 是 | — |
| `kind` | string | 是 | `enum=["tool", "mcp", "skill"]` |

```json
{
  "additionalProperties": false,
  "description": "One named capability slot an expert fills through fixed dependencies.",
  "properties": {
    "kind": {
      "enum": [
        "tool",
        "mcp",
        "skill"
      ],
      "title": "Kind",
      "type": "string"
    },
    "slot": {
      "title": "Slot",
      "type": "string"
    }
  },
  "required": [
    "slot",
    "kind"
  ],
  "title": "CapabilitySlot",
  "type": "object"
}
```

<a id="schema-CapabilityVersionView"></a>

## CapabilityVersionView

One immutable-or-draft version with its full declared content.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `definition_id` | string (uuid) | 是 | `format="uuid"` |
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
    }
  },
  "required": [
    "definition_id",
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

<a id="schema-ChannelConfiguration"></a>

## ChannelConfiguration

The vendor protocol one channel definition speaks.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `protocol` | string | 是 | `maxLength=50`; `minLength=2` |
| `signature_algorithm` | string | 否 | `const="hmac-sha256"`; `default="hmac-sha256"` |
| `operations` | array&lt;[ChannelOperation](schemas.md#schema-ChannelOperation)&gt; | 是 | `maxItems=4`; `minItems=1` |
| `parameters_schema` | object | 否 | `additionalProperties=true` |
| `secret_slots` | array&lt;string&gt; | 否 | — |

```json
{
  "additionalProperties": false,
  "description": "The vendor protocol one channel definition speaks.",
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
    "secret_slots": {
      "items": {
        "type": "string"
      },
      "title": "Secret Slots",
      "type": "array"
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

<a id="schema-ChannelCreateRequest"></a>

## ChannelCreateRequest

Create one channel definition with its first draft version.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `maxLength=100`; `minLength=1` |
| `name` | string | 是 | `maxLength=160`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `configuration` | [ChannelConfiguration](schemas.md#schema-ChannelConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Create one channel definition with its first draft version.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ChannelConfiguration"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
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
    "configuration"
  ],
  "title": "ChannelCreateRequest",
  "type": "object"
}
```

<a id="schema-ChannelDraftRequest"></a>

## ChannelDraftRequest

Replace the current draft configuration of one channel definition.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration` | [ChannelConfiguration](schemas.md#schema-ChannelConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Replace the current draft configuration of one channel definition.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ChannelConfiguration"
    }
  },
  "required": [
    "configuration"
  ],
  "title": "ChannelDraftRequest",
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

<a id="schema-DirectoryCreateRequest"></a>

## DirectoryCreateRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `path` | string | 是 | `maxLength=1000`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "properties": {
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

<a id="schema-ExpertConfiguration"></a>

## ExpertConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `instructions` | string | 是 | — |
| `prompt` | string | 否 | `default=""` |
| `model_requirements` | [ModelRequirements](schemas.md#schema-ModelRequirements) anyOf null | 否 | `anyOf=[{"$ref": "#/components/schemas/ModelRequirements"}, {"type": "null"}]` |
| `guardrails` | array&lt;string&gt; | 否 | `default=[]` |
| `capability_slots` | array&lt;[CapabilitySlot](schemas.md#schema-CapabilitySlot)&gt; | 否 | `default=[]` |
| `parameters_schema` | object | 否 | `additionalProperties=true`; `default={}` |
| `secret_slots` | array&lt;string&gt; | 否 | `default=[]` |

```json
{
  "additionalProperties": false,
  "properties": {
    "capability_slots": {
      "default": [],
      "items": {
        "$ref": "#/components/schemas/CapabilitySlot"
      },
      "title": "Capability Slots",
      "type": "array"
    },
    "guardrails": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Guardrails",
      "type": "array"
    },
    "instructions": {
      "title": "Instructions",
      "type": "string"
    },
    "model_requirements": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/ModelRequirements"
        },
        {
          "type": "null"
        }
      ]
    },
    "parameters_schema": {
      "additionalProperties": true,
      "default": {},
      "title": "Parameters Schema",
      "type": "object"
    },
    "prompt": {
      "default": "",
      "title": "Prompt",
      "type": "string"
    },
    "secret_slots": {
      "default": [],
      "items": {
        "type": "string"
      },
      "title": "Secret Slots",
      "type": "array"
    }
  },
  "required": [
    "instructions"
  ],
  "title": "ExpertConfiguration",
  "type": "object"
}
```

<a id="schema-ExpertCreateRequest"></a>

## ExpertCreateRequest

Create one expert definition with its first draft version.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `maxLength=100`; `minLength=1` |
| `name` | string | 是 | `maxLength=160`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `configuration` | [ExpertConfiguration](schemas.md#schema-ExpertConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Create one expert definition with its first draft version.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ExpertConfiguration"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
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
    "configuration"
  ],
  "title": "ExpertCreateRequest",
  "type": "object"
}
```

<a id="schema-ExpertDraftRequest"></a>

## ExpertDraftRequest

Replace the current draft configuration of one expert definition.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration` | [ExpertConfiguration](schemas.md#schema-ExpertConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Replace the current draft configuration of one expert definition.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ExpertConfiguration"
    }
  },
  "required": [
    "configuration"
  ],
  "title": "ExpertDraftRequest",
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

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `operations` | array&lt;[ExtensionOperation](schemas.md#schema-ExtensionOperation)&gt; | 否 | `maxItems=256` |
| `hooks` | array&lt;[ExtensionHook](schemas.md#schema-ExtensionHook)&gt; | 否 | `maxItems=256` |
| `events` | array&lt;[ExtensionEvent](schemas.md#schema-ExtensionEvent)&gt; | 否 | `maxItems=256` |
| `permissions` | array&lt;string&gt; | 否 | `maxItems=128` |

```json
{
  "additionalProperties": false,
  "description": "The export contract one extension definition publishes.",
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

<a id="schema-ExtensionCreateRequest"></a>

## ExtensionCreateRequest

Create one extension definition with its first draft version.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `maxLength=100`; `minLength=1` |
| `name` | string | 是 | `maxLength=160`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `configuration` | [ExtensionConfiguration](schemas.md#schema-ExtensionConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Create one extension definition with its first draft version.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ExtensionConfiguration"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
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
    "configuration"
  ],
  "title": "ExtensionCreateRequest",
  "type": "object"
}
```

<a id="schema-ExtensionDraftRequest"></a>

## ExtensionDraftRequest

Replace the current draft configuration of one extension definition.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration` | [ExtensionConfiguration](schemas.md#schema-ExtensionConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Replace the current draft configuration of one extension definition.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ExtensionConfiguration"
    }
  },
  "required": [
    "configuration"
  ],
  "title": "ExtensionDraftRequest",
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

<a id="schema-Handoff"></a>

## Handoff

One directed handoff edge between two member slots.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `source` | string | 是 | `maxLength=100`; `minLength=1` |
| `target` | string | 是 | `maxLength=100`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "description": "One directed handoff edge between two member slots.",
  "properties": {
    "source": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Source",
      "type": "string"
    },
    "target": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Target",
      "type": "string"
    }
  },
  "required": [
    "source",
    "target"
  ],
  "title": "Handoff",
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
| `send_count` | integer | 是 | — |
| `last_sent_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
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
    "last_sent_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Last Sent At"
    },
    "send_count": {
      "title": "Send Count",
      "type": "integer"
    },
    "status": {
      "title": "Status",
      "type": "string"
    }
  },
  "required": [
    "invitation_id",
    "email",
    "status",
    "expires_at",
    "created_at",
    "accepted_at",
    "send_count",
    "last_sent_at",
    "invited_by_user_id"
  ],
  "title": "InvitationView",
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

<a id="schema-MailIntentPage"></a>

## MailIntentPage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `intents` | array&lt;[MailIntentView](schemas.md#schema-MailIntentView)&gt; | 是 | — |
| `total` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "intents": {
      "items": {
        "$ref": "#/components/schemas/MailIntentView"
      },
      "title": "Intents",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "intents",
    "total"
  ],
  "title": "MailIntentPage",
  "type": "object"
}
```

<a id="schema-MailIntentView"></a>

## MailIntentView

One durable send intent and its confirmed or unconfirmed outcome.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `intent_id` | string (uuid) | 是 | `format="uuid"` |
| `source` | string | 是 | — |
| `source_ref` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `source_revision` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `recipient` | string | 是 | — |
| `subject` | string | 是 | — |
| `status` | string | 是 | — |
| `attempts` | integer | 是 | — |
| `outcome_unknown` | boolean | 是 | — |
| `failure_reason` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |
| `sent_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `reconciled_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `reconciliation_note` | string anyOf null | 是 | `anyOf=[{"type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "One durable send intent and its confirmed or unconfirmed outcome.",
  "properties": {
    "attempts": {
      "title": "Attempts",
      "type": "integer"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "failure_reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Failure Reason"
    },
    "intent_id": {
      "format": "uuid",
      "title": "Intent Id",
      "type": "string"
    },
    "outcome_unknown": {
      "title": "Outcome Unknown",
      "type": "boolean"
    },
    "recipient": {
      "title": "Recipient",
      "type": "string"
    },
    "reconciled_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reconciled At"
    },
    "reconciliation_note": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reconciliation Note"
    },
    "sent_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Sent At"
    },
    "source": {
      "title": "Source",
      "type": "string"
    },
    "source_ref": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Ref"
    },
    "source_revision": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Revision"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "subject": {
      "title": "Subject",
      "type": "string"
    }
  },
  "required": [
    "intent_id",
    "source",
    "source_ref",
    "source_revision",
    "recipient",
    "subject",
    "status",
    "attempts",
    "outcome_unknown",
    "failure_reason",
    "created_at",
    "sent_at",
    "reconciled_at",
    "reconciliation_note"
  ],
  "title": "MailIntentView",
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
| `secret_slots` | array&lt;string&gt; | 否 | — |

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
    "secret_slots": {
      "items": {
        "type": "string"
      },
      "title": "Secret Slots",
      "type": "array"
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

<a id="schema-McpCreateRequest"></a>

## McpCreateRequest

Create one MCP definition with its first draft version.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `maxLength=100`; `minLength=1` |
| `name` | string | 是 | `maxLength=160`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `configuration` | [McpConfiguration](schemas.md#schema-McpConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Create one MCP definition with its first draft version.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/McpConfiguration"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
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
    "configuration"
  ],
  "title": "McpCreateRequest",
  "type": "object"
}
```

<a id="schema-McpDraftRequest"></a>

## McpDraftRequest

Replace the current draft configuration of one MCP definition.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration` | [McpConfiguration](schemas.md#schema-McpConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Replace the current draft configuration of one MCP definition.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/McpConfiguration"
    }
  },
  "required": [
    "configuration"
  ],
  "title": "McpDraftRequest",
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

<a id="schema-ModelRequirements"></a>

## ModelRequirements

The model an expert expects; selection itself belongs to execution.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `model` | string | 是 | `maxLength=100`; `minLength=1` |
| `reasoning` | string anyOf null | 否 | `anyOf=[{"maxLength": 40, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The model an expert expects; selection itself belongs to execution.",
  "properties": {
    "model": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Model",
      "type": "string"
    },
    "reasoning": {
      "anyOf": [
        {
          "maxLength": 40,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reasoning"
    }
  },
  "required": [
    "model"
  ],
  "title": "ModelRequirements",
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

<a id="schema-ReconcileMailRequest"></a>

## ReconcileMailRequest

The operator's confirmation of one unconfirmed send.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `outcome` | string | 是 | `maxLength=20` |
| `note` | string anyOf null | 否 | `anyOf=[{"maxLength": 2000, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The operator's confirmation of one unconfirmed send.",
  "properties": {
    "note": {
      "anyOf": [
        {
          "maxLength": 2000,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Note"
    },
    "outcome": {
      "maxLength": 20,
      "title": "Outcome",
      "type": "string"
    }
  },
  "required": [
    "outcome"
  ],
  "title": "ReconcileMailRequest",
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

<a id="schema-SecurityEventPage"></a>

## SecurityEventPage

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `events` | array&lt;[SecurityEventView](schemas.md#schema-SecurityEventView)&gt; | 是 | — |
| `total` | integer | 是 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "events": {
      "items": {
        "$ref": "#/components/schemas/SecurityEventView"
      },
      "title": "Events",
      "type": "array"
    },
    "total": {
      "title": "Total",
      "type": "integer"
    }
  },
  "required": [
    "events",
    "total"
  ],
  "title": "SecurityEventPage",
  "type": "object"
}
```

<a id="schema-SecurityEventView"></a>

## SecurityEventView

One sanitized security fact; details never carry credentials.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `event_id` | string (uuid) | 是 | `format="uuid"` |
| `kind` | string | 是 | — |
| `severity` | string | 是 | — |
| `summary` | string | 是 | — |
| `details` | object | 是 | `additionalProperties=true` |
| `user_id` | string (uuid) anyOf null | 是 | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `occurred_at` | string (date-time) | 是 | `format="date-time"` |
| `created_at` | string (date-time) | 是 | `format="date-time"` |

```json
{
  "additionalProperties": false,
  "description": "One sanitized security fact; details never carry credentials.",
  "properties": {
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
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
    "kind": {
      "title": "Kind",
      "type": "string"
    },
    "occurred_at": {
      "format": "date-time",
      "title": "Occurred At",
      "type": "string"
    },
    "severity": {
      "title": "Severity",
      "type": "string"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    },
    "user_id": {
      "anyOf": [
        {
          "format": "uuid",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "User Id"
    }
  },
  "required": [
    "event_id",
    "kind",
    "severity",
    "summary",
    "details",
    "user_id",
    "occurred_at",
    "created_at"
  ],
  "title": "SecurityEventView",
  "type": "object"
}
```

<a id="schema-SendMailRequest"></a>

## SendMailRequest

One delegated send; the source comes from the verified invocation.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `recipient` | string (email) | 是 | `format="email"`; `maxLength=320` |
| `subject` | string | 是 | `maxLength=500`; `minLength=1` |
| `body` | string | 是 | `minLength=1` |
| `idempotency_key` | string | 是 | `maxLength=200`; `minLength=1` |
| `source_ref` | string anyOf null | 否 | `anyOf=[{"maxLength": 200, "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "One delegated send; the source comes from the verified invocation.",
  "properties": {
    "body": {
      "minLength": 1,
      "title": "Body",
      "type": "string"
    },
    "idempotency_key": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Idempotency Key",
      "type": "string"
    },
    "recipient": {
      "format": "email",
      "maxLength": 320,
      "title": "Recipient",
      "type": "string"
    },
    "source_ref": {
      "anyOf": [
        {
          "maxLength": 200,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Ref"
    },
    "subject": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Subject",
      "type": "string"
    }
  },
  "required": [
    "recipient",
    "subject",
    "body",
    "idempotency_key"
  ],
  "title": "SendMailRequest",
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

<a id="schema-SkillCreateRequest"></a>

## SkillCreateRequest

Import one skill definition; its configuration comes from the package.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `maxLength=100`; `minLength=1` |
| `name` | string | 是 | `maxLength=160`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |

```json
{
  "additionalProperties": false,
  "description": "Import one skill definition; its configuration comes from the package.",
  "properties": {
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
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
    "name"
  ],
  "title": "SkillCreateRequest",
  "type": "object"
}
```

<a id="schema-SmtpConfigurationRequest"></a>

## SmtpConfigurationRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `host` | string | 是 | `maxLength=255`; `minLength=1` |
| `port` | integer | 是 | `maximum=65535.0`; `minimum=1.0` |
| `security` | string | 是 | `maxLength=20` |
| `username` | string | 否 | `default=""`; `maxLength=255` |
| `password` | string | 是 | `maxLength=255`; `minLength=1` |
| `from_email` | string (email) | 是 | `format="email"`; `maxLength=320` |
| `from_name` | string | 是 | `maxLength=100`; `minLength=1` |
| `timeout_seconds` | integer | 否 | `default=30`; `maximum=300.0`; `minimum=1.0` |

```json
{
  "additionalProperties": false,
  "properties": {
    "from_email": {
      "format": "email",
      "maxLength": 320,
      "title": "From Email",
      "type": "string"
    },
    "from_name": {
      "maxLength": 100,
      "minLength": 1,
      "title": "From Name",
      "type": "string"
    },
    "host": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Host",
      "type": "string"
    },
    "password": {
      "maxLength": 255,
      "minLength": 1,
      "title": "Password",
      "type": "string"
    },
    "port": {
      "maximum": 65535.0,
      "minimum": 1.0,
      "title": "Port",
      "type": "integer"
    },
    "security": {
      "maxLength": 20,
      "title": "Security",
      "type": "string"
    },
    "timeout_seconds": {
      "default": 30,
      "maximum": 300.0,
      "minimum": 1.0,
      "title": "Timeout Seconds",
      "type": "integer"
    },
    "username": {
      "default": "",
      "maxLength": 255,
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "host",
    "port",
    "security",
    "password",
    "from_email",
    "from_name"
  ],
  "title": "SmtpConfigurationRequest",
  "type": "object"
}
```

<a id="schema-SmtpConfigurationView"></a>

## SmtpConfigurationView

The stored configuration; the password is never rendered back.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `host` | string | 是 | — |
| `port` | integer | 是 | — |
| `security` | string | 是 | — |
| `username` | string | 是 | — |
| `from_email` | string (email) | 是 | `format="email"` |
| `from_name` | string | 是 | — |
| `timeout_seconds` | integer | 是 | — |
| `password_configured` | boolean | 是 | — |
| `updated_at` | string (date-time) anyOf null | 是 | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |

```json
{
  "additionalProperties": false,
  "description": "The stored configuration; the password is never rendered back.",
  "properties": {
    "from_email": {
      "format": "email",
      "title": "From Email",
      "type": "string"
    },
    "from_name": {
      "title": "From Name",
      "type": "string"
    },
    "host": {
      "title": "Host",
      "type": "string"
    },
    "password_configured": {
      "title": "Password Configured",
      "type": "boolean"
    },
    "port": {
      "title": "Port",
      "type": "integer"
    },
    "security": {
      "title": "Security",
      "type": "string"
    },
    "timeout_seconds": {
      "title": "Timeout Seconds",
      "type": "integer"
    },
    "updated_at": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Updated At"
    },
    "username": {
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "host",
    "port",
    "security",
    "username",
    "from_email",
    "from_name",
    "timeout_seconds",
    "password_configured",
    "updated_at"
  ],
  "title": "SmtpConfigurationView",
  "type": "object"
}
```

<a id="schema-TeamConfiguration"></a>

## TeamConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `members` | array&lt;[TeamMember](schemas.md#schema-TeamMember)&gt; | 是 | `maxItems=32`; `minItems=1` |
| `entrypoint` | string | 是 | `maxLength=100`; `minLength=1` |
| `handoffs` | array&lt;[Handoff](schemas.md#schema-Handoff)&gt; | 否 | `maxItems=128` |
| `agents_as_tools` | array&lt;[AgentAsTool](schemas.md#schema-AgentAsTool)&gt; | 否 | `maxItems=128` |
| `parameters_schema` | object | 否 | `additionalProperties=true` |
| `secret_slots` | array&lt;string&gt; | 否 | — |

```json
{
  "additionalProperties": false,
  "properties": {
    "agents_as_tools": {
      "items": {
        "$ref": "#/components/schemas/AgentAsTool"
      },
      "maxItems": 128,
      "title": "Agents As Tools",
      "type": "array"
    },
    "entrypoint": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Entrypoint",
      "type": "string"
    },
    "handoffs": {
      "items": {
        "$ref": "#/components/schemas/Handoff"
      },
      "maxItems": 128,
      "title": "Handoffs",
      "type": "array"
    },
    "members": {
      "items": {
        "$ref": "#/components/schemas/TeamMember"
      },
      "maxItems": 32,
      "minItems": 1,
      "title": "Members",
      "type": "array"
    },
    "parameters_schema": {
      "additionalProperties": true,
      "title": "Parameters Schema",
      "type": "object"
    },
    "secret_slots": {
      "items": {
        "type": "string"
      },
      "title": "Secret Slots",
      "type": "array"
    }
  },
  "required": [
    "members",
    "entrypoint"
  ],
  "title": "TeamConfiguration",
  "type": "object"
}
```

<a id="schema-TeamCreateRequest"></a>

## TeamCreateRequest

Create one team definition with its first draft version.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `maxLength=100`; `minLength=1` |
| `name` | string | 是 | `maxLength=160`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `configuration` | [TeamConfiguration](schemas.md#schema-TeamConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Create one team definition with its first draft version.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/TeamConfiguration"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
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
    "configuration"
  ],
  "title": "TeamCreateRequest",
  "type": "object"
}
```

<a id="schema-TeamDraftRequest"></a>

## TeamDraftRequest

Replace the current draft configuration of one team definition.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration` | [TeamConfiguration](schemas.md#schema-TeamConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Replace the current draft configuration of one team definition.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/TeamConfiguration"
    }
  },
  "required": [
    "configuration"
  ],
  "title": "TeamDraftRequest",
  "type": "object"
}
```

<a id="schema-TeamMember"></a>

## TeamMember

One expert member filling one named slot with one unique role.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `slot` | string | 是 | `maxLength=100`; `minLength=1` |
| `expert_key` | string | 是 | `maxLength=100`; `minLength=1` |
| `role` | string | 是 | `maxLength=100`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "description": "One expert member filling one named slot with one unique role.",
  "properties": {
    "expert_key": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Expert Key",
      "type": "string"
    },
    "role": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Role",
      "type": "string"
    },
    "slot": {
      "maxLength": 100,
      "minLength": 1,
      "title": "Slot",
      "type": "string"
    }
  },
  "required": [
    "slot",
    "expert_key",
    "role"
  ],
  "title": "TeamMember",
  "type": "object"
}
```

<a id="schema-TestMailRequest"></a>

## TestMailRequest

One administrator-triggered SMTP test.

``idempotency_key`` identifies the test operation, not the configuration:
a retry of the same request reuses the key and replays its intent, while
a new test — after a failure, a configuration change or simply again —
carries a new key and starts a new send. The key is persisted as declared
inside the test source's own scope, so this bound is the stored one.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `recipient` | string (email) | 是 | `format="email"`; `maxLength=320` |
| `idempotency_key` | string | 是 | `maxLength=200`; `minLength=1` |

```json
{
  "additionalProperties": false,
  "description": "One administrator-triggered SMTP test.\n\n``idempotency_key`` identifies the test operation, not the configuration:\na retry of the same request reuses the key and replays its intent, while\na new test — after a failure, a configuration change or simply again —\ncarries a new key and starts a new send. The key is persisted as declared\ninside the test source's own scope, so this bound is the stored one.",
  "properties": {
    "idempotency_key": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Idempotency Key",
      "type": "string"
    },
    "recipient": {
      "format": "email",
      "maxLength": 320,
      "title": "Recipient",
      "type": "string"
    }
  },
  "required": [
    "recipient",
    "idempotency_key"
  ],
  "title": "TestMailRequest",
  "type": "object"
}
```

<a id="schema-ToolConfiguration"></a>

## ToolConfiguration

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `execution_mode` | string | 是 | — |
| `entrypoint` | string | 是 | `maxLength=300`; `minLength=1` |
| `operations` | array&lt;[ToolOperation](schemas.md#schema-ToolOperation)&gt; | 是 | `maxItems=256`; `minItems=1` |
| `parameters_schema` | object | 否 | `additionalProperties=true` |
| `secret_slots` | array&lt;string&gt; | 否 | — |

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
    },
    "secret_slots": {
      "items": {
        "type": "string"
      },
      "title": "Secret Slots",
      "type": "array"
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

<a id="schema-ToolCreateRequest"></a>

## ToolCreateRequest

Create one tool definition with its first draft version.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `key` | string | 是 | `maxLength=100`; `minLength=1` |
| `name` | string | 是 | `maxLength=160`; `minLength=1` |
| `description` | string | 否 | `default=""`; `maxLength=2000` |
| `visibility` | string | 否 | `default="private"`; `enum=["private", "public"]` |
| `configuration` | [ToolConfiguration](schemas.md#schema-ToolConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Create one tool definition with its first draft version.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ToolConfiguration"
    },
    "description": {
      "default": "",
      "maxLength": 2000,
      "title": "Description",
      "type": "string"
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
    "configuration"
  ],
  "title": "ToolCreateRequest",
  "type": "object"
}
```

<a id="schema-ToolDraftRequest"></a>

## ToolDraftRequest

Replace the current draft configuration of one tool definition.

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `configuration` | [ToolConfiguration](schemas.md#schema-ToolConfiguration) | 是 | — |

```json
{
  "additionalProperties": false,
  "description": "Replace the current draft configuration of one tool definition.",
  "properties": {
    "configuration": {
      "$ref": "#/components/schemas/ToolConfiguration"
    }
  },
  "required": [
    "configuration"
  ],
  "title": "ToolDraftRequest",
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
