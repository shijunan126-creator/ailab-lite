> **实施修订（2026-11-04，权威差异见 [ADR 0033](../adr/0033-context-epoch-band-process-snapshot.md)）**
> - §25–29：压缩后目标由“窗口 50–60%”改为整份请求 10K–48K（瞄准 30K）、硬上限 64K，小窗口按比例缩放；任何重建也须通过同一校验。
> - 计数：触发、压缩后校验、窗口检查均以每次调用返回的 `prompt_tokens` 为锚点，只估算新增内容。
> - §28/§31：失败（含输出截断）统一为保留旧纪元与后台任务的 `ContextPause`；只有当前用户输入本身可超过硬上限。
> - §78–83：ProcessView 现为 V2（逐步确认、终态核对）；事件序号以快照为下界。
> - 下文为原始 HLD v4，保留作设计依据。

# AILab Context Compression, Prefix Cache, Multi-Tenant & Durable Process HLD

> **Status:** Proposed HLD v4  
> **Code audit baseline:** `ailab-lite/main` (reviewed 2026-11-03)  
> **Scope:** Chat / Work / Skills / Memory / Tools / Subtasks / multi-turn context / historical work-process UI  
> **Primary goals:** correctness → prefix-cache stability → bounded context → implementation simplicity

---

## 1. Executive Decision

AILab adopts one model-facing context architecture:

```text
Stable Prefix
    ↓
Frozen Checkpoint
    ↓
Recent Raw Complete Groups
    ↓
Current Turn
```

A checkpoint is a **logical composite**:

```text
Frozen Checkpoint
├── Structured Semantic Summary     # LLM-generated, schema-validated
└── Program-Owned Operational State # deterministic runtime state
```

Normal policy:

```text
Compaction trigger        = 90% of model context window
Post-compaction target    = 50–60% (best effort, not a hard invariant)
Safety zone               = 95%
Provider hard limit       = 100%
```

Hard rules:

> **Within one Context Epoch, previously sent model messages and tool schemas are never rewritten.**

> **Compaction may only happen at a Safe Compaction Point with no unresolved model/tool protocol pair.**

> **Semantic history may be lossy; operational correctness state must remain deterministic.**

> **Conversation records are durable, but execution traces have retention policies and must not be treated as permanent history.**

---

## 2. Current-State Audit

The existing AILab implementation already contains most of the required primitives. The HLD should consolidate them rather than introduce a second context subsystem.

### 2.1 Durable conversation record already exists

Current persistent layers include:

```text
Conversation
Run
RunEvent
RunCheckpoint
Files / Artifacts
Workspace snapshots
ContextSummaryFrame

NEW:
RunProcessSnapshot
```

However these have different lifecycle guarantees.

### Long-lived business history

`Conversation` and `Run` contain the user-visible conversation path, prompts, replies/summaries, status, model, ancestry, attachments and result metadata.

For a live, non-deleted conversation these form the durable conversation record.

### Retention-governed execution traces

`RunEvent` is durable in the database, but is not permanent.

Current default:

```text
RUN_EVENTS_RETENTION_DAYS = 30
```

Terminal-run events older than the configured retention period are garbage-collected.

Therefore:

> **Compaction correctness must never depend on old RunEvent rows remaining forever.**

The historical UI must also not depend on retained `RunEvent` rows forever. A terminal/paused run materializes a durable user-visible process record before old execution events are eligible for GC. This record is the **RunProcessSnapshot** defined later in this HLD.

### Runtime checkpoints

`RunCheckpoint` is durable runtime state used for:

```text
resume
recovery
exact model-context inheritance
prefix-cache continuity
pending tool execution
workspace/runtime correctness
```

It is a runtime substrate, not a separate permanent full-history archive.

### Workspace

Current default:

```text
WORKSPACE_TTL_DAYS = 3
```

Workspace snapshots have their own lifecycle.

### Deleted conversations

Current default:

```text
CONVERSATION_PURGE_DAYS = 30
```

This means a conversation that has already been deleted is physically purged after the configured delay. It does **not** mean every conversation disappears 30 days after creation.

---

## 3. Correct Durability Terminology

Do not describe the architecture as:

```text
Full raw history is permanently stored forever.
```

Use:

```text
Durable Conversation Record
├── Conversation / Runs
├── Structured checkpoints / summaries
├── Durable Process Snapshot       # historical UI only
├── Retention-governed RunEvents   # live execution + debug
├── Runtime RunCheckpoints
└── Files / workspace under their own lifecycle
```

The model-facing architecture must remain correct even after:

```text
old RunEvents expired
old workspace snapshots expired
superseded runtime snapshots were collected
```

For normal model continuation, the durable semantic state is therefore:

```text
Conversation / Runs
+
Structured Checkpoint
+
recent still-available raw turns
```

For historical UI rendering, the durable process state is:

```text
RunProcessSnapshot
```

These two paths are intentionally separate. `RunProcessSnapshot` is never injected into model context and is not part of prefix-cache construction. The architecture does not assume that every historical tool event can always be replayed.

---

## 4. Target Architecture

```text
                          Persistent Platform State
                                   │
            ┌──────────────────────┼──────────────────────┐
            │                      │                      │
      Conversation/Runs      Summary Frames       Runtime Checkpoints
            │                      │                      │
            └──────────────────────┼──────────────────────┘
                                   │
                                   ▼
                          ┌─────────────────┐
                          │ ContextManager  │
                          └────────┬────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
        ▼                          ▼                          ▼
 Stable Prefix             Frozen Checkpoint            Recent Raw Tail
        │                          │                          │
        └──────────────────────────┼──────────────────────────┘
                                   │
                              Current Turn
                                   │
                                   ▼
                                  LLM
                                   │
                              append only
                                   │
                                   ▼
                               AgentLoop
                                   │
                      ┌────────────┴────────────┐
                      │                         │
                 Tool Runtime             ContextManager
                      │                         │
                      └──── Compaction Barrier ┘
```

`ContextManager` is a code-level abstraction, not a new service.

Recommended location:

```text
platform/ailab_platform/agent/context.py
```

---

## 5. Stable Prefix

The globally reusable prefix should contain only deterministic platform state:

```text
Platform System Prompt
Fixed Tool Catalog
Provider request shape
Versioned platform rules
```

It should remain byte-stable for:

```text
same model capability set
same mode
same platform prompt version
same tool-catalog version
same request-shape version
```

Do not place the following before the reusable platform boundary:

```text
user id/name
conversation id
run id
timestamps
random ids
memory contents
personal skill contents
current files
current project state
dynamic current-turn data
```

---

## 6. Two Cache Layers

AILab should reason about two reusable prefix layers.

### 6.1 Platform Prefix

```text
[Platform System]
[Fixed Tool Catalog]
```

Designed to be as user-independent as possible.

### 6.2 Conversation Epoch Prefix

```text
[Platform System]
[Fixed Tool Catalog]
[Conversation Boundary Context]
[Frozen Checkpoint]
```

Stable inside one Context Epoch.

Dynamic conversation state begins only after the platform-stable prefix.

---

## 7. Context Epoch

A **Context Epoch** means:

> a period during which every model request extends the previous model-facing context without mutating any already-sent prefix bytes.

Example:

```text
Epoch 12

Stable Prefix
Checkpoint 12
Raw Group A
Raw Group B
Current work...
```

Compaction creates:

```text
Epoch 13

Stable Prefix
Checkpoint 13
Protected Recent Groups
Current work...
```

### 7.1 Important correction

`ContextSummaryFrame.revision` must **not** be used as the universal Context Epoch ID.

Reason:

- conversation-level summary frames have their own revision;
- Work can compact inside a single Run;
- an in-run compaction changes the model-facing prefix without necessarily creating a new `ContextSummaryFrame`.

Therefore introduce a runtime epoch counter:

```text
context_epoch: integer
```

stored in `RunCheckpoint` state.

Rules:

```text
new run inheriting exact parent context:
    inherit epoch

any actual prefix-replacing boundary:
    context_epoch += 1
```

`ContextSummaryFrame.revision` remains the revision of the persistent conversation semantic summary only.

It is provenance, not the global cache epoch.

---

