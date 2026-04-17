"""Shell into n8n-n8n-1's node runtime to run the JS mirror validator.

The contract framework needs a JS validator running on the same Node
runtime as n8n so the mirror cannot lie about what n8n would accept.
We don't have system-wide `node` on the VPS (by design — only the n8n
container ships it), so we `docker exec -i n8n-n8n-1 node` with the
validator script piped over stdin.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
JS_VALIDATOR = HERE / "js_validator.js"
CONTAINER = "n8n-n8n-1"


class JSMirrorError(RuntimeError):
    """Raised when the JS mirror cannot be invoked (docker/node missing)."""


def validate_js(schema: str, data: Any) -> dict:
    """Run the JS mirror validator for `schema` against `data`.

    Returns {"valid": bool, "errors": [...]}.

    Input is sent to node via stdin; the validator script is sent as a
    single argv-less file contents over stdin in a two-step protocol:
    we cat the validator and then pipe input, so use a wrapper.
    """
    if not JS_VALIDATOR.exists():
        raise JSMirrorError(f"js_validator.js missing at {JS_VALIDATOR}")

    script = JS_VALIDATOR.read_text(encoding="utf-8")
    payload = json.dumps({"schema": schema, "data": data})

    # Single-shot: feed `<script>\n__END_OF_SCRIPT__\n<payload>` into a
    # small bootstrap eval. Simpler: use `node -e` with the script inlined
    # via a here-string, but -e has shell-quoting pain. Instead we cat
    # the validator to a file inside the container once per call.
    #
    # Pragmatic approach: base64-encode the script, decode in-container,
    # write to /tmp/js_validator.js, then pipe input to `node /tmp/...`.
    import base64
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    # Note: --privileged not needed; josh is in docker group.
    sh = (
        f"echo {b64} | base64 -d > /tmp/js_validator.js && "
        f"node /tmp/js_validator.js"
    )
    cmd = ["docker", "exec", "-i", CONTAINER, "sh", "-c", sh]
    try:
        proc = subprocess.run(
            cmd,
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as e:
        raise JSMirrorError(f"docker not available: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise JSMirrorError(f"js mirror validator timed out: {e}") from e

    if proc.returncode != 0:
        raise JSMirrorError(
            "js mirror returned exit " + str(proc.returncode)
            + "\nstderr: " + proc.stderr.decode("utf-8", errors="replace")[:500]
        )
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise JSMirrorError(
            f"js mirror returned non-JSON: {e}\nstdout: {proc.stdout[:500]!r}"
        ) from e
