# Single-Detector Engineering Flow

This note records the design contract for the `Eric-super-spiir` code/runtime
workflow.  The project split is deliberately simple:

- `Eric-super-spiir` owns code, scripts, configuration, and directly runnable
  OzSTAR workflow pieces.
- `Eric-bless-spiir` is the PDF note used to record design intent, run
  commands, monitor semantics, and debugging decisions.

`results/run_*` directories are execution outputs from the runnable code, not a
separate core package.  The branch-level goal is not to replace the coherent
SPIIR online search.  It adds an opt-in single-detector sidecar that observes
postcoh products, preserves detector-local rows, and lets the offline/online FAR
assignment tools build a rolling single-detector ledger.

## End-to-End Numbered Flow

Read this section first.  It is the whole engineering story in chronological
order, from raw data selection to the final monitor plot.

```text
frame cache + GPS + banks
  -> main SPIIR pipeline
  -> matched filtering and coherent postcoh
  -> zerolag XML snapshots
  -> single-detector feature extraction
  -> worker-local background and FAR assignment
  -> merged FAR ledger, monitor JSON, and four-panel plot
```

1. **Identify the input data and freeze the run configuration.**
   Choose the frame cache, GPS start/end, H1/L1 strain channels, H1/L1 state
   channels, IIR bank directory, detector-response map, and coherent
   background/statistics paths.  The submitted job freezes the values it
   actually used in `logs/run_config_<jobid>.env`.  This frozen file is the
   first truth surface for debugging.
2. **Inject or replay that data into the main SPIIR pipeline.**
   `batch_submit.sh` and `submit.sh` start the OzSTAR job, then
   `run_bank_group_worker.sh` calls `pipeline.sh`.  `pipeline.sh` launches the
   standard SPIIR online command over the selected frame-cache data.  This is
   still the main coherent SPIIR pipeline; the single-detector branch does not
   replace it.  The current runtime front-end is the Python-3 WGuo-compatible
   path, `SPIIR_BUILD_NAME=wguo-single-det-py3` with
   `SPIIR_RUN_FUNCTION=run_spiir_py3`, so the zerolag handoff is produced by
   the same front-end family used for WGuo reference comparisons.
3. **Run the main SPIIR matched-filter and coherent postcoh path.**
   The main pipeline loads the IIR banks, whitens the H1/L1 streams, performs
   matched filtering, forms detector-local quantities such as `snglsnr` and
   `chisq`, and then passes those streams through the normal coherent postcoh
   and `cohfar` machinery.  When this Python-3 front-end is selected, the
   submitted workflow also creates run-local WGuo-compatible copies of the
   current split-bank XML files before the workers start.
4. **Write zerolag XML snapshots from the main pipeline.**
   FinalSink periodically writes zerolag snapshots under numbered bank-group
   directories, such as `000/000_zerolag_<gps_start>_<duration>.xml.gz` and
   `001/001_zerolag_<gps_start>_<duration>.xml.gz`.  These zerolag files are
   the handoff from the main SPIIR pipeline to the single-detector sidecar.

   **Important boundary:** for the single-detector branch, zerolag only becomes
   an input after the main H1+L1 SPIIR pipeline has both detectors running.  If
   the beginning of a replay has only H or only L available, that detector-only
   startup interval is not written into the zerolag surface used by the sidecar.
   The sidecar can preserve H-only and L-only detector-local rows only after
   this H/L joint-start boundary has been crossed.

5. **Extract detector-local features from zerolag into the sidecar.**
   `extract_zerolag_features.py` reads the current worker's zerolag XML files
   and writes `single_branch/worker_<id>/zerolag_features.csv`.  The important
   columns are detector/IFO, time, bank/template id, SNR, chi-square, source
   kind, and whether the row is used as background or foreground.  This is the
   point where H-only and L-only rows that actually reached postcoh are
   preserved for the single-detector study.

   On the single-detector side, H1 and L1 should then be understood as separated
   detector-local streams.  If a zerolag/postcoh row carries both detectors, the
   sidecar peels it into one H1 feature row and one L1 feature row.  From that
   point onward, those rows enter the single-detector branch independently: the
   rank, background support, and FAR assignment are computed for each
   detector-local row, not for an H-L pair relation.
6. **Build a worker-local single-detector background.**
   Each node/worker owns one numbered bank group, not a stride over many groups.
   With two nodes, the run covers only two groups, for example worker 0 handles
   `000` and worker 1 handles `001`.  A run over groups `000`-`005` therefore
   needs six nodes/workers.  `BANKS_PER_GROUP` is the number of template banks
   inside one numbered group; it is not the number of groups assigned to one
   node.  Each worker builds its own rolling background from its own visible
   features, using the configured `BACKGROUND_ACCUMULATION_SECONDS`.  The
   formal single-detector FAR contract uses `10800` seconds, i.e. three hours.
   Shorter windows are developer-debug products only and must require
   `ALLOW_SHORT_BACKGROUND_DEBUG=1`; they must not be mistaken for formal
   background or FAR products.  The background files should always be checked
   with two timing quantities: the accumulation window and the update trigger.
   The first says how much visible history is needed to form the support; the
   second, configured by `BACKGROUND_UPDATE_TRIGGER_SECONDS`, says when a new
   visible interval should refresh that support.