## 8. Explicit Context Boundaries

Only these events may replace the existing model prefix:

```text
compacted
model_changed
context_abi_changed
access_revoked
rebuilt
```

Every boundary must emit:

```text
agent.context_boundary
```

with its reason.

Inside an epoch:

```text
append only
```

No hidden mutation.

---

## 9. Frozen Checkpoint

A Frozen Checkpoint is the authoritative model-facing representation of already-compressed context.

It is logically:

```text
Frozen Checkpoint
├── Semantic Summary
└── Operational Context
```

Once activated in an epoch:

```text
immutable
```

Do not:

```text
patch the summary every turn
rewrite memory snapshot
rewrite old tool results
rewrite skill results
silently shorten old outputs
```

A new checkpoint is created only through an explicit Context Boundary.

---

## 10. Structured Semantic Summary

The canonical semantic summary should use a structured schema rather than free-form Markdown.

Use the existing `ContextSummary` direction:

```json
{
  "schema_version": 1,
  "objective": "...",
  "constraints": [],
  "decisions": [],
  "facts": [],
  "open_items": [],
  "next_actions": [],
  "uncertainties": []
}
```

Each semantic entry may contain bounded provenance handles:

```text
run_id
file_id
block_id
```

These handles provide provenance for a bounded summary entry.

They are **not** a global growing reference catalog.

---

## 11. Structured Summary Must Be Universal

Current implementation is inconsistent:

```text
rebuild/history summary:
    structured only when capability_discovery == 1

parent-context compaction:
    free-form text

Work in-run compaction:
    free-form text
```

Target:

```text
Conversation rebuild compaction
Parent-context compaction
Work in-run compaction
```

all produce the same canonical semantic schema.

Context correctness must not depend on:

```text
capability_discovery
```

or another unrelated product feature flag.

---

## 12. One Logical Contract, Two Existing Persistence Scopes

Do **not** create a second compaction framework.

Use one semantic checkpoint contract with the existing persistence owners.

### Conversation scope

For completed historical turns and rebuild:

```text
Conversation.summary
ContextSummaryFrame
summary_through_run_id
```

This is the durable conversation semantic checkpoint.

### Active-run scope

For Work compaction inside a currently executing run:

```text
RunCheckpoint state
```

stores the structured in-run semantic summary and exact wire messages.

This avoids forcing unfinished tool/runtime state into the Conversation summary.

Both scopes use:

```text
same ContextSummary schema
same rendering contract
same source rules
same prefix-cache rules
```

They differ only in lifecycle/ownership.

---

## 13. Canonical Storage vs Model Rendering

Separate:

### Canonical storage

Structured JSON:

```text
ContextSummary
```

validated by code.

### Model-facing rendering

Use a deterministic renderer.

Recommended:

```text
<conversation_checkpoint>
{canonical compact JSON}
</conversation_checkpoint>
```

or an equivalent deterministic system message.

Do not use arbitrary model-generated Markdown as canonical state.

Benefits:

```text
schema validation
deterministic serialization
future migration
bounded fields
source validation
stable prefix bytes
```

---

## 14. Canonical Serialization

Prefix caching requires deterministic bytes.

Checkpoint JSON rendering must use a single serializer contract:

```text
fixed field order
fixed separators
no pretty-print variability
UTF-8
no random metadata
no timestamps unless semantically required
```

Do not deserialize and re-render the exact inherited provider messages on the fast path.

The canonical serializer is primarily for **new boundary construction**, not routine turns.

---

## 15. Semantic State vs Program-Owned Operational State

Do not ask the summary model to own execution correctness.

### 15.1 Semantic state

LLM-generated:

```text
objective
constraints
decisions
facts
open_items
next_actions
uncertainties
```

### 15.2 Operational state

Program-owned:

```text
pending tool calls
tool execution ledger
running exec handles
non-replayable execution fences
workspace snapshot state
workspace_dirty
pending/confirmed deliveries
active plan state
loaded runtime skills/modules
pending file/OCR dependencies
other state required to resume safely
```

Principle:

> **Semantic state can be lossy. Operational state cannot depend on semantic summarization.**

---

## 16. Operational State Rendering

Program-owned state can remain in `RunCheckpoint`, but the subset required by the model after a boundary must be deterministically rendered into context.

Example:

```text
<runtime_state>
active_background_jobs:
- handle: job_123
  status: running

confirmed_deliveries:
- report.xlsx -> /workspace/outputs/report.xlsx

plan:
- ...
</runtime_state>
```

Important:

- this block is generated by code;
- it is only replaced at an explicit Context Boundary;
- changes inside an epoch are appended as new messages/notices instead of rewriting it.

---

## 17. Current Operational-State Gap

Current code already stores useful state such as:

```text
background_jobs
tool_log
workspace
workspace_dirty
delivered
delivery_names
plan
pending
```

But after `_compact_work_context()` the model-facing messages do not have one formal deterministic rendering of all operational state.

This can cause a state to remain recoverable by the program while becoming hard for the model to reference.

Example:

```text
exec -> handle job_123
...many interactions...
compaction
```

If the original tool result is compressed away but `background_jobs=["job_123"]` remains only in runtime state, the program knows the process exists while the model may lose the handle needed for `exec_wait`.

Target fix:

```text
operational_context_message(state)
```

rendered at the new boundary.

---

## 18. Skill / Module State Is Operational Metadata

Current compaction appends `module_summary(...)` text to semantic summary messages.

With canonical structured JSON this must change.

Do not mutate:

```text
ContextSummary JSON
```

by concatenating free-form module text.

Instead:

```text
Structured Semantic Summary
+
Deterministic Capability / Module State Block
```

Example:

```text
<loaded_modules>
platform.docx/viewer/SKILL.md @ hash...
...
</loaded_modules>
```

This keeps:

```text
semantic schema valid
module visibility deterministic
prefix rendering stable
```

---

## 19. Recent Raw Complete Groups

After the Frozen Checkpoint retain recent raw interaction groups.

A tool protocol group is logically:

```text
Assistant tool_call
→ Tool result
→ next assistant continuation where relevant
```

Never create:

```text
orphan tool_result
dangling tool_call
```

Retention must operate on complete protocol groups, not arbitrary message count.

---

## 20. Current Barrier Behavior

Current main loop already has a useful property:

- `_compact_work_context()` is called only in the `not state["pending"]` branch;
- pending tool calls are executed and removed before the next model call;
- therefore ordinary main-loop Work compaction already occurs after the current tool batch has settled.

This is an **implicit safe point**.

Do not throw it away.

However it is not yet a complete protocol contract because:

```text
_compact_work_context() itself does not validate no dangling pairs
the safe-point rule is encoded indirectly in caller control flow
future callers can violate it
subsystems do not share one explicit barrier abstraction
```

Target: formalize the existing behavior.

---

## 21. Compaction Barrier

When context reaches the threshold:

```text
compaction_requested = true
```

The runtime enters a barrier.

```text
90% reached
    │
    ▼
Request Compaction
    │
    ▼
Freeze new model/tool progression
    │
    ▼
Drain current synchronous tool protocol
    │
    ▼
Persist tool results
    │
    ▼
Persist program-owned runtime state
    │
    ▼
Validate protocol closure
    │
    ▼
Compact
```

The barrier is coordinated by `AgentLoop`, because `AgentLoop` owns execution.

`ContextManager` must not become a second tool runtime.

---

## 22. Safe Compaction Point

Compaction is allowed only when all conditions hold:

```text
no model stream in flight
state.pending is empty
every visible tool_call has a matching tool_result
every visible tool_result has a matching tool_call
current tool events/results are checkpointed
non-replayable execution state is durably fenced
runtime state needed for resume is checkpointed
```

Provide an explicit validator:

```python
validate_safe_compaction_point(messages, runtime_state)
```

Failure is a programming/runtime error, not something the LLM repairs.

---

## 23. Tool Quiescence Does Not Mean Killing Background Jobs

A long-running exec may return:

```json
{
  "handle": "job_123",
  "running": true
}
```

The protocol:

```text
tool_call
→ tool_result
```

is closed.

The OS/background task may continue.

Therefore:

```text
Tool Protocol: CLOSED
Background Operation: PENDING
```

