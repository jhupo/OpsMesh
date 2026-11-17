# Channels release review — 2026-11-16

Scope: pending v0.5.6 channel transport, typed replies, conversation examples,
incoming attachment downloads, and packaging version alignment.

## Validation

- 347 affected channel, queue, chat, sandbox, and client tests passed.
- The attachment tests passed again after adding a safe platform-error regression
  (32 tests, including the additional test).
- CI exposed a missed HTTP protocol test migration: old string streams caused
  the backpressure fixture to wait indefinitely. Migrated the sources to typed
  stream events, updated message IDs and budget/deadline assertions, and bounded
  the fixture wait. All 36 HTTP protocol tests then passed. The first CI run was
  cancelled and replaced; it is not counted as a successful validation.
- Ruff and Git whitespace checks passed. Removed import paths were searched
  across source, examples, tests, and documentation.
- Reviewed HTTP dispatch/response ownership, cancellation, conversation queue
  ordering, sender validation, native DingTalk card updates, example-owned file
  selection/persistence, and dependency configuration parsing.
- GitNexus identified the incoming DingTalk attachment callers. Taint and PDG
  queries returned no findings for the downloader; this does not establish
  complete coverage of callbacks or dynamic dispatch.
- Independent reviewer agents failed to run because their inference service
  returned insufficient balance. The completed review was performed locally.

## Attachment incident

The production journal records an attachment-read failure at
2026-11-16 09:25:01 UTC, before model execution. The old log omitted the cause;
the original temporary download code was not retained across process restart.
The exact cause of that individual incident is therefore unconfirmed.

The downloader previously rejected every redirect. It now follows at most
three redirects, validating HTTPS, port, credentials, and vendor host at every
hop. Cookies are not forwarded; the total timeout and decoded byte limit remain
bounded. Tests cover a Markdown attachment, allowed redirects, disallowed hosts,
HTTPS downgrade, loops, missing locations, size limits, and safe failure details.
DingTalk API failures now become inspectable AttachmentError values and produce
logs without download codes, tokens, response bodies, or signed URLs.

The original file must be resent to confirm the real robot download after
deployment. No unsolicited robot test message was sent.

## Follow-up — 2026-11-17

The user resent the file after enabling the robot plugin. Origin diagnostics
identified an HTTP URL from `lippi-space-sh.oss-cn-shanghai.aliyuncs.com`.
The existing HTTPS-only check rejected that URL before downloading it; the
Markdown MIME type was valid. Redirect handling alone did not fix this case.

The DingTalk adapter now upgrades native OSS bucket HTTP URLs to HTTPS before
calling the shared downloader, preserving the signed path and query verbatim.
Unknown HTTP origins, embedded credentials, and nonstandard ports are not
upgraded. The shared downloader remains HTTPS-only. Rejection logs include only
scheme, host, and port, without paths, signatures, or credentials.

All 39 attachment tests passed. The server validated TLS for the observed host.
After deployment, the user's resend at 01:35:44 UTC passed attachment reading
and sandbox upload storage and reached successful model calls. Final agent
response delivery was still in progress when this evidence was recorded.
This follow-up was applied as a server plugin hotfix after v0.5.6 publication;
the existing v0.5.6 release artifacts were not modified.
