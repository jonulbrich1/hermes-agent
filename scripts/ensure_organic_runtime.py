from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request

from configure_organic_hermes import ROOT, organic_environment, runtime_python


STATE_URL = "http://127.0.0.1:8788/api/state"


def runtime_ready(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(STATE_URL, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("runtime", {}).get("status") == "healthy"
    except Exception:
        return False


def main() -> int:
    if runtime_ready():
        print("Organic runtime already ready at http://127.0.0.1:8788")
        return 0
    python = runtime_python()
    if not python.exists():
        raise SystemExit(f"Organic runtime Python is missing: {python}")
    log_path = ROOT / "runtime" / "organic_home" / "logs" / "runtime-bridge.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(organic_environment())
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [
                str(python),
                "-m",
                "organic_runtime",
                "gui",
                "--host",
                "127.0.0.1",
                "--port",
                "8788",
                "--no-open",
            ],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
    deadline = time.time() + 35.0
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"Organic runtime exited with code {process.returncode}; see {log_path}"
            )
        if runtime_ready():
            print(f"Organic runtime ready at http://127.0.0.1:8788 (pid={process.pid})")
            return 0
        time.sleep(0.4)
    raise SystemExit(f"Organic runtime did not become ready; see {log_path}")


if __name__ == "__main__":
    raise SystemExit(main())
