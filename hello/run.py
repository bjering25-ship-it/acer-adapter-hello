"""hello - Acer Worker test adapter (v1.1).

Usage (by the worker):  python -E -s hello\\run.py <job.json>

Computes a number sequence (linear, squares, fibonacci) one step at a time:
  - one progress.jsonl line per step (progress, current/total, stage, message)
  - checks the cancel file between steps (and while waiting): on cancel it
    writes the partial result, prints "cancel honoured" and exits 0
  - outputs\\result.csv (step,value) and outputs\\report.txt (tabulate)
  - summary.json: message, 4 fields, FILE references

No network, no secrets, no child processes. Exit codes: 0 done or
cancelled, 2 invalid job.json.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tabulate import tabulate


PATTERNS = ("linear", "squares", "fibonacci")
POLL_S = 0.1


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sequence(pattern):
    """Yields (step, value) for step = 1, 2, ..."""
    step, a, b = 0, 0, 1
    while True:
        step += 1
        if pattern == "linear":
            yield step, step
        elif pattern == "squares":
            yield step, step * step
        else:
            a, b = b, a + b
            yield step, a


def write_json_atomic(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def cancelled(cancel_file):
    return cancel_file is not None and cancel_file.exists()


def wait(seconds, cancel_file):
    """Sleeps up to `seconds`; returns True as soon as cancel is requested."""
    deadline = time.monotonic() + seconds
    while True:
        if cancelled(cancel_file):
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(POLL_S, left))


def write_results(job_dir, outputs, pattern, rows, steps, elapsed, was_cancelled):
    with open(outputs / "result.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["step", "value"])
        writer.writerows(rows)

    report = tabulate(rows, headers=["step", "value"], tablefmt="github")
    (outputs / "report.txt").write_text(
        f"hello: pattern={pattern}, {len(rows)} of {steps} steps"
        f"{' (cancelled)' if was_cancelled else ''}\n\n{report}\n", encoding="utf-8")

    last = str(rows[-1][1]) if rows else "-"
    message = (f"Cancelled after {len(rows)} of {steps} steps" if was_cancelled
               else f"Computed {steps} {pattern} steps")
    write_json_atomic(job_dir / "summary.json", {
        "message": message,
        "fields": [
            {"label": "Pattern", "value": pattern},
            {"label": "Steps done", "value": len(rows)},
            {"label": "Last value", "value": last[:200]},
            {"label": "Elapsed (s)", "value": round(elapsed, 1)},
        ],
        "outputs": [
            {"type": "FILE", "path": "result.csv", "label": "Result (CSV)"},
            {"type": "FILE", "path": "report.txt", "label": "Report"},
        ],
    })


def load_spec(path):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    params = spec["params"]
    pattern, steps, step_seconds = params["pattern"], params["steps"], params["step_seconds"]
    if pattern not in PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}")
    if type(steps) is not int or steps < 1:
        raise ValueError(f"steps must be a positive integer, got {steps!r}")
    if isinstance(step_seconds, bool) or not isinstance(step_seconds, (int, float)) or step_seconds < 0:
        raise ValueError(f"step_seconds must be a number >= 0, got {step_seconds!r}")
    cancel = Path(spec["cancel_file"]) if spec.get("cancel_file") else None
    return Path(spec["job_dir"]), Path(spec["outputs_dir"]), cancel, pattern, steps, step_seconds


def main(argv):
    if len(argv) != 2:
        print("usage: run.py <job.json>", file=sys.stderr)
        return 2
    try:
        job_dir, outputs, cancel_file, pattern, steps, step_seconds = load_spec(argv[1])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"invalid job spec: {exc}", file=sys.stderr)
        return 2

    print(f"hello: pattern={pattern} steps={steps} step_seconds={step_seconds}", flush=True)
    started = time.monotonic()
    rows = []
    was_cancelled = False

    with open(job_dir / "progress.jsonl", "a", encoding="utf-8") as progress:
        for step, value in sequence(pattern):
            if cancelled(cancel_file):
                was_cancelled = True
                break
            rows.append((step, value))
            progress.write(json.dumps({
                "time": now(), "progress": round(step / steps, 4), "current": step,
                "total": steps, "unit": "steps", "stage": pattern,
                "message": f"step {step}/{steps}: {str(value)[:60]}",
            }) + "\n")
            progress.flush()
            if step == steps:
                break
            if wait(step_seconds, cancel_file):
                was_cancelled = True
                break

    write_results(job_dir, outputs, pattern, rows, steps, time.monotonic() - started, was_cancelled)
    if was_cancelled:
        print(f"hello: cancel requested - cancel honoured after {len(rows)} of {steps} steps",
              flush=True)
    else:
        print(f"hello: done, {steps} steps, last value {str(rows[-1][1])[:60]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
