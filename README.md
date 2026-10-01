# acer-adapter-hello

Test adapter for Acer Worker v1.1 (workload_id `hello`). It computes a number
sequence step by step and exercises the whole adapter contract: params with an
enum and numbers, `progress.jsonl`, graceful cancel, `summary.json` with fields
and FILE outputs. No network, no secrets, no domain logic.

## Files

- `acer-manifest.json`: the manifest template. The installer sets `venv` and
  replaces `{adapter_dir}` with the installed source folder.
- `hello/run.py`: the adapter (`python -E -s hello\run.py <job.json>`).
- `requirements.lock.txt`: `tabulate==0.9.0`, pinned by wheel sha256.
- `tests/test_run.py`: local tests (stdlib unittest).

## Params

| name | type | values |
|---|---|---|
| `pattern` | string | `linear`, `squares`, `fibonacci`, `primes` (from 1.1.0) |
| `steps` | integer | 1-600 |
| `step_seconds` | number | 0-5 |

Defaults: linear, 30 steps, 1 s per step. Limits: `max_runtime_s` 3600,
`cancel_grace_s` 10, `graceful_cancel` true.

## Behaviour

- Writes one progress line per step.
- Checks `cancel.requested` every 0.1 s. On cancel it writes the partial result,
  prints `cancel honoured` and exits 0.
- Outputs: `outputs\result.csv` (step,value) and `outputs\report.txt`.
- Summary fields: pattern, steps done, last value, elapsed time.
- Exit 2 for an invalid job.json.

## Install on the Acer (admin only)

Installed only with Acer Worker's `ops\install-adapter.ps1`, run elevated and
pinned to a full commit SHA. Details are in `docs/v11-plan.md` in acer-worker.

## Local tests

    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --require-hashes --no-deps --only-binary=:all: -r requirements.lock.txt
    .venv\Scripts\python.exe -m unittest discover -s tests -v
