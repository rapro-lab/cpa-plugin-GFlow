# GFlow project control protocol v1 (plugin 0.1.12)

Project control reuses `POST /v1/chat/completions` with a regular generation API key; no management key is needed. The dedicated model `gflow-project-control` only performs control operations and never reaches the image/video generator. The existing seven media models and unbound third-party requests keep their current behavior; clients should exclude the control model from their media picker.

The request must use `stream: false` and contain one user message whose content is a JSON string:

```json
{
  "model": "gflow-project-control",
  "stream": false,
  "messages": [{
    "role": "user",
    "content": "{\"version\":1,\"op\":\"capabilities\",\"workspaceId\":\"11111111-1111-4111-8111-111111111111\"}"
  }]
}
```

Control JSON:

- `version: 1`, `op` and `workspaceId` are required; workspace and project identifiers are UUIDs.
- `capabilities` needs no account and returns `models`, the service instance and the `projectBinding` capability. Without an account it still returns the base 1K catalog; that response does not mean a session is signed in or that generation will succeed.
- `projects.list` returns the projects registered for that workspace and does not scan the whole Google account or other workspaces.
- `projects.get` requires a local `projectId` and returns `project` plus the model parameters allowed by the owning account's tier. An optional `flowProjectId` (null or UUID) is diagnostic; the UUID must match the stored binding and takes no part in routing.
- `projects.create` additionally requires `projectId`, `operationId` and `name` (1-128 characters); the optional `preferredModel` is one of the seven base media models and defaults to `gflow-nano-banana-2-lite`. Once an account is chosen it immediately calls Google Flow's create-project RPC and generates no media.

Success responses use a standard chat completion; parse the JSON inside `choices[0].message.content`:

```json
{
  "version": 1,
  "instanceId": "service persistent UUID",
  "workspaceId": "client workspace UUID",
  "projectBinding": true,
  "capabilities": {"projectBinding": true},
  "project": {
    "id": "CutFlow local project UUID",
    "name": "Project name",
    "flowProjectId": "Google Flow project UUID",
    "state": "ready",
    "accountLabel": "a***@example.invalid",
    "instanceId": "service persistent UUID"
  },
  "models": [{
    "id": "gflow-nano-banana-2-lite",
    "kind": "image",
    "maxReferences": 3,
    "aspects": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "imageSizes": ["1K"],
    "durations": []
  }]
}
```

Control protocol validation, project lookup and binding-conflict errors still return a standard chat completion, with `{version:1,projectBinding:true,error:{code,message,status_code}}` as the content JSON; clients must check `error` first and only then validate the instance fields of a success response. This keeps compatibility with the current host, which collapses executor exceptions into `plugin_error` at the ABI. Transport interruptions are still handled as HTTP/host errors and must not be used to rebuild a project automatically.

`list` uses a `projects` array; `create`/`get` use a single `project`. Project `state` is `creating|ready|failed|creation_unknown`; failures may carry `error:{code,message}`. Cookies, keys, internal account IDs and raw email addresses are never returned. 2K requires Pro and 4K requires Ult; the parameter catalog reflects the known account tier, and real generation still needs platform validation and credits.

Generation inside a project still uses the original media model, adding these fields at the top level of the standard request:

```json
{
  "gflow_workspace_id": "client workspace UUID",
  "gflow_project_id": "real Google Flow project UUID",
  "gflow_instance_id": "service persistent UUID"
}
```

`gflow_project_id` is the `flowProjectId`, not the local project ID. All three fields must be present and must match the service record. When the project's account is disabled, removed or has changed identity, its tier is insufficient, or the verification route is unavailable, the request fails; switching accounts, falling back to the project pool or creating a replacement project is forbidden. Images and videos, streaming and non-streaming share this rule, and the request context is cleared after errors and cancellations. When an account refresh returns a new object, the enabled flag and tier are re-validated; fixed requests always run a non-blocking concurrency preflight that consumes no extra upstream slot. Whether the quota is sufficient still depends on Google Flow's actual response, and platform quota errors never trigger an account switch. For consecutive image edits the client explicitly supplies the selected image as the reference; project binding never turns other session history into a reference automatically.

Operation ids and project request content are idempotent per workspace: an identical duplicate returns the original record, and conflicting parameters are rejected. If the RPC has already started and then times out, is cancelled or loses its receipt, the state is `creation_unknown` and nothing is rebuilt automatically, avoiding duplicate cloud projects. On restart the service turns unfinished `creating` records into `creation_unknown`. Confirmed failures are not retried automatically either; the user must review and create a new project. Cloud rename, delete, migration and scan-based recovery are out of scope for protocol v1.

SQLite adds metadata and CutFlow project tables to the existing flow.db; legacy `projects` records default to purpose `pool` while new dedicated projects are `cutflow`, so polling clients only see `pool`. The 27 pinned upstream source files and the Google RPC implementation keep their original hashes.

Permission boundary: a regular API key represents generation access to that EasyCPA instance; the workspace UUID groups data and is not additional user authentication, so this protocol must not be described as multi-tenant isolation. The existing `/v0/management` routes still require separate management authentication. Real host routing, real account creation and real media generation must be accepted separately; offline tests do not prove they are verified.