is a valid Safe Compaction Point.

Do **not** call `exec_kill_all` because of compaction.

Current code already kills background processes on pause/final cleanup, not as part of normal compaction. Preserve that separation.

---

## 24. Parallel Subtasks

Treat the parent `run_subtasks` invocation as one parent tool protocol unit.

```text
Parent tool_call(run_subtasks)
        ↓
  child A / B / C
        ↓
combined parent tool_result
        ↓
Safe Compaction Point
```

Parent context must not compact while the parent `run_subtasks` call is unresolved.

Current implementation gathers all child tasks before returning the parent result, which already gives a natural barrier.

Keep this behavior.

---

## 25. Normal Compaction Trigger

Set:

```text
threshold_ratio = 0.90
```

Formula:

```text
C = actual configured model context_tokens
T = floor(C * threshold_ratio)
```

Then:

```text
input < 90%
    → continue

input >= 90%
    → request compaction
```

---

## 26. 90 / 95 / 100 Responsibilities

```text
0% ─────────────────── 90% ───── 95% ───── 100%
                           │         │          │
                       compact     safety      hard
```

### 90%

Normal trigger.

### 95%

Safety/diagnostic zone.

If a request remains here after compaction attempts, likely causes include:

```text
protected context too large
compaction failed
large current attachment/input
token estimation error
large fixed tool/system prefix
```

Do not introduce a second summarization architecture at 95%.

### 100%

Hard model/provider limit.

Never cross by silently dropping required context.

---

## 27. 90% Migration Requirement

Current code still defaults to 0.85.

The change to 0.90 requires more than editing one constant.

Update:

```text
ContextSettings.threshold_ratio default
ModelPolicy fallback/default
threshold policy tests
admin UI/default display
architecture docs / ADR references
deployment acceptance tests
```

Also:

`PlatformSetting("context")` is persistent.

If an existing deployment already stores:

```json
{"threshold_ratio": 0.85}
```

changing the Pydantic/default code does **not** change that deployment.

Deployment migration must explicitly choose one:

```text
A. data migration 0.85 -> 0.90 when value is exactly the old platform default
or
B. deployment/admin API explicitly writes 0.90
```

Do not silently rewrite intentionally customized values.

---

## 28. Post-Compaction Target

Desired planning target:

```text
50–60%
```

But this is **best effort**, not a correctness invariant.

Why:

```text
Stable Prefix
Current User Turn
protected files/images
operational state
recent complete tool groups
```

may themselves exceed 60%.

Algorithm should choose enough complete old groups to compact so that the estimated result is likely to land around 50–60%.

After one valid compaction:

```text
<60%         ideal
60–90%       acceptable
>=90%        non-converging / unsafe → pause or reduce task scope
```

Do not repeatedly summarize solely to hit 60%.

---

## 29. Current Compaction-Cut Gap

Current `_compact_work_context()` roughly:

```text
keep the last two assistant-led groups
summarize everything older
accept result if below threshold
```

It does not explicitly plan for a 50–60% target.

Target algorithm:

1. identify protocol-complete groups;
2. protect current user turn and required runtime groups;
3. estimate fixed/protected token cost;
4. select the oldest continuous compactable prefix needed to aim for ~55%;
5. make one summary call;
6. validate output;
7. activate the new epoch;
8. if still >=90%, pause rather than loop.

---

## 30. Compaction Call and Prefix Cache

The compaction call itself should preserve the provider request prefix as much as possible.

Use:

```text
same model
same tools
same system layout
same reasoning/request-shape behavior
existing exact messages
+ append compaction instruction
```

Avoid switching to:

```text
tool_choice=none
different tool schema
different reasoning setting
different system prompt layout
```

when provider templating would destroy cache reuse.

After compaction, one boundary miss is expected.

Then the new epoch must again return to strict append-only extension.

---

## 31. Structured Compaction Pipeline

```text
Context >= 90%
      │
      ▼
Compaction Requested
      │
      ▼
Safe Compaction Barrier
      │
      ▼
Freeze input digest + compactable groups
      │
      ▼
Structured summary model call
      │
      ▼
Validate:
- valid ContextSummary JSON
- allowed source handles
- bounded size
- no protected operational state claimed incorrectly
      │
      ▼
Build deterministic runtime-state block
      │
      ▼
Persist semantic + runtime checkpoint
      │
      ▼
Activate next context_epoch
      │
      ▼
Resume append-only execution
```

If validation or persistence fails:

```text
old epoch remains authoritative
```

Do not partially activate a new context.

---

## 32. Source Validation

Structured summaries must only use source handles supplied to the summary call.

For conversation-level summary:

```text
ancestor run ids
authorized file ids
authorized block ids
```

For in-run Work summary:

```text
current run id
known current file/block handles
other explicitly supplied handles
```

The model cannot invent source IDs.

In-run summary may use the current `run_id` as a coarse source handle when no finer durable semantic source exists.

Do not make old `RunEvent` IDs mandatory provenance because RunEvents are retention-governed.

---

## 33. Current Turn Protection

The current user request remains raw during execution.

Do not absorb it into the semantic checkpoint while that turn is still active.

Protect:

```text
current user message
current task constraints
required current attachments/input
current requested output
```

Reason:

> **The current user message is the highest-priority source of truth for the active task.**

---

## 34. Large Tool Results

Bound tool output **before first insertion** into model history.

Use:

```text
bounded result/page/window
+
cursor / offset / handle
```

Never:

```text
insert huge output
later rewrite old tool result to a shorter version
```

because that destroys prefix stability.

The existing bounded read/output behavior should be retained.

---

## 35. Memory Integration

Memory follows:

```text
Snapshot at Context Boundary
+
append-only change notices inside the epoch
```

At epoch start:

```text
Memory Snapshot revision N
```

Freeze it.

If memory changes:

```text
append notice
```

At next compaction/rebuild:

```text
refresh snapshot
```

Do not mutate old messages inside an epoch.

---

## 36. Skills Integration

Keep skills lazy-loaded.

Stable platform prompt contains capability rules, not every skill body.

When required:

```text
Assistant → load_skill
Tool → skill/module content
```

Within an epoch, the returned skill/module content remains immutable.

On compaction:

- semantic summary does not absorb full skill bodies;
- deterministic module/capability state records what relevant modules were already shown;
- authoritative skill content can be reloaded from the skill store when actually needed.

---

## 37. Cross-Conversation Recall

The previous HLD was too broad in saying recall should simply disappear after compaction.

Current AILab already has an important security/correctness behavior:

```text
recall_history skips the current conversation
```

Therefore `recall_history` is already fundamentally:

```text
Cross-Conversation Recall
```

not a way to reload the compressed current branch.

Keep this property.

---

## 38. Recalled External History Must Not Become Permanent Local Truth

Current compaction intentionally avoids treating `recall_history` excerpts as ordinary durable facts.

This protects against:

```text
source deletion
visibility changes
project/account scope changes
stale external conversation state
```

Keep this distinction:

```text
Current-conversation facts:
    may be captured in structured checkpoint

Cross-conversation recalled excerpts:
    do not become permanent semantic facts solely because they were recalled
```

A checkpoint may record:

```text
"previously searched topic X"
```

without permanently copying the recalled excerpt.

If exact external history is required later, the recall path may revalidate/retrieve it under current authorization.

Because current conversation is excluded, this does **not** create the same-conversation compact → recall → compact loop.

---

## 39. Full History Is Not a Model Tool

Do not introduce general model tools such as:

```text
read_full_current_conversation
search_current_conversation
read_old_run_events
```

for normal compaction recovery.

Persistent data remains available internally for:

```text
UI
audit
debug
fork
rebuild
source lifecycle
migration
```

but the normal model view is:

```text
Checkpoint
+
Recent Tail
+
Current Turn
```

---

## 40. Fast Path

Normal next turn:

```text
create Run
   │
   ▼
load parent exact model context
   │
   ▼
valid model/tool/system/context ABI?
   │
 YES
   │
   ▼
reuse exact messages + exact tool catalog
   │
   ▼
append current user message
   │
   ▼
<90% ?
 ├─ yes → model
 └─ no  → safe compaction boundary
```

