"""Build the runtime image and check it end to end with the tiny random model: never downloads real weights.

build -> write the tiny model into a throwaway volume -> run the container read-only against it -> poll /health ->
POST /check once -> stop and clean up.
  python scripts/container_smoke.py
"""

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

IMAGE, VOLUME = "hallucination-detector-smoke:local", "hd-smoke-model"


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def main() -> None:
    if not shutil.which("docker"):
        raise SystemExit("Docker is required. Install/start Docker, then rerun this smoke check.")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["docker", "build", "--target", "runtime", "-t", IMAGE, str(root)], check=True)
    docker("volume", "create", VOLUME)
    container = None
    try:
        docker("run", "--rm", "--user", "root", "-v", f"{VOLUME}:/model", IMAGE, "python", "-m", "hd.tiny_model",
               "/model")
        # read-only root, so the Hugging Face cache gets a writable tmpfs owned by the app user
        container = docker("run", "--detach", "--read-only", "--tmpfs", "/tmp",
                           "--tmpfs", "/home/app/.cache:uid=10001,gid=10001", "--publish", "127.0.0.1::8000",
                           "-v", f"{VOLUME}:/model:ro", "--env", "HD_MODEL=/model", IMAGE)
        base = f"http://127.0.0.1:{docker('port', container, '8000/tcp').rsplit(':', 1)[1]}"

        deadline = time.monotonic() + 120
        while True:
            try:
                with urllib.request.urlopen(base + "/health", timeout=5) as r:
                    assert json.load(r)["status"] == "ok"
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                running = docker("inspect", "-f", "{{.State.Running}}", container) == "true"
                if not running or time.monotonic() >= deadline:
                    subprocess.run(["docker", "logs", container], check=False)
                    raise RuntimeError("container exited" if not running else "container not healthy in 120 s") \
                        from None
                time.sleep(1)

        payload = {"sources": ["the cat sat on the mat"], "answer": "The cat sat. It was on the mat."}
        req = urllib.request.Request(base + "/check", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
        assert [s["text"] for s in body["sentences"]] == ["The cat sat.", "It was on the mat."], body
        print("PASS: build, startup, /health, /check")
    finally:
        if container:  # no --rm on the run, so a crashed container's logs survive until here
            subprocess.run(["docker", "rm", "-f", container], check=False, capture_output=True)
        subprocess.run(["docker", "volume", "rm", VOLUME], check=False, capture_output=True)


if __name__ == "__main__":
    main()
