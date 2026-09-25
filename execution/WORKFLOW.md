# Execution workflow

This harness turns the policy in [REWRITE.md](../REWRITE.md) into bounded work
that an agent can finish and another agent can resume. The user's current
instructions take precedence. These files add no permission checkpoints.

## Start with the actual state

1. Read AGENTS.md, the rewrite policy, STATUS.md, the assigned phase/task, and
   VALIDATION.md. Check relevant log entries and decisions.
2. Inspect repository status, existing changes, source/tests for the feature,
   available tools, and prior evidence. Preserve user and other-agent work.
3. Verify prerequisites rather than trusting a phase label. Do not redo completed
   work unless new evidence or changes invalidate it.
4. Record a short plan with outputs, boundaries, dependencies, and concrete
   acceptance checks. Use the plan template for substantial work; a small task
   can use its brief and handoff. Planning is the beginning of implementation.

Plans live under `execution/plans/` when created. Handoffs live under
`execution/handoffs/`. Name files by phase/task ID. No empty placeholder plans
are required. Store durable evidence under `validation/` as established in P00;
keep bulky generated outputs outside version control and link manifests.

## Execute through the acceptance checks

Implement the smallest coherent vertical slice, exercising real interfaces and
storage. Write meaningful tests for new behavior and discovered regressions.
Capture an oracle result before replacing a behavior where feasible. Prefer
short feedback loops over completing a subsystem before running anything.

Run focused checks, repair failures caused by the work, then run the relevant
integration suite. Once those pass, broaden testing only for an unresolved risk
or the phase's exit requirements. Do not repeatedly rerun an unchanged passing
suite to appear busy. Use [VALIDATION.md](VALIDATION.md) for comparison decisions.

Update COVERAGE.md and link actual evidence. Record discoveries while working,
not just in the final response. Finish with a handoff and STATUS.md update.
Continue through all authorized deliverables; do not yield after a plan, a
partial stub, or a request to confirm an obvious next step.

Work on the assigned phase(s), not an unbounded rewrite. When multiple phases
are authorized, advance when their prerequisites are met without asking again.
When a single phase is complete, deliver its result and identify the next phase.

## Decisions and clarification

| Situation | Action |
| --- | --- |
| Reversible implementation detail, dependency choice, test organization, internal type or module layout | Use the policy, inspect existing conventions, make a reasonable choice, and continue. Record a decision only if it materially shapes later work. |
| Existing behavior is ambiguous | Inspect Python code/tests, reproduce it against the reference, and record what was learned. Ask only if evidence cannot resolve a material product choice. |
| Explained numerical difference with no visible or material semantic impact | Apply the agreed tolerance policy, retain evidence and a regression bound, log it, and continue. No separate confirmation is needed. |
| Missing preference with a safe reversible default | State the assumption, optionally ask asynchronously, and continue independent work. If no reply arrives, use the stated default only when the answer is optional. |
| Material scope conflict, incompatible public behavior, unclear destructive action, unavailable credential/account choice | Ask a concise specific question with evidence, a recommended option, and its consequences. Keep dependent work pending while advancing independent tasks. |
| Tool permission or external-action authorization is required | Follow the actual tool/session policy. Prepare a concrete reviewable result first where possible; identify the action and why permission is required. Do not add a second permission step after authorization. |

Do not ask which framework or crate to use merely to avoid engineering judgment.
Do not repeatedly ask a settled question or ask whether to run tests, fix your
regression, or complete an assigned adapter. An unanswered required question is
not approval, and waiting longer does not change that.

## Navigate blockers without stalling

A blocker is a missing prerequisite, authority, or external condition that
prevents a specific outcome. A failing test or a difficult implementation is
normally work to do, not a reason to stop.

1. Capture the exact failure, command, environment, and affected acceptance check.
   Classify it as implementation, reference/environment, tooling/access, or a
   product decision. Do not expose secrets in the record.
2. Reduce it and try plausible remedies. Change a relevant condition with each
   retry; avoid loops through the same failure. Inspect existing logs before
   repeating an investigation.
3. If an investigation consumes roughly an hour without new evidence, summarize
   what is known and switch to a bounded experiment or independent task. This is
   a reassessment trigger, not a rule to abandon difficult necessary work.
4. Use safe alternatives that preserve the claim: isolated dependencies, a local
   database service, cached real-raster subsets, or a local range-capable HTTP server.
   Label what these cannot validate. A mock cannot stand in for a real-service
   acceptance check.
5. Record the missing condition, attempted remedies, exact resume step, and
   independent work still available. Ask for the smallest missing input if needed.
6. Leave the dependent task blocked or partially verified. Continue useful work.
   End only when the assignment is complete or no authorized work can progress;
   explain the remaining blocker plainly in the handoff.

If automatic approval review rejects an action, identify the rejected action and
stated reason when reporting it. Use a safer authorized route if available;
do not relabel a rejected action or treat a policy file as permission to bypass it.

Never disable a test, widen all tolerances, fabricate a passing result, or alter
the reference implementation just to clear a blocker. Missing network, hardware,
or cloud access should produce an explicit validation gap, not false confidence.

## Ownership and handoffs

Use one owner for each active task and its shared interfaces. When delegation is
authorized, assign independent bounded slices with explicit file/interface
ownership and an integration owner. Avoid simultaneous changes to workspace
manifests, shared types, reference fixtures, or the same state/log rows. Independent
branches/worktrees are useful when available; do not reset someone else's work.

Subtasks being complete does not make their integration complete. The integration
owner runs cross-boundary checks and reconciles evidence. A review agent reports
findings; it does not silently change the baseline or compatibility policy.

When interrupted, leave the tree understandable and record unfinished changes,
last commands/results, active processes, and the exact next action. A new session
must inspect this state rather than restart the phase from scratch.

## Progress accounting

Phase states: `not-started`, `in-progress`, `blocked`, `complete`. Tasks within a
phase may have independent blockers. Mark a phase `blocked` only when it cannot
advance; otherwise keep it `in-progress` and list the blocked exit check.

Coverage implementation and verification are separate. A feature can be
implemented but unverified; a test can pass locally while cloud/platform evidence
is pending. `complete` requires all phase exit checks or explicitly recorded
scope changes. An accepted deviation is evidence-backed compatibility, not an
unimplemented feature. Preserve blockers in the release inventory until resolved.

No commits, pushes, releases, or cloud deployments are implied by writing a plan.
Follow the assignment's authorization for those actions; ordinary local edits and
verification within the assigned scope should proceed autonomously.
