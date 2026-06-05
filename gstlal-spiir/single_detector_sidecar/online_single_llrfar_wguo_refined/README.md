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

Quick checks:

```bash
bash -n *.sh
python3 -m py_compile *.py
python3 -m unittest tests/test_engineering_flow_contract.py
```
