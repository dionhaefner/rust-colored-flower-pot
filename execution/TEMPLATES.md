# Reusable execution records

Keep records proportional to the work. A task brief plus evidence and handoff is
enough for a small change; do not create five documents for every function.
Replace placeholders when assigning work. No template represents completed work.

## Task brief / phase plan

```text
ID / phase / owner:
Outcome in user-visible terms:
Assignment boundary (which phase(s) or bounded task):
Required context (source files, docs, decisions, RW IDs):
Prerequisites and evidence they hold:
Files/interfaces owned; integration owner if working with others:
Deliverables:
Implementation steps (short, revisable):
Acceptance checks with commands or a plan to create those commands:
Reference inputs / expected behavior:
Known uncertainties and optional assumptions:
Required external resources or authorization (if any):
Stop/handback condition:
```

## Evidence record

```text
Run ID / date / phase / coverage IDs:
Reference source commit and environment manifest:
Rust commit plus dirty-tree patch/checksum, or complete source snapshot:
Fixture/input checksums and settings:
Real source asset/version, subset window/bands/recipe, and native/derived properties:
Corpus tier; for stress cases, file bytes/decoded size/TIFF offsets and memory budget:
Exact working directory, commands, exit codes:
Passed checks:
Failed checks with reproduction:
Skipped/not-run checks with reasons and next action:
Artifacts and retention/reproduction instructions:
For visual comparisons: ordinary viewing scale, samples inspected, observed impact:
For numerical deviations: cause, semantic impact, narrow regression bound, RW ID:
For performance: resources, cache state, repetitions, metrics and limitations:
Claim this evidence supports; claims it does not support:
```

## Blocker / required question

```text
ID / affected task and acceptance check:
Observed failure (exact command/error, redacted where necessary):
Classification: implementation / reference-environment / tool-access / product choice
Evidence and remedies already attempted:
Smallest missing condition/input/authorization:
Recommended option and consequence; meaningful alternatives if needed:
Dependent work left pending:
Independent work continuing now:
Exact resume action after the condition clears:
```

Do not ask open-ended questions like "How should I proceed?" when evidence
supports a concrete recommendation. If a decision is already authorized, use it.

## Handoff

```text
Task / phase / owner:
Outcome and implemented scope:
Changed files / interface decisions:
Validation: actual commands and evidence links, with pass/fail/skip distinctions:
Coverage rows and RW/decision IDs updated:
Known limitations and unresolved checks:
Uncommitted changes / active processes / artifacts another agent must preserve:
Phase state and why:
Exact next action, required context, and prerequisites:
```

## Review findings

```text
Reviewed revision/patch and assigned scope:
Checks performed and evidence inspected:
Finding: severity, file/case, concrete failure or risk, reproduction, suggested fix
Compatibility consequence and relevant coverage/RW ID:
Missing evidence (separate from demonstrated implementation defects):
Verdict for the assigned scope: ready / changes needed / evidence incomplete
```

"No findings" means none found within the reviewed scope and evidence; it is
not a substitute for unperformed release qualification.
