# Eric-super-spiir online single-detector sidecar

This directory contains the runnable online single-detector FAR sidecar for the
Eric-super-spiir branch.

Engineering contract:

- The coherent SPIIR pipeline remains the main search path.
- The sidecar starts from main-pipeline zerolag snapshots.
- Each worker owns exactly one numbered bank group.  To run groups `000-005`,
  submit six workers/nodes.
- The initial background accumulation window is not assigned final FAR.
- Later trigger windows are assigned with the latest prior frozen background.
- `assigned_far` is the fitted/interpolated, append-only ledger value.
- `calculated_far` is the direct formula value kept for diagnostics.
- Four-panel summaries use linear SNR axes, linear chi-square axes, and
  `log10(FAR)` only in the color scale.

O3a BNS online-frontier background:

`run_o3a_bns_online_frontier_bg.sh` builds a frozen O3a BNS background without
showing a seven-day future horizon to one pipeline process.  It submits one
worker over sequential 24-hour chunks, waits for each chunk to finish, then uses
that chunk's coherent stats as the external multi-detector background seed for
the next chunk.  After all chunks complete, the script aggregates the zerolag
snapshots, builds the single-detector background JSON, and fans out the final
multi-detector stats for later two-worker injection runs.

This controller is intentionally no-injection:

- `WGUO_O3A_INJECTION_MODE=none`
- `WGUO_O3A_INJECTION_FILE=` is empty
- injection-trigger outputs must not be used to accumulate background

For online injection validation, preserve the same frontier rule: future
`sim_inspiral` rows must not be visible.  Expose injection rows chunk by chunk
and assign FAR with frozen no-injection single and multi-detector backgrounds.

Typical launch:

```bash
tmux new-session -s codex4
./run_o3a_bns_online_frontier_bg.sh
```

Useful overrides include `ROOT_DIR`, `START_GPS`, `NUM_CHUNKS`,
`CHUNK_SECONDS`, `BANKS_PER_GROUP`, `INITIAL_MULTI_STATS_LOC`, and
`POLL_SECONDS`.

Quick checks:

```bash
bash -n *.sh
python3 -m py_compile *.py
python3 -m unittest tests/test_engineering_flow_contract.py
```
