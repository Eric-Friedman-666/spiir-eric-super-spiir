# SPIIR Sidecar and Crashcar Project Instructions

These instructions apply to this SPIIR validation project, especially work involving
the sidecar and crashcar pipelines on OzSTAR.

## Core Requirements

- Treat sidecar and crashcar as paired implementations of the same online
  validation problem. A crashcar test is not accepted until the corresponding
  sidecar test has also been run with the same effective configuration and the
  results agree within numerical precision.
- The running program must be the latest intended program version. Do not use
  superseded worktrees, old copied wrappers, canceled run roots, or stale
  installed binaries unless the user explicitly asks for forensic comparison.
- Before launching any real run, verify package provenance: git branch, git
  commit, remote head, tracked dirty status, and the exact runtime copied into
  the run root.
- Keep online behavior real. Future data and future injection rows must not be
  visible to the pipeline. When simulating online operation, expose data and
  injection XML rows chunk by chunk.
- Injection runs must not accumulate single-detector background from injection
  triggers. Use a no-injection background first, freeze it, and then assign FAR
  in the injection run from that frozen background.
- Both single-detector and multi/coherent background products must be saved and
  traceable.

## Crashcar Standard Workflow

The standard way to run crashcar is:

1. Adjust `scripts/crashcar.env`.
2. Run `bash scripts/crashcar.sh`.
3. Watch the job long enough to confirm it has entered the pipeline and is
   writing expected files.
4. Set up an hourly monitor for any continuing run.
5. On every monitor check, confirm both file production and numerical outputs
   look normal.

Do not bypass `scripts/crashcar.sh` with ad hoc commands unless debugging a
minimal isolated failure. If a wrapper or run-root-only patch is used for
debugging, record it clearly and do not treat it as the formal package until the
fix is moved into the package and verified.

## `crashcar.env` Policy

- Prefer changing only values in `scripts/crashcar.env`.
- Do not add, remove, or rename env variables unless it is unavoidable.
- If an env variable change is unavoidable, report the reason before relying on
  it for formal results.
- Preserve the existing variable names. Downstream scripts should be adjusted to
  the env contract, not the other way around.
- Keep `BG_update_hour` and `zerolag_update_hour` separate. `BG_update_hour`
  controls background/FAR-background update cadence; `zerolag_update_hour` is a
  common parameter above `injection_mode` and controls zerolag file snapshot and
  landing cadence.
- For `injection_mode=False`, run from the normal O3 data fields in the
  non-injection block.
- For `injection_mode=True`, common parameters above the mode switch still apply,
  but injection/background data selection must come from the `#inject` block:
  `injection_bg_*` for frozen no-injection background accumulation and
  `injection_*` for the injection run.
- The foreground `injection_*` run block should mirror the normal O3 data block
  shape, plus the required `injection_file`. Do not expose foreground
  background-accumulation, background-update, chunking, or SNR-threshold knobs
  in the user-facing env; those are internal defaults unless the user explicitly
  asks to surface them.
- In injection mode, first run a no-injection BG stage. Then freeze the single
  BG JSON and the multi/coherent BG stats. The injection stage must read those
  frozen BG products and must not accumulate a new background from injection
  triggers.
- Do not use an external seed or fallback directory as the frozen multi/coherent
  background for injection. The frozen multi stats must come from the matching
  no-injection BG stage in the current workflow; if they are missing, fail
  clearly instead of borrowing old stats.

## Crashcar Engineering Flow Contract

For crashcar, verify that the engineering flow matches the intended online
pipeline:

- Single-detector branch computes as close to zero latency as possible at the
  finalsink side.
- Single-detector FAR assignment is written back together with the multi branch
  into zerolag outputs.
- Triggers passing the configured FAR/SNR-series threshold produce SNR series.
- Crashcar detail/feature rows are exported and remain consistent with the
  final zerolag products.
- Frozen-background injection runs show `single_background_mode=frozen` and
  point to the no-injection BG JSON.
- Injection chunks set the background requirement only for export/assignment as
  intended, not to accumulate injection background.

## Sidecar Parity Requirement

Every crashcar smoke test must have a matching sidecar smoke test using the same
effective configuration:

- Same GPS interval and duration.
- Same detector response file and segment XML.
- Same data cache.
- Same bank split and worker count, unless the test explicitly isolates worker
  behavior.
- Same injection XML filtering/online frontier when `injection_mode=True`.
- Same frozen no-injection single and multi/coherent background sources for
  injection tests.

Acceptance requires comparing sidecar and crashcar outputs:

- Trigger counts per worker and detector.
- Zerolag file counts and event counts.
- Marginalized stats file counts and source paths.
- Single-detector LLR values.
- Single-detector FAR/logFAR values.
- Multi/coherent ranking/FAR outputs.
- SNR-series manifest rows and selected event identities when SNR series are
  expected.

Numerical differences must be explained and bounded. Do not call a parity smoke
test passed if the differences are only visually inspected or if only one branch
was checked.

## Monitoring Requirements

For a continuing crashcar or sidecar run, create an hourly monitor after the run
has visibly started. Each monitor report must include:

- Server, tmux session, working directory, model/role, and task.
- Slurm state, queue reason, node, elapsed time, and exit code if complete.
- Current controller phase/status JSON.
- Latest `.out`, `.err`, and controller-log evidence if blocked or failed.
- Counts of `*_zerolag_*.xml*`.
- Counts and paths of `*_marginalized_stats_*.xml*`.
- Single detail/feature CSV existence and row counts.
- SNR-series manifest existence, row counts, and archive status when expected.
- Frozen BG source paths for injection runs.
- Whether single BG is rolling/no-injection or frozen/injection-assignment-only.
- Whether multi/coherent BG comes from the frozen no-injection source, not from
  the injection run.
- A short statement on whether the files and numerical outputs look normal.

If plots are part of the run, update them during the monitor and return the
latest figures. A plot is supporting evidence only; it does not replace file and
numeric checks.

## Required Debugging Behavior

- Debug minimally and one issue at a time.
- Prefer run-root-only patches for diagnosis. Move fixes into the source package
  only after the failure mode is understood.
- After a code fix, rerun the relevant smoke test from a fresh run root.
- If crashcar changes, rerun sidecar parity smoke as well.
- Do not accept a run because Slurm completed. Accept only after final reports,
  output counts, frozen-background provenance, and sidecar/crashcar parity have
  been verified.
- If a check script or temporary driver reports failure due to checking the
  wrong artifact path, verify the correct staged reports manually and then fix
  the checker before reusing it.

## Reporting Format

When reporting a run, include:

- Verdict: queued, running, blocked, failed, retried, or complete.
- Exact run root.
- Git branch and commit.
- Slurm job IDs and states.
- Background mode and frozen BG paths.
- Zerolag/stats/detail/SNR-series counts.
- Sidecar/crashcar parity summary.
- Any fixes made, including whether `crashcar.env` variables were changed.

Be explicit when a result is from a temporary smoke configuration rather than
the formal `scripts/crashcar.env`.

## Never Do These

- Do not use injection triggers to accumulate background.
- Do not run a formal result from stale source or a stale runtime binary.
- Do not silently add, remove, or rename `crashcar.env` variables.
- Do not claim a monitor will continue after the active session ends unless a
  real watchdog, cron job, service, or automation has been created.
- Do not treat old canceled/interrupted runs as canonical background or
  injection output.
- Do not declare sidecar/crashcar agreement without checking both single and
  multi outputs.