7. **Assign single-detector FAR in an append-only ledger.**
   `assign_frozen_far_ledger.py` must assign FAR using the latest available
   background products for that worker.  The trigger row receiving FAR must be
   later than the background support used to calibrate it; the assignment step
   should not score a trigger against a background that already contains that
   same trigger or a later one.  Triggers that were only used to accumulate the
   initial background are calibration support, not final FAR events, because
   their FAR would be biased by the incomplete bootstrap background.

   Keep two FAR quantities separate:

   - **Assigned FAR** is the final value frozen into the ledger.  It comes from
     the fitted or interpolated LLR-FAR background curve.
   - **Calculated FAR** is the direct value from the FAR formula.  It is useful
     as a formula-level diagnostic or comparison, but it should not be confused
     with the fitted assigned FAR.

   The first full background window is special.  Before the full three-hour
   support exists, there is no valid background file, LLR-FAR support curve, or
   calculated FAR.  After `BG-000` has been built from `[0,3) h`, the rows
   inside that first window may have diagnostic calculated FAR values against
   `BG-000`, but they still must not receive assigned FAR because they are the
   support used to build `BG-000`.  Assigned FAR starts only for later triggers
   whose times are outside the background support used for assignment.

   After a valid background exists, newly visible later trigger rows are
   appended to `single_branch/worker_<id>/single_final_far_all.csv`.  Trigger
   keys that are already present are counted as duplicates and left unchanged,
   so a later background refresh can add new rows but should not rewrite old
   assigned FAR values.

   Rank convention: each detector-local trigger first gets its own LLR/rank.
   The ordering step is then performed over the full BG accumulation window,
   not over a single snapshot or update tick.  For example, a three-hour
   `BG-000` sorts and tail-counts all background trigger ranks collected in
   `[0,3) h`.  This ranking domain is still worker-local and IFO-local:
   worker 0 and worker 1 do not share rank samples, and H1 and L1 are sorted in
   separate background distributions.  A target trigger's calculated FAR is
   obtained by comparing that target trigger's own rank to the matching
   worker-local, IFO-local background rank distribution, for example
   `N_bg(rank >= r*) / T_bg`.
8. **Merge ledgers and draw the monitor summary.**
   `merge_worker_far_ledgers.py` combines the frozen worker ledgers into
   `single_branch/single_final_far_all.csv`.  This is only bookkeeping; it
   should not recompute FAR globally across workers.  After that,
   `realtime_single_monitor.py` and `monitor_run_table.py` read compact
   JSON/status files such as `monitor/pipeline_progress.json` and
   `monitor/latest_single_background_status.json`.  The four-panel summary uses
   the merged single-detector FAR ledger, the coherent/zerolag products, and a
   current worker background.  SNR axes are linear, chi-square/chisq/cmbchisq
   axes are linear, and only FAR is shown as `log10(FAR)` in the color scale.

## Example Background/FAR Timing Table

This is the concrete timing contract for the single-detector sidecar.  Let
`0 h` denote the first injected/zerolag time after the H/L joint-start
boundary.  The example uses an accumulation window `A = 3 h` and update trigger
`U = 1 h`, corresponding to `BACKGROUND_ACCUMULATION_SECONDS=10800` and
`BACKGROUND_UPDATE_TRIGGER_SECONDS=3600`.  In a real run, first define `0 h`
from the frozen start point in `logs/run_config_<jobid>.env`, then fill the
table with run-relative `h` values and the configured accumulation/update
values.  The pipeline should not create a ten-minute formal background; any
short-window artifact is a developer-debug product and should not be treated as
the engineering contract.

`BG-000`, `BG-001`, ... are logical background versions for one worker.  If the
implementation keeps only the current
`single_branch/worker_<id>/single_far_llr_background.json`, the same version,
support range, and refresh time still need to be recoverable from the worker
status JSON or an archived copy.  The **Assign BG ID** column names the BG
version used when writing the fitted assigned FAR for the visible trigger
interval; if a calculated FAR is also recorded, it should be the direct formula
value evaluated against the same prior BG version.

| Trigger time now visible | Calculated FAR | Assign BG ID | Assigned FAR and next BG |
| --- | --- | --- | --- |
| Triggers in `[0,3) h`. | None until the full three-hour `BG-000` exists.  After that, only diagnostic in-background calculated FAR may be reported. | None yet. | These rows accumulate the first background only.  At `3 h`, write `BG-000` with support `[0,3) h`.  Do not write assigned FAR to these bootstrap triggers. |
| Triggers in `[3,4) h`. | Use `BG-000`. | `BG-000`. | Write assigned FAR only for the later triggers in `[3,4) h`.  Then refresh the next background as `BG-001` with support `[1,4) h`. |
| Triggers in `[4,5) h`. | Use `BG-001`. | `BG-001`. | Write assigned FAR for `[4,5) h`.  Then write `BG-002` with support `[2,5) h`. |
| Triggers in `[5,6) h`. | Use `BG-002`. | `BG-002`. | Write assigned FAR for `[5,6) h`.  Then write `BG-003` with support `[3,6) h`. |

The important rule is: for each target trigger, use the newest background whose
support ends before that trigger interval.  The first triggers used to build
`BG-000` are background support, not final FAR events.  A later background that
already contains a target trigger must not be used to score that same trigger.
Everything else is bookkeeping: `PIPELINE_MODE=multi` keeps the original
coherent path, `PIPELINE_MODE=single` enables the sidecar, and monitors should
prefer compact JSON/status summaries over repeated full CSV scans.
