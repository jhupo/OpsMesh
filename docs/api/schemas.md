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

<a id="schema-TestMailRequest"></a>

## TestMailRequest

| 字段 | 类型 | 必填 | 说明与约束 |
| --- | --- | --- | --- |
| `recipient` | string (email) | 是 | `format="email"`; `maxLength=320` |

```json
{
  "additionalProperties": false,
  "properties": {
    "recipient": {
      "format": "email",
      "maxLength": 320,
      "title": "Recipient",
      "type": "string"
    }
  },
  "required": [
    "recipient"
  ],
  "title": "TestMailRequest",
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
