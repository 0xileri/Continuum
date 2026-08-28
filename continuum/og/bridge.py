"""The Python↔Node seam.

Every 0G call in this codebase goes through ``call_bridge``. That is deliberate: it gives one place
where the subprocess contract, the timeout, the error taxonomy and — most importantly — the rule
that **secrets never cross this boundary in either direction** are enforced.

``OG_PRIVATE_KEY`` is read by the Node side from its own environment. It is never passed as an
argument, never included in the JSON written to stdin, and never appears in a result. The bridge
returns the *address* it signed with, which is what an operator needs to fund and what a reader
needs to check on the explorer.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from continuum import config

log = logging.getLogger(__name__)

BRIDGE_DIR = config.PROJECT_ROOT / "og-bridge"


class BridgeError(RuntimeError):
    """The bridge ran and reported a failure. ``payload`` carries whatever detail it returned."""

    def __init__(self, message: str, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class BridgeUnavailable(BridgeError):
    """The bridge could not run at all — Node missing, dependencies not installed, no key.

    Distinct from ``BridgeError`` because the callers treat them differently: an unavailable bridge
    is a configuration state the engine is expected to run through (scoring continues, unattested),
    while a bridge that ran and failed is usually a real problem worth surfacing loudly.
    """


def _node() -> str:
    node = shutil.which("node")
    if not node:
        raise BridgeUnavailable(
            "node is not on PATH. The 0G SDKs are TypeScript-only, so Compute, Storage and the "
            "registry publish all run through og-bridge/. Install Node 18+ and run "
            "`cd og-bridge && npm install`."
        )
    return node


def _ready(script: str) -> Path:
    path = BRIDGE_DIR / script
    if not path.exists():
        raise BridgeUnavailable(f"missing bridge script {path}")
    if not (BRIDGE_DIR / "node_modules").exists():
        raise BridgeUnavailable(
            f"og-bridge dependencies are not installed. Run:\n"
            f"    cd {BRIDGE_DIR.name} && npm install"
        )
    return path


def has_key() -> bool:
    """Whether a signing key is configured. Checked, never read."""
    return bool(os.getenv("OG_PRIVATE_KEY") or os.getenv("PRIVATE_KEY"))


def available(script: str = "compute.mjs") -> tuple[bool, str]:
    """``(ready, reason)`` — for ``/health`` and CLI preflight, so a run can say what is missing
    before it spends twenty minutes discovering it."""
    try:
        _node()
        _ready(script)
    except BridgeUnavailable as exc:
        return False, str(exc)
    if not has_key():
        return False, "OG_PRIVATE_KEY is not set"
    return True, ""


def call_bridge(script: str, payload: dict, *, timeout: int | None = None) -> dict:
    """Run one bridge script with ``payload`` on stdin and return its parsed JSON result.

    The Node side always writes exactly one JSON object to stdout and everything human-readable to
    stderr, so a chatty SDK cannot corrupt the channel. A non-zero exit with parseable JSON is
    raised as ``BridgeError`` carrying that JSON; anything else is raised with the captured stderr,
    which is the only thing that helps when an SDK throws before our handler runs.
    """
    node = _node()
    path = _ready(script)

    env = dict(os.environ)
    env.setdefault("CONTINUUM_OG_NETWORK", config.OG_NETWORK)
    env.setdefault("CONTINUUM_OG_BRIDGE_TIMEOUT", str(config.OG_BRIDGE_TIMEOUT_S))

    body = {"network": config.OG_NETWORK, **payload}

    try:
        proc = subprocess.run(
            [node, str(path)],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            cwd=str(BRIDGE_DIR),
            env=env,
            timeout=timeout or config.OG_BRIDGE_TIMEOUT_S + 30,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(
            f"{script} did not return within {exc.timeout}s. 0G Storage uploads and Compute "
            f"settlement both wait on chain confirmations — raise CONTINUUM_OG_BRIDGE_TIMEOUT if "
            f"this is the network being slow rather than something hanging."
        ) from exc

    if proc.stderr.strip():
        for line in proc.stderr.strip().splitlines():
            log.info("[%s] %s", script, line)

    stdout = proc.stdout.strip()
    if not stdout:
        raise BridgeError(
            f"{script} exited {proc.returncode} with no result on stdout. stderr:\n"
            f"{proc.stderr.strip()[:2000]}"
        )

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"{script} wrote unparseable output: {stdout[:500]}") from exc

    if not result.get("ok", False):
        error = str(result.get("error", "unknown bridge failure"))
        if "OG_PRIVATE_KEY is not set" in error or "not installed" in error:
            raise BridgeUnavailable(error, result)
        raise BridgeError(error, result)

    return result
