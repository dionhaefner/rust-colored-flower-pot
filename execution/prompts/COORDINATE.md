# Multi-phase coordinator brief

Use this when the user assigns a range of phases or a broader implementation
outcome. Agent delegation remains subject to the active session's authorization
and tool rules; this file does not require spawning agents.

---

Own delivery of the assigned Terracotta rewrite phases, from current state to
validated outcomes. Read AGENTS.md, the policy, workflow, phase roadmap,
validation rules, status, coverage, discoveries, and relevant decisions.

Inspect the actual code/evidence and dependency graph. Turn the assigned scope
into bounded tasks using execution/TEMPLATES.md. Each task must have an owner,
source context, concrete output, interface/file ownership, and acceptance checks.
Reuse execution/prompts/IMPLEMENT.md as the common instruction and add the
specific task context. Do not send another agent only a vague request to "port
the drivers" or assume it remembers this conversation.

When delegation is authorized and useful, parallelize independent work. Keep
shared types, workspace manifests, oracle captures, and schema decisions under
clear ownership. Provide downstream agents the actual interface contract and
required predecessor evidence. Do not launch dependent work against invented
APIs or concurrently mutate the same files without coordination.

Continue useful integration work while tasks run. Reconcile discoveries and
interface changes promptly. Reassign or reduce tasks when evidence changes the
plan; preserve completed work. Investigate blockers using WORKFLOW.md and ask
the user only for missing decisions or authority that cannot be inferred.

Integrate outputs and run cross-boundary checks yourself. A collection of green
unit suites does not prove an end-to-end workflow. Use REVIEW.md for a bounded
independent review when available and authorized; otherwise perform a focused
self-review and describe that honestly. Resolve material findings before marking
the relevant phase complete.

Keep STATUS.md current, with task ownership and evidence links. Do not mark a
phase complete while required external checks or material mismatches remain.
Advance through authorized phases without repeatedly requesting confirmation.
End at completion of the assigned scope, or after documenting a genuine blocker
when no independent authorized work remains. Leave one coherent final handoff.
