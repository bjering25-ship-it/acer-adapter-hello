"""Local tests for the hello adapter (stdlib unittest, non-admin, offline).

Run with a venv built from requirements.lock.txt:
    python -m venv .venv
    .venv\\Scripts\\python.exe -m pip install --require-hashes --no-deps --only-binary=:all: -r requirements.lock.txt
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import ast
import csv
import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "hello" / "run.py"
FORBIDDEN = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "xmlrpc", "requests",
             "subprocess", "asyncio", "multiprocessing", "ctypes"}


def make_job(base, params, graceful=True):
    job_dir = Path(base) / "job"
    (job_dir / "outputs").mkdir(parents=True)
    (job_dir / "tmp").mkdir()
    spec = {
        "job_id": "test", "workload_id": "hello", "workload_version": "test",
        "params": params, "job_dir": str(job_dir), "outputs_dir": str(job_dir / "outputs"),
        "tmp_dir": str(job_dir / "tmp"),
        "cancel_file": str(job_dir / "cancel.requested") if graceful else None,
        "max_runtime_s": 3600, "cancel_grace_s": 10,
    }
    (job_dir / "job.json").write_text(json.dumps(spec), encoding="utf-8")
    return job_dir


def start(job_dir):
    return subprocess.Popen([sys.executable, "-E", "-s", str(RUN), str(job_dir / "job.json")],
                            cwd=job_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def read_csv(job_dir):
    text = (job_dir / "outputs" / "result.csv").read_text(encoding="utf-8")
    return list(csv.reader(text.splitlines()))


def progress_lines(job_dir):
    path = job_dir / "progress.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class TestStatic(unittest.TestCase):
    def test_no_network_or_process_imports(self):
        for path in (REPO / "hello").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "").split(".")[0])
                elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "__import__":
                    self.fail(f"{path}: dynamic __import__")
            self.assertFalse(imported & FORBIDDEN, f"{path}: {sorted(imported & FORBIDDEN)}")
            self.assertLessEqual(imported, {"csv", "json", "os", "sys", "time", "datetime", "pathlib",
                                            "tabulate"}, f"{path}: {sorted(imported)}")

    def test_manifest(self):
        data = json.loads((REPO / "acer-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["workload_id"], "hello")
        self.assertEqual(data["version"], "1.0.0")
        self.assertIs(data["diagnostic"], False)
        self.assertNotIn("venv", data)
        self.assertEqual(data["argv"], ["{python}", "-E", "-s", "{adapter_dir}\\hello\\run.py", "{job_spec}"])
        props = data["params_schema"]["properties"]
        self.assertEqual(props["pattern"]["enum"], ["linear", "squares", "fibonacci"])
        self.assertEqual(props["steps"]["type"], "integer")
        self.assertEqual(props["step_seconds"]["type"], "number")
        self.assertIs(data["params_schema"]["additionalProperties"], False)
        self.assertEqual((data["max_runtime_s"], data["cancel_grace_s"], data["graceful_cancel"]),
                         (3600, 10, True))
        # Worst case steps * step_seconds fits in max_runtime_s.
        self.assertLessEqual(props["steps"]["maximum"] * props["step_seconds"]["maximum"],
                             data["max_runtime_s"])

    def test_lock_is_pinned_and_hashed(self):
        text = (REPO / "requirements.lock.txt").read_text(encoding="utf-8")
        lines = [l.split("#")[0].strip() for l in re.sub(r"\\\r?\n", " ", text).splitlines()]
        lines = [l for l in lines if l]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^tabulate==0\.9\.0\s+--hash=sha256:[0-9a-f]{64}$")


class TestRun(unittest.TestCase):
    def run_job(self, params, graceful=True):
        with tempfile.TemporaryDirectory() as base:
            job_dir = make_job(base, params, graceful)
            proc = start(job_dir)
            out, err = proc.communicate(timeout=60)
            result = {
                "code": proc.returncode, "out": out, "err": err,
                "progress": progress_lines(job_dir),
                "summary": json.loads((job_dir / "summary.json").read_text(encoding="utf-8"))
                if (job_dir / "summary.json").exists() else None,
                "csv": read_csv(job_dir)
                if (job_dir / "outputs" / "result.csv").exists() else None,
                "report": (job_dir / "outputs" / "report.txt").read_text(encoding="utf-8")
                if (job_dir / "outputs" / "report.txt").exists() else None,
            }
            return result

    def test_patterns(self):
        expected = {"linear": [1, 2, 3, 4, 5, 6], "squares": [1, 4, 9, 16, 25, 36],
                    "fibonacci": [1, 1, 2, 3, 5, 8]}
        for pattern, values in expected.items():
            r = self.run_job({"pattern": pattern, "steps": 6, "step_seconds": 0})
            self.assertEqual(r["code"], 0, r["err"])
            self.assertEqual([int(v) for _, v in r["csv"][1:]], values, pattern)

    def test_completed_job_files(self):
        r = self.run_job({"pattern": "squares", "steps": 5, "step_seconds": 0.01})
        self.assertEqual(r["code"], 0, r["err"])
        self.assertEqual(len(r["progress"]), 5)
        self.assertEqual([p["current"] for p in r["progress"]], [1, 2, 3, 4, 5])
        self.assertEqual(r["progress"][-1]["progress"], 1.0)
        for line in r["progress"]:
            self.assertEqual(set(line), {"time", "progress", "current", "total", "unit", "stage", "message"})
            self.assertLessEqual(len(line["time"]), 64)
            self.assertLessEqual(len(line["message"]), 500)
        self.assertEqual(r["csv"][0], ["step", "value"])
        self.assertEqual(len(r["csv"]), 6)
        self.assertRegex(r["report"], r"\|\s+step\s+\|\s+value\s+\|")
        s = r["summary"]
        self.assertEqual(set(s), {"message", "fields", "outputs"})
        self.assertTrue(1 <= len(s["fields"]) <= 4)
        for field in s["fields"]:
            self.assertEqual(set(field), {"label", "value"})
            self.assertLessEqual(len(field["label"]), 40)
        self.assertIn({"type": "FILE", "path": "result.csv", "label": "Result (CSV)"}, s["outputs"])
        self.assertIn({"type": "FILE", "path": "report.txt", "label": "Report"}, s["outputs"])
        self.assertIn("done, 5 steps", r["out"])

    def test_long_fibonacci_fits_limits(self):
        r = self.run_job({"pattern": "fibonacci", "steps": 600, "step_seconds": 0})
        self.assertEqual(r["code"], 0, r["err"])
        last = next(f["value"] for f in r["summary"]["fields"] if f["label"] == "Last value")
        self.assertIsInstance(last, str)
        self.assertLessEqual(len(last), 200)
        self.assertEqual(len(r["progress"]), 600)

    def test_graceful_cancel(self):
        with tempfile.TemporaryDirectory() as base:
            job_dir = make_job(base, {"pattern": "linear", "steps": 600, "step_seconds": 1})
            proc = start(job_dir)
            deadline = time.monotonic() + 20
            while len(progress_lines(job_dir)) < 2 and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertGreaterEqual(len(progress_lines(job_dir)), 2)
            requested = time.monotonic()
            (job_dir / "cancel.requested").write_text("cancel", encoding="utf-8")
            out, err = proc.communicate(timeout=10)
            self.assertLess(time.monotonic() - requested, 2.0, "cancel must be honoured within 2 s")
            self.assertEqual(proc.returncode, 0, err)
            self.assertIn("cancel honoured", out)
            summary = json.loads((job_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["message"].startswith("Cancelled after"))
            rows = read_csv(job_dir)
            self.assertEqual(len(rows) - 1, len(progress_lines(job_dir)))

    def test_no_cancel_file_runs_to_end(self):
        r = self.run_job({"pattern": "linear", "steps": 3, "step_seconds": 0}, graceful=False)
        self.assertEqual(r["code"], 0, r["err"])
        self.assertEqual(len(r["progress"]), 3)

    def test_invalid_spec_exit_2(self):
        for params in ({"pattern": "nope", "steps": 3, "step_seconds": 0},
                       {"pattern": "linear", "steps": 0, "step_seconds": 0},
                       {"pattern": "linear", "steps": 3}):
            r = self.run_job(params)
            self.assertEqual(r["code"], 2, params)
            self.assertIn("invalid job spec", r["err"])


if __name__ == "__main__":
    unittest.main()