Never rebuild equivalent messages from DB on every turn.

---

## 41. Slow Rebuild Path

Rebuild only on:

```text
no usable parent checkpoint
legacy context layout
context ABI mismatch
model boundary
tool-catalog incompatibility
source/access invalidation
fork/edit ancestry change
checkpoint corruption
```

Rebuild uses:

```text
Conversation structured summary
+
valid history after summary cursor
+
current boundary context
```

The API remains owner of:

```text
ancestry
authorization
source lifecycle
summary CAS
history pagination
```

Do not duplicate those database rules in `ContextManager`.

---

## 42. Context ABI

Introduce:

```python
CONTEXT_ABI_VERSION = "2026-11-v1"
```

It versions the model-facing context contract:

```text
system-message layout
boundary-context layout
checkpoint serialization
operational-state rendering
tool ordering
request-shape assumptions
reasoning echo behavior
```

Store in RunCheckpoint state.

Fast path requires:

```text
parent.context_abi == current.context_abi
```

Otherwise:

```text
context_boundary = rebuilt
```

This is cleaner than accumulating many implicit compatibility heuristics.

Existing compatibility checks can remain temporarily for legacy migration.

---

## 43. Exact Wire Context Remains the Fast-Path Authority

For prefix cache, the most important persisted values remain:

```text
messages
tool_catalog_v1
```

These must represent the actual provider-facing context.

Do not:

```text
load semantic objects
re-render an equivalent conversation
expect provider cache to treat it as identical
```

when a valid exact previous wire context already exists.

Structured state is used to create **new boundaries and rebuilds**.

Exact wire messages are used for ordinary continuation.

---

## 44. RunCheckpoint Target State

Conceptually:

```json
{
  "context_abi": "2026-11-v1",
  "context_epoch": 13,

  "messages": [],
  "tool_catalog_v1": [],

  "semantic_checkpoint": {},
  "memory_observed": {},

  "delivered": {},
  "delivery_names": {},

  "background_jobs": [],
  "plan": [],
  "tool_log": {},
  "workspace": {},
  "workspace_dirty": false,

  "finished": false
}
```

Do not necessarily duplicate fields that are already present in the existing checkpoint state.

The goal is to define ownership, not create redundant storage.

---

## 45. ContextManager Responsibilities

Recommended interface:

```python
class ContextManager:
    async def build(...)
    def append(...)
    def should_compact(...)
    async def compact(...)
    def validate_safe_boundary(...)
    def snapshot(...)
```

Responsibilities:

```text
fast-path context reuse
slow-path rebuild
context epoch
semantic checkpoint generation
complete-group selection
token-budget planning
canonical rendering
boundary validation
```

Do not move sandbox/tool execution into this module.

---

## 46. AgentLoop Responsibilities

`AgentLoop` remains owner of execution:

```text
model calls
tool dispatch
tool durability
tool settlement
subtasks
sandbox
background jobs
workspace snapshots
previews
pause/final cleanup
```

It coordinates the Compaction Barrier.

Target:

```text
AgentLoop
    │
    ├─ Tool Runtime
    │
    └─ ContextManager
           ↑
       safe boundary
```

---

## 47. Suggested Runtime Flow

```python
ctx = await ContextManager.build(run, checkpoint)

while not finished:

    if not runtime.pending_tools:
        if ctx.should_compact():
            runtime_state = await runtime.freeze_for_compaction()

            ctx.validate_safe_boundary(runtime_state)

            await ctx.compact(
                runtime_state=runtime_state,
            )

        response = await call_model(
            messages=ctx.messages,
            tools=ctx.tools,
        )

        ctx.append(response)
        persist_checkpoint()

    while runtime.pending_tools:
        result = await execute_next_durable_tool()
        ctx.append(result)
        persist_checkpoint()
```

The actual code may remain organized differently; the protocol is what matters.

---

## 48. Current Conflicts / Gaps Found

The following are concrete differences between current `main` and this HLD.

| # | Current behavior | HLD target | Severity | Required action |
|---|---|---|---|---|
| 1 | Default compaction threshold is 0.85 | 0.90 | Medium | Change defaults, tests, docs and deployed PlatformSetting |
| 2 | Work/parent compaction produces free-form text | Structured `ContextSummary` | High | Unify summary contract |
| 3 | Structured history summary depends on `capability_discovery==1` | Structured always | High | Remove feature-flag coupling |
| 4 | Safe compaction is implicit through `state.pending == []` caller flow | Explicit barrier + validator | High | Add deterministic safe-boundary validation |
| 5 | `ContextSummaryFrame.revision` cannot represent in-run compaction epochs | Runtime `context_epoch` | High | Add checkpoint epoch field |
| 6 | Background-job handles are program state but lack one formal post-compaction rendering | Deterministic operational-state block | High | Render required runtime state at boundary |
| 7 | `module_summary()` is concatenated into summary text | Semantic JSON separate from deterministic module state | Medium | Split semantic/capability blocks |
| 8 | Current Work cut keeps recent groups but does not plan for 50–60% | ~55% best-effort planning target | Medium | Token-aware group selection |
| 9 | Current HLD previously assumed all raw execution history was permanent | Retention-aware durability model | High | Correct architecture wording and rebuild assumptions |
| 10 | Previous HLD proposed suppressing recall re-query too broadly | Preserve cross-conversation authorization/revalidation semantics | Medium | Keep current-conversation exclusion; preserve revalidation |
| 11 | Current compatibility is inferred from several message/layout checks | Explicit Context ABI | Medium | Add ABI and keep legacy checks during migration |
| 12 | Canonical structured checkpoint rendering is not defined byte-for-byte | Deterministic serializer | Medium | Add serialization regression tests |

---

## 49. Things Already Aligned With the HLD

Not everything needs rewriting.

Current implementation already has good properties:

```text
✓ exact previous_model_context reuse
✓ fixed per-run tool catalog
✓ prefix-cache regression tests
✓ model/tool request-shape awareness
✓ compaction called before a new model call
✓ main-loop compaction only when pending tool list is empty
✓ durable tool execution ledger
✓ non-replayable execution fencing
✓ background job handles persisted separately from messages
✓ parent run_subtasks result waits for child gather
✓ current conversation excluded from recall_history
✓ conversation summary cursor + CAS
✓ source invalidation / access-revocation handling
✓ memory snapshot + append-style change handling
✓ bounded tool-output direction
```

The implementation plan should preserve these.

---

## 50. Migration Strategy

Do not replace everything in one change.

### Phase 1 — Contracts and guards

Implement:

```text
Context ABI
context_epoch
90% threshold migration
SafeCompactionPoint validator
protocol-group parser
tests for dangling tool calls
```

Do not yet change summary format.

### Phase 2 — Structured summaries

Convert:

```text
_compact_parent_context
_compact_work_context
_assemble_context compaction path
```

to one structured semantic contract.

Keep existing exact messages behavior.

### Phase 3 — Operational rendering

Add deterministic boundary blocks for:

```text
background jobs
deliveries
plan
workspace continuation state
module/capability visibility
```

Remove free-form concatenation into semantic JSON.

### Phase 4 — ContextManager extraction

After behavior is stable, move context-specific logic out of `AgentLoop`.

This prevents a large refactor from changing semantics and cache behavior at the same time.

---

## 51. Prefix Cache Invariants

These become hard regression contracts.

### A. Message prefix extension

Inside an epoch:

```text
Request N.messages
```

is an exact prefix of:

```text
Request N+1.messages
```

### B. Tool stability

Inside an epoch:

```text
Request N.tools == Request N+1.tools
```

byte-equivalent under canonical request serialization.

### C. No historical rewrite

These only append inside an epoch:

```text
tool results
skill/module results
memory changes
plan updates
delivery notices
runtime notices
reasoning echoes
```

### D. Explicit prefix breaks only

Only:

```text
compacted
model_changed
context_abi_changed
access_revoked
rebuilt
```

may replace the prefix.

### E. Tool protocol closure

At every compaction boundary:

```text
no dangling tool_call
no orphan tool_result
```

### F. Compaction-call shape

Whenever supported by provider behavior:

