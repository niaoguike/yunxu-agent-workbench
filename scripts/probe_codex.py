"""Read-only native protocol probe. No model turn, no user credential profile."""

import argparse
import json
import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", default="codex")
    arguments = parser.parse_args()
    root = Path(".data/probes").resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, ignore_cleanup_errors=True) as temporary:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"OPENAI_API_KEY", "CODEX_API_KEY"}
        }
        env["CODEX_HOME"] = str(Path(temporary) / "native")
        Path(env["CODEX_HOME"]).mkdir()
        process = subprocess.Popen(
            [arguments.command, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
        )
        inbox = queue.Queue()

        def reader():
            for line in process.stdout:
                inbox.put(json.loads(line))

        threading.Thread(target=reader, daemon=True).start()

        def send(value):
            process.stdin.write(json.dumps(value) + "\n")
            process.stdin.flush()

        def reply(identifier):
            while True:
                result = inbox.get(timeout=15)
                if result.get("id") == identifier:
                    if "error" in result:
                        raise RuntimeError(result["error"])
                    return result["result"]

        try:
            send(
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "workbench-probe", "version": "0.1.0"},
                        "capabilities": {"experimentalApi": False},
                    },
                }
            )
            reply(1)
            send({"method": "initialized", "params": {}})
            send(
                {
                    "id": 2,
                    "method": "thread/start",
                    "params": {
                        "cwd": temporary,
                        "approvalPolicy": "untrusted",
                        "sandbox": "workspace-write",
                    },
                }
            )
            thread = reply(2)["thread"]["id"]
            assert thread
            print(
                "Native Codex probe passed: initialize and thread/start; no model turn or personal authentication used. Resume requires a materialized rollout and is not tested here."
            )
        finally:
            if os.name == "nt" and process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
