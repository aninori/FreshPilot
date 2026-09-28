"""Build the offline synthetic demo once, then serve API and web until interrupted."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from freshpilot.catalog.run import run as run_catalog
from freshpilot.config import SEED
from freshpilot.data.generate import generate, write_duckdb
from freshpilot.evals.run import run as run_evaluation
from freshpilot.forecasting.run import run as run_forecasting
from freshpilot.pipeline.run import run as run_pipeline

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/freshpilot.duckdb"


def _wait_until_ready(processes: tuple[subprocess.Popen[bytes], ...]) -> None:
    deadline = time.monotonic() + 30
    urls = ("http://127.0.0.1:8000/docs", "http://127.0.0.1:5173")
    while time.monotonic() < deadline:
        failed = next((process for process in processes if process.poll() is not None), None)
        if failed is not None:
            raise RuntimeError(f"Demo server exited during startup with code {failed.returncode}")
        try:
            for url in urls:
                with urlopen(url, timeout=1) as response:  # noqa: S310 - fixed loopback URLs
                    if response.status >= 400:
                        raise URLError(f"HTTP {response.status}")
        except (OSError, URLError):
            time.sleep(0.25)
        else:
            return
    raise TimeoutError("Demo servers did not become ready within 30 seconds")


def main() -> None:
    os.chdir(ROOT)
    os.environ["FRESHPILOT_LLM"] = "off"
    started = time.monotonic()
    print("[1/5] Generating deterministic synthetic data")
    write_duckdb(generate(SEED), DB)
    print("[2/5] Training catalog and forecast models")
    catalog = run_catalog(DB, SEED)
    forecast = run_forecasting(DB, SEED)
    print("[3/5] Running the daily decision pipeline")
    run_pipeline(DB)
    print("[4/5] Measuring G1-G8")
    report = run_evaluation(DB, SEED, catalog, forecast)
    if not all(v.get("pass", False) for k, v in report.items() if k.startswith("G")):
        raise SystemExit("Evaluation gate failed; see reports/eval.md")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise SystemExit("Node.js/npm is required for the web app")
    if not (ROOT / "web/node_modules").exists():
        subprocess.run([npm, "install", "--no-fund", "--no-audit"], cwd=ROOT / "web", check=True)
    subprocess.run([npm, "run", "build"], cwd=ROOT / "web", check=True)
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "freshpilot.api.app:app",
                            "--host", "127.0.0.1", "--port", "8000"], cwd=ROOT)
    web = subprocess.Popen([npm, "run", "dev", "--", "--strictPort"], cwd=ROOT / "web")
    try:
        _wait_until_ready((api, web))
        print(f"[5/5] Demo ready in {time.monotonic()-started:.1f}s")
        print("Open http://127.0.0.1:5173 — press Ctrl+C to stop both servers")
        while api.poll() is None and web.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in (web, api):
            if process.poll() is None:
                process.terminate()
        for process in (web, api):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