```text
same model
same tools
same system layout
same reasoning mode
same prior messages
+ appended compaction instruction
```

---

## 52. Tests

Add or update the following.

### Threshold

```text
89.99% → no compact
90.00% → compact
```

Test both:

```text
default config
persisted PlatformSetting
```

### Prefix continuity

```text
Chat 10 turns
Work 20 model/tool steps
```

below threshold:

```text
strict request extension
```

### Boundary recovery

After compaction:

```text
first request establishes new epoch
next 5 requests strictly extend it
```

### Dangling protocol

```text
assistant has unresolved tool_call
→ compaction rejected
```

```text
orphan tool_result
→ compaction rejected
```

### Background exec

```text
exec returns running handle
→ tool pair closed
→ handle persisted
→ compaction allowed
→ model still receives handle after boundary
```

### Subtasks

```text
run_subtasks unresolved
→ no parent compaction

combined result settled
→ parent compaction allowed
```

### Structured summary

All three paths:

```text
rebuild
parent
Work
```

must return valid `ContextSummary`.

Invalid JSON:

```text
does not advance boundary
does not replace prior epoch
```

### Source validation

Model-generated unknown source handle:

```text
reject summary
```

### Retention independence

Simulate old `RunEvent` deletion.

Conversation continuation/rebuild must still work from:

```text
Conversation/Run
Structured Checkpoint
valid recent state
```

### Operational state

After compaction ensure the model can still see:

```text
active exec handle
confirmed deliveries
current plan
required workspace continuation information
```

### Module state

Structured JSON remains parseable; module metadata is rendered separately and deterministically.

### Context ABI

```text
matching ABI → exact reuse
different ABI → rebuilt boundary
```

---

## 53. Metrics

Keep:

```text
prompt_tokens
cached_tokens
```

Add:

```text
context_compactions_total
context_compaction_failures_total{reason}
context_rebuilds_total{reason}
context_epoch
context_input_tokens
context_checkpoint_tokens
context_recent_tokens
context_operational_tokens
context_barrier_wait_seconds
context_barrier_pending_tools
```

Primary cache metric:

```text
cache_ratio = cached_prompt_tokens / prompt_tokens
```

Target:

```text
normal multi-turn Chat / Work >= 95%
```

Do not use:

```text
user_id
conversation_id
run_id
```

as Prometheus labels.

Important:

```text
90% = compaction trigger
95% = desired cached-token ratio
```

They are unrelated metrics.

---

## 54. Non-Goals

Do not add:

```text
Recall Controller
current-conversation RAG
Vector DB for compaction
Reference Graph
Artifact Graph
multi-level summary trees
rolling summary every turn
background autonomous context rewriting
```

The Context Core remains:

```text
stable prefix
+
frozen structured checkpoint
+
program-owned operational state
+
recent raw groups
+
current turn
```

---

## 55. Final Lifecycle

```text
                    Durable Conversation Record
            ┌──────────────────────────────────────────┐
            │ Conversation / Runs                      │
            │ Structured Conversation Summary          │
            │ Durable RunProcessSnapshot (UI only)     │
            │ RunCheckpoint runtime state              │
            │ RunEvents (retention-governed)           │
            │ Files / workspace (own lifecycles)       │
            └──────────────────┬───────────────────────┘
                               │
                         rebuild when needed
                               │
                               ▼
┌────────────────────────────────────────────────────────────┐
│                       CONTEXT EPOCH                        │
│                                                            │
│ Stable Prefix                                              │
│ ─────────────────────────────────────────────────────────  │
│ Frozen Structured Semantic Checkpoint                      │
│ ─────────────────────────────────────────────────────────  │
│ Deterministic Operational / Capability State               │
│ ─────────────────────────────────────────────────────────  │
│ Recent Raw Complete Protocol Groups                        │
│ ─────────────────────────────────────────────────────────  │
│ Current Turn                                               │
│                                                            │
└──────────────────────────────┬─────────────────────────────┘
                               │
                               ▼
                              LLM
                               │
                          append only
                               │
                               ▼
                       Context >= 90% ?
                         │           │
                        NO          YES
                         │           │
                      continue       ▼
                           Compaction Requested
                                    │
                           Safe Compaction Barrier
                                    │
                           pending tools == empty
                                    │
                         validate no dangling pairs
                                    │
                           persist runtime state
                                    │
                       structured semantic summary
                                    │
                         deterministic state render
                                    │
                           activate next epoch
                                    │
                       aim for 50–60% best effort
```

---

## 56. Final Architecture Rules

1. **Use existing durable Conversation/Run storage; do not create another Full History Store.**
2. **Treat RunEvents as retention-governed live/debug traces, not permanent semantic memory or permanent UI history.**
4. **Materialize a durable `RunProcessSnapshot` for user-visible historical thinking/work process before RunEvents become eligible for GC.**
3. **Use one structured semantic summary contract for conversation and Work compaction.**
5. **Keep active-run operational correctness in program-owned checkpoint state.**
6. **Render required operational state deterministically after a compaction boundary.**
7. **Never compact across an unresolved tool-call protocol.**
8. **Do not kill healthy background jobs merely to compact context.**
9. **Inside one Context Epoch, previous model-facing bytes are append-only.**
10. **Use exact inherited wire messages on the fast path; structured state is for boundaries/rebuilds.**
11. **Trigger normal compaction at 90%; use 50–60% only as a best-effort target.**
12. **Keep 95% as a safety zone, not another compaction algorithm.**
13. **Keep current-conversation history out of `recall_history`; preserve cross-conversation authorization/revalidation semantics.**
14. **Version the complete model-facing layout with a Context ABI.**
15. **A failed compaction never replaces the previous authoritative epoch.**

This design preserves the strongest parts of the current AILab implementation while making context compression explicit, structured, tool-safe, retention-aware and prefix-cache-friendly.

# 57. Multi-Tenant & Concurrency Guarantees

AILab is a concurrent multi-user platform. Context management is therefore a **tenant-isolated, run-scoped protocol**, not shared mutable process state.

Core rule:

> **Cross-user sharing is allowed only for immutable identical platform-prefix bytes. User, conversation, run, memory, skill, file, checkpoint and runtime state are never shared as mutable context.**

## 57.1 Isolation hierarchy

```text
Platform
│
├── User A
│   ├── Conversation A1
│   │   ├── Run A1-10
│   │   └── Run A1-11
│   └── Conversation A2
│
├── User B
│   └── Conversation B1
│
└── User C
    └── Conversation C1
```

Every active model context belongs to one concrete run/branch. The useful identity for a context generation is:

```text
(run_id, context_epoch)
```

`context_epoch` is run-local, not globally unique.

```text
Run A: epoch 0 → 1 → 2
Run B: epoch 0 → 1
Run C: epoch 0 → 1 → 2 → 3
```

---

# 58. ContextManager Scope in a Multi-User Platform

`ContextManager` must not be a global mutable singleton holding current user/conversation state.

Forbidden:

```python
shared_context_manager.current_user = ...
shared_context_manager.messages = ...
shared_context_manager.current_conversation = ...
```

Preferred:

```python
ctx = await ContextManager.build(
    run_id=run.id,
    conversation_id=run.conversation_id,
    checkpoint=checkpoint,
)
```

or a mostly stateless manager with explicit state arguments.

Hard rule:

> **No mutable ContextManager state may be shared across users, conversations, runs or branches.**

Worker memory is only an execution cache. Persistent platform storage remains authoritative.

---

# 59. Persistent Storage Remains Authoritative Across Workers

AILab may have multiple API replicas and multiple agent workers.

```text
Worker 1 starts Run X
        ↓
Worker 1 crashes
        ↓
Worker 3 resumes Run X
```

Therefore these cannot exist only in process memory:

```text
context_epoch
exact provider-facing messages
tool_catalog_v1
pending tool state
semantic checkpoint
memory-observed state
workspace continuation state
background job handles
delivery state
plan/runtime state
```

Authoritative recovery continues to rely on existing durable platform state such as:

```text
RunCheckpoint
Conversation
Run
ContextSummaryFrame
Files / workspace lifecycle state
```

Structures such as `self._states[run_id]` remain transient execution caches.

---

# 60. Cross-User Prefix Cache Sharing

Prefix caching must distinguish **byte sharing** from **state sharing**.

## 60.1 Immutable platform bytes may be shared

If requests use the same:

```text
model capability profile
mode
PLATFORM_PROMPT_VERSION
CONTEXT_ABI_VERSION
tool catalog
provider request shape
```

then the platform prefix should ideally be byte-identical:

```text
User A ─┐
User B ─┼──→ same immutable Platform Prefix bytes
User C ─┘
```

This is safe only because user-specific data is absent from this reusable prefix.

## 60.2 Conversation state is never shared

Always tenant/conversation scoped:

```text
memory
project instructions
conversation checkpoint
recent messages
current turn
personal skills
current files
runtime state
deliveries
plan
background jobs
```

Intended boundary:

```text
GLOBAL / PREFIX-FAMILY STABLE
─────────────────────────────
Platform System
Tool Catalog / Request Shape

TENANT / CONVERSATION SPECIFIC
─────────────────────────────
Memory / Project Boundary Context
Frozen Conversation Checkpoint
Operational State
Recent Messages
Current Turn
```

---

# 61. Prefix Families

Not all users necessarily expose the same tools.

Example:

```text
User A: Files + GitHub
User B: Files
User C: Files + GitHub + Jira
```

Therefore Level-1 cache reuse is organized into **prefix families**, not one universal prefix.

Recommended identity:

```text
PrefixFamilyKey =
    model/profile
  + mode
  + PLATFORM_PROMPT_VERSION
  + CONTEXT_ABI_VERSION
  + tool_catalog_fingerprint
  + provider_request_shape_fingerprint
```

Users in the same family can share identical prefix bytes. Different authorization/tool sets naturally form different families.

> **Never weaken authorization or expose extra tools merely to improve cache hit rate.**

Security and correctness outrank cache reuse.

---

# 62. Tool Catalog Fingerprint

The frozen tool catalog is part of provider request identity.

At run bootstrap:

```text
tool_catalog_v1
```

is fixed. Derive a deterministic fingerprint from its canonical serialized representation:

```text
tool_catalog_fingerprint = hash(canonical_tool_catalog)
```

Canonicalization requires:

```text
stable tool ordering
stable JSON field ordering
stable descriptions/schema serialization
no random ids
no user-specific metadata unless authorization genuinely changes the catalog
```

The fingerprint is useful for compatibility/cache diagnostics, but **is not authorization itself**.

---

# 63. Concurrent Run Safety

Two workers must never advance the same run checkpoint with last-write-wins behavior.

Continue using optimistic concurrency:

```text
read checkpoint step N
        ↓
compute next state
        ↓
write expected_step = N
        ↓
 success → N+1 active
 conflict → stale result discarded / reload
```

This applies to:

```text
tool settlement
context compaction
context_epoch advance
runtime-state update
resume/recovery
```

A compaction generated from stale checkpoint state must never become authoritative.

---

# 64. Conversation-Level Summary Concurrency

Conversation summary has a different concurrency domain from an active Run.

Continue using guarded advancement based on existing concepts such as:

```text
summary_through_run_id
source_epoch
ancestor validation
expected prior cursor/frame
```

Conceptually:

```text
Conversation Checkpoint V7
          │
   Worker A compacts
          │
     CAS succeeds
          ▼
Conversation Checkpoint V8
```

A second candidate generated from stale V7:

```text
CAS fails
→ discard candidate
→ reload current summary
```

No last-write-wins conversation compaction.

---

# 65. Run Epoch and Conversation Summary Revision Are Separate

Keep these concepts distinct:

```text
RunCheckpoint.context_epoch
```

tracks model-prefix replacement events for the active run.

```text
ContextSummaryFrame.revision
```

tracks persistent conversation-summary revisions.

They can advance independently.

Example:

```text
Conversation summary revision = 8

Run X:
epoch 0
epoch 1  # in-run Work compact
epoch 2  # second in-run compact

Conversation revision may still be 8.
```

---

# 66. Fork / Edit / Retry Isolation

Branches may reuse immutable ancestors but never mutable descendants.

```text
            Run 10
           /      \
      Run 11A    Run 11B
```

Both can reuse the immutable exact prefix of Run 10.

After divergence:

```text
Run 11A → own Context Epoch chain
Run 11B → own Context Epoch chain
```

Rule:

> **Share immutable ancestor bytes; never share mutable descendant state.**

The same rule applies to edit, retry and alternate branches.

---

# 67. Memory / Skills / Files Tenant Isolation

## Memory

Memory is user/project scoped and must never enter the globally reusable platform prefix.

Correct layout:

```text
Platform System              # globally stable
Boundary Context             # user/project scoped
  ├── Memory snapshot
  ├── Project instructions
  └── Conversation state
```

## Personal skills

Personal skills are tenant-owned state. A skill loaded by User A must never be considered loaded for User B solely because the skill name matches.

Built-in skill package bytes may be shared, but loaded-state/history remains tied to the concrete run.

## Files / project data

Current files, project libraries, workspace state and attachments are tenant/run scoped.

Rebuild must revalidate:

```text
user authorization
project membership
file/source lifecycle
source revocation
branch ancestry
```

before including them in a new checkpoint.

---

# 68. Multi-Worker Safe Compaction

A Safe Compaction Point has two dimensions.

## Protocol safety

```text
state.pending is empty
no dangling tool_call/tool_result pair
runtime state is checkpointable
```

## Concurrency safety

```text
current attempt still owns the run
expected checkpoint step is still current
no newer checkpoint has committed
conversation summary cursor/source epoch still matches when applicable
```

Activation flow:

```text
read authoritative checkpoint
        ↓
reach protocol-safe point
        ↓
freeze compaction input
        ↓
generate structured summary
        ↓
validate summary/runtime state
        ↓
CAS checkpoint / conversation summary
        │
   ┌────┴────┐
 success   conflict
   │          │
activate    discard stale result
new epoch   reload authoritative state
```

> **An LLM-generated summary is not authoritative until the durable concurrency check succeeds.**

---

# 69. Attempt Ownership

Retries/recovery must use the existing run-attempt ownership contract.

A stale worker that lost ownership must not:

```text
append messages
settle tools
advance context_epoch
activate a compacted checkpoint
```

`ContextManager` must not invent a second independent lock/ownership system. It relies on the platform runtime's existing attempt/checkpoint authority.

---

# 70. Multi-Tenant Metrics

Never use high-cardinality identifiers as Prometheus labels:

```text
user_id
conversation_id
run_id
project_id
file_id
context_epoch numeric value
```

Use bounded technical dimensions:

```text
provider
model
mode
boundary_reason
rebuild_reason
compaction_result
context ABI
prefix/tool-catalog family class
```

`run_id` / `context_epoch` can appear in logs/traces for debugging, but not as unbounded metric labels.

Primary cache metric remains:

```text
cache_ratio = cached_prompt_tokens / prompt_tokens
```

Analyze it by bounded prefix-family dimensions.

---

# 71. Multi-Tenant Security Invariants

Add hard invariants/tests:

```text
A. User A memory/checkpoint/messages never appear in User B request.
B. Conversation A checkpoint never becomes Conversation B checkpoint.
C. Run A operational state cannot mutate Run B state.
D. Cross-user prefix sharing contains no tenant-specific bytes.
E. Same tool name does not imply same authorization.
F. Rebuild revalidates file/source access.
G. A stale worker cannot commit over a newer checkpoint.
H. Fork siblings share immutable ancestors only.
```

---

# 72. Multi-Tenant Test Matrix

## Same prefix family, different users

```text
same model/mode/platform/tools
different user memory

→ platform prefix identical
→ boundary context different
→ no leakage
```

## Different tool permissions

```text
User A = Files
User B = Files + GitHub

→ different tool_catalog_fingerprint
→ different prefix family
```

## Concurrent checkpoint writers

```text
Worker A reads step 5
Worker B reads step 5
B commits step 6
A attempts stale write

→ A rejected
```

## Concurrent conversation compaction

```text
two candidates from same summary cursor
→ only one CAS succeeds
```

## Worker crash/resume

```text
Worker A dies after durable checkpoint
Worker B resumes
→ exact wire context + context_epoch restored
```

## Fork isolation

```text
two children reuse immutable parent prefix
→ future compactions evolve independently
```

## Memory isolation

```text
same prefix family
users A/B have different memory
→ same global prefix
→ distinct boundary context
```

## Personal skill isolation

```text
same skill name under two users
different file/version
→ no carried-skill contamination
```

---

# 73. Updated ContextManager Contract

With multi-tenancy included:

```python
class ContextManager:
    """No cross-run mutable state; persistent platform state is authoritative."""

    @classmethod
    async def build(
        cls,
        *,
        run_id,
        conversation_id,
        checkpoint,
        ...,
    ):
        ...

    def append(self, message):
        ...

    def should_compact(self):
        ...

    async def compact(self, *, runtime_state, expected_checkpoint_step):
        ...

    def validate_safe_boundary(self, runtime_state):
        ...

    def snapshot(self):
        ...
```

Exact API names may differ, but the ownership/isolation guarantees must remain.

---

# 74. Updated Platform Architecture

```text
                         MULTI-TENANT AILAB
                                 │
                ┌────────────────┴────────────────┐
                │                                 │
       Immutable Prefix Families          Tenant-Owned State
                │                                 │
     ┌──────────┴──────────┐          ┌──────────┴──────────┐
     │ Platform Prompt     │          │ User / Project      │
     │ Tool Catalog        │          │ Conversation        │
     │ Context ABI         │          │ RunCheckpoint       │
     │ Request Shape       │          │ Files / Workspace   │
     └──────────┬──────────┘          └──────────┬──────────┘
                │                                │
                └──────────────┬─────────────────┘
                               │
                               ▼
                      Run-scoped ContextManager
                               │
                               ▼
┌───────────────────────────────────────────────────────────┐
│                     CONTEXT EPOCH                         │
│                                                           │
│ Stable Prefix Family                                      │
│ ────────────────────────────────────────────────────────  │
│ Tenant / Conversation Boundary Context                    │
│ ────────────────────────────────────────────────────────  │
│ Frozen Structured Semantic Checkpoint                     │
│ ────────────────────────────────────────────────────────  │
│ Deterministic Operational / Capability State              │
│ ────────────────────────────────────────────────────────  │
│ Recent Raw Complete Protocol Groups                       │
│ ────────────────────────────────────────────────────────  │
│ Current Turn                                              │
└─────────────────────────────┬─────────────────────────────┘
                              │
                              ▼
                             LLM
                              │
                         append only
                              │
                              ▼
                    Safe Compaction Boundary
                              │
               protocol safe + concurrency safe
                              │
                              ▼
                    CAS / expected-step commit
                              │
                    ┌─────────┴─────────┐
                    │                   │
                 success             conflict
                    │                   │
              new epoch active    discard / reload
```

---

# 75. Additional Final Rules for AILab as a Platform

15. **ContextManager is run-scoped or stateless across runs; never a tenant-shared mutable singleton.**
16. **Persistent storage and optimistic concurrency remain authoritative across workers.**
17. **Cross-user prefix reuse means identical immutable bytes, never shared mutable context state.**
18. **Prefix families are keyed by model/mode/prompt ABI/tool-catalog/request-shape compatibility.**
19. **Different authorization/tool sets may reduce cache sharing; never weaken permissions for cache hit rate.**
20. **Forks share immutable ancestors only; descendants have independent context epochs.**
21. **Run-level `context_epoch` and conversation-level summary revision are separate concurrency domains.**
22. **Compaction activation requires both protocol safety and successful CAS/ownership validation.**
23. **Memory, personal skills, files, project data, workspace and runtime state remain tenant-isolated.**
24. **Metrics aggregate by bounded technical dimensions, never tenant/run identifiers.**

With these guarantees, the Context architecture is suitable for AILab as a concurrent multi-user platform with multiple workers, retries, forks, per-user capabilities and provider prefix caching.

# 76. Durable Process Snapshot

AILab exposes thinking and work-process history as a user-facing product feature. That history must survive the retention lifecycle of raw execution events.

Introduce a durable per-run product record:

```text
RunProcessSnapshot
```

Its purpose is narrow:

> **Preserve the user-visible historical work/thinking process after `RunEvent` has expired.**

It is **not**:

```text
model context
conversation memory
compaction checkpoint
runtime recovery state
audit/debug event log
```

This separation is mandatory.

---

# 77. Three Different Histories

The platform now has three deliberately different representations.

## 77.1 Model continuation history

```text
Structured Context Checkpoint
+
Recent Raw Groups
+
Current Turn
```

Used only by the model/agent.

Optimized for:

```text
correct continuation
bounded context
prefix cache
```

## 77.2 User-visible process history

```text
RunProcessSnapshot
```

Used only by Portal/history UI.

Optimized for:

```text
thinking display
tool/work steps
plans
asks/steers
public narration
long-term historical rendering
```

## 77.3 Raw execution trace

```text
RunEvent
```

Used for:

```text
live streaming
debugging
short/medium-term replay
operational diagnostics
```

Retention remains governed by:

```text
RUN_EVENTS_RETENTION_DAYS
```

These representations must not be substituted for each other.

---

# 78. Process Snapshot Data Model

Do **not** store the full snapshot inside `Run.result`.

Reason:

`Run.result` participates in normal run/conversation payloads, while the process body may be large. Historical process data should only be fetched when the user opens the process UI.

Recommended persistence:

```text
run_process_snapshots
```

Conceptual schema:

```text
run_id               PK / FK -> runs.id ON DELETE CASCADE
schema_version        integer
through_seq           integer
items                 JSON / JSONB
created_at            timestamp
updated_at            timestamp
```

Optional bounded metadata:

```text
item_count
uncompressed_bytes
```

`items` uses a stable **ProcessView** schema equivalent to the normalized model already consumed by `WorkProcess`.

Conceptually:

```json
{
  "schema_version": 1,
  "items": [
    {
      "kind": "thinking",
      "step": 1,
      "text": "...full user-visible thinking text...",
      "seconds": 12.3
    },
    {
      "kind": "steps",
      "steps": [
        {
          "name": "web_search",
          "label": "搜索网页",
          "status": "ok",
          "detail": "...",
          "sources": []
        }
      ]
    }
  ]
}
```

The exact schema may evolve, but must be versioned independently from `RunEvent`.

---

# 79. What the Snapshot Keeps

Persist what the user can actually inspect in the historical WorkProcess UI.

Keep:

```text
full user-visible thinking sections
normalized tool/work steps
user-visible tool outputs that WorkProcess exposes
plan state/changes needed for the displayed timeline
ask-user prompts and answers
steer markers
public process narration
step timing/status needed by UI
source links shown in process rows
```

Do not persist merely because an event existed.

Drop from the durable process representation when not required for the product UI:

```text
answer streaming deltas already represented by Run.summary
heartbeat events
checkpoint bookkeeping
lease events
watchdog internals
context-boundary debug events
quota accounting internals
raw high-frequency status chatter
provider transport details
internal tool bookkeeping
```

Streaming chunks should be coalesced into their final user-visible representation.

Example:

```text
80 agent.thinking chunks
        ↓
1 thinking ProcessView item
```

This is why Process Snapshot is materially smaller and more stable than retaining all raw RunEvents forever.

---

# 80. Snapshot Materialization Lifecycle

The snapshot is materialized at a durable execution boundary.

Primary boundaries:

```text
succeeded
failed
canceled
interrupted / durable pause
```

`waiting_user` does not require immediate final materialization because the run is still live and its events are not terminal-GC candidates; it may still be materialized opportunistically if useful.

Normal lifecycle:

```text
Run executes
    │
    ├── RunEvent → live Portal SSE
    │
    ▼
Durable stop / terminal boundary
    │
    ▼
Materialize ProcessViewV1
    │
    ▼
Persist RunProcessSnapshot
    │
    ▼
RunEvent remains available during retention window
    │
    ▼
Retention expires
    │
    ▼
Raw RunEvents may be GC'd
    │
    ▼
Historical UI still reads RunProcessSnapshot
```

If an interrupted run later resumes, its snapshot is replaced/upserted at the next durable stop with a newer `through_seq`.

---

# 81. Snapshot Must Be Materialized Before Event GC

This is a hard durability rule.

Raw process events must not be deleted if the platform has promised historical process UI but no complete durable snapshot exists.

GC eligibility becomes conceptually:

```text
terminal run
AND event retention expired
AND process snapshot exists
AND snapshot covers required user-visible process events
```

At minimum:

```text
snapshot.through_seq >= required_process_seq
```

where `required_process_seq` is the highest sequence that contributes to the user-visible process representation.

If snapshot generation failed:

```text
retain events
retry materialization later
```

Never:

```text
snapshot failed
→ delete raw events anyway
→ historical process permanently lost
```

A bounded background repair/materialization pass should scan stopped runs that are missing a valid snapshot.

---

# 82. Snapshot Is Product Data, RunEvent Is Runtime Data

Once a complete snapshot exists, it becomes the long-term authority for the historical process UI.

Recommended read behavior:

```text
ACTIVE RUN
    SSE / current RunEvents

STOPPED RUN + snapshot available
    RunProcessSnapshot

STOPPED RUN + snapshot temporarily missing
    RunEvents fallback
    + schedule/retry materialization
```

This is preferable to making Portal behavior depend on whether the current date happens to fall before or after a 30-day GC cutoff.

The user should see the same process representation before and after raw event deletion.

---

# 83. Process Snapshot API

Add a product-level process endpoint instead of exposing storage details to Portal.

Recommended:

```text
GET /v1/runs/{run_id}/process
```

Response concept:

```json
{
  "schema_version": 1,
  "source": "snapshot",
  "items": []
}
```

For an active run, live items continue to arrive through SSE.

For a stopped run:

```text
/process
→ durable snapshot when available
→ event-derived fallback only while snapshot is missing
```

Portal should not need to know:

```text
whether raw events have expired
what RUN_EVENTS_RETENTION_DAYS is
how the snapshot was materialized
```

The existing `/events/history` endpoint can remain for:

```text
debugging
testing
operational inspection
legacy compatibility
```

but it should no longer be the permanent historical WorkProcess contract.

---

# 84. UI Behavior After Event Retention

Today, if old `RunEvent` rows disappear, Portal may receive an empty event history and show no historical process even though the final answer still exists.

Target behavior:

```text
User opens a 6-month-old conversation
        │
        ▼
Run.summary → final answer
        │
        └── /runs/{id}/process
                  │
                  ▼
          RunProcessSnapshot
                  │
                  ▼
       historical WorkProcess renders normally
```

The UI must not report an error merely because raw RunEvents have expired.

If no process was ever produced, an empty snapshot may legitimately render:

```text
没有可显示的过程
```

This is different from data loss.

---

# 85. Relationship to `Run.result.thinking`

Keep the existing bounded:

```text
Run.result.thinking
```

as lightweight run metadata/fallback if it remains useful.

It is not the authoritative historical thinking store.

Authority becomes:

```text
Live run:
    agent.thinking events

Historical stopped run:
    RunProcessSnapshot
```

Do not enlarge `Run.result.thinking` into the complete thinking transcript; that would inflate ordinary conversation/run payloads.

---

# 86. Prefix Cache Isolation

`RunProcessSnapshot` must have **zero effect** on model request construction.

Never insert it into:

```text
Stable Prefix
Conversation Checkpoint
Recent Raw Tail
Current Turn
Memory
Context rebuild
```

Therefore:

```text
Process Snapshot creation
Process Snapshot schema migration
RunEvent GC
historical UI fetches
```

must not change prefix-cache bytes.

This keeps the model context architecture and UI durability architecture independent.

---

# 87. Multi-Tenant Process Isolation

A Process Snapshot is tenant-owned data through its Run.

Hard requirements:

```text
snapshot.run_id must resolve to the authorized user's Run
fork/retry creates its own snapshot for the new Run
snapshot data is never reused across users
snapshot endpoint enforces the same run visibility rules as run detail
conversation purge cascades/deletes snapshot
```

Cross-user prefix sharing has no relationship to Process Snapshot sharing.

---

# 88. Fork / Retry Semantics

Do not share mutable Process Snapshots between branches.

For a fork/retry:

```text
source Run snapshot
    remains source Run product history

new Run
    produces its own process snapshot
```

If product requirements want a fork UI to show inherited historical process, that should be represented by the conversation branch UI, not by copying or mutating the source run's process snapshot as execution state.

---

# 89. Storage and Scale Characteristics

The snapshot is expected to use materially less database/storage overhead than indefinite raw events because it removes:

```text
per-event row/index overhead
streaming answer deltas
internal lifecycle/debug events
repeated status events
redundant thinking chunks
runtime-only bookkeeping
```

It also makes long-term storage proportional to **user-visible process content**, rather than execution-event frequency.

Still enforce reasonable product bounds for pathological runs.

If one snapshot becomes very large, the implementation may store the serialized ProcessView in compressed object storage with DB metadata/pointer, without changing the API or HLD contract.

Do not prematurely introduce chunk graphs or hierarchical process archives.

---

# 90. Process Snapshot Versioning

Process Snapshot needs its own version:

```text
PROCESS_SNAPSHOT_SCHEMA_VERSION = 1
```

This is independent from:

```text
CONTEXT_ABI_VERSION
ContextSummary.schema_version
RunEvent event schema
```

Why:

```text
Context ABI       → model/prefix compatibility
ContextSummary    → semantic checkpoint compatibility
Process Snapshot  → historical UI compatibility
RunEvent          → runtime event compatibility
```

Do not tie these lifecycles together.

---

# 91. Process Snapshot Tests

Add tests for:

## Complete thinking preservation

```text
many agent.thinking chunks
→ one/more normalized thinking item(s)
→ full visible text preserved
```

## Tool process preservation

```text
tool call/result sequence
→ normalized WorkProcess step
```

## Event GC independence

```text
create completed run
materialize snapshot
delete RunEvents
open process API
→ same historical process still available
```

## GC guard

```text
snapshot materialization fails
→ events older than retention are NOT purged
```

## Resume

```text
interrupted snapshot through seq N
resume run
new events N+1...
stop again
→ snapshot updated and through_seq advances
```

## Tenant isolation

```text
User B cannot read User A snapshot
```

## Fork/retry

```text
child run gets independent snapshot lifecycle
```

## Prefix cache independence

```text
create/update/read process snapshot
→ exact model request prefix unchanged
```

## Payload isolation

```text
conversation/run list responses do not embed full snapshot body
```

---

# 92. Updated Durability Model

The final durability model is:

```text
Durable Product State
├── Conversation / Run                # user conversation/result
├── ContextSummary                    # model continuation
├── RunProcessSnapshot                # historical UI process
├── Files / Artifacts                 # product assets
└── required metadata

Durable Runtime / Recovery State
└── RunCheckpoint                     # execution/resume/prefix continuity

Retention-Governed Execution Trace
└── RunEvent                          # live streaming/debug/recent replay
```

This is the intended separation of concerns.

---

# 93. Updated Final Rule Set

In addition to the previous Context and multi-tenant rules:

25. **Historical WorkProcess UI must not depend on raw RunEvents remaining forever.**
26. **Materialize `RunProcessSnapshot` at durable stop/terminal boundaries.**
27. **Do not put full Process Snapshot data inside `Run.result` or ordinary conversation-list payloads.**
28. **Do not GC required historical process events until a valid snapshot safely covers them.**
29. **For stopped runs, Process Snapshot is the long-term product authority for thinking/work-process rendering.**
30. **RunEvent remains the live/debug trace and may retain the existing 30-day lifecycle.**
31. **Process Snapshot is never fed back into model context and has zero influence on prefix-cache construction.**
32. **Process Snapshot schema/versioning is independent from Context ABI and ContextSummary.**
33. **Process Snapshots are tenant/run scoped and follow run/conversation deletion and authorization.**

The final system therefore preserves both goals without conflating them:

```text
Model continuity
→ Structured Context Checkpoint

Historical user-visible work process
→ Durable RunProcessSnapshot

Live execution / debugging
→ retention-governed RunEvent
```

