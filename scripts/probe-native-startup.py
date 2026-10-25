#!/usr/bin/env python3
"""Observe only the frozen public native-builder startup; never build an engine/app."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

TAG = "v0.5.6-beta.7"
SOURCE = "01d3ec566b32f8fa943d9bcfe6c4a8abf8a40f63"
PINS = {
    "scripts/build-ffi-xcframework.sh": "33212807d8b4243baa68311feda394d2ca704e6f906806b1223d671e20d85000",
    "scripts/resolve-native-engine.sh": "6c261682d60d2a58197ba471a9f988c3cda80cb3555a8e2b8e64b5932fff1cb6",
}
SOURCE_LINE = 'source "$HOME/.cargo/env" 2>/dev/null || true'
MARKER = b"VORTX_STARTUP_PREFIX_COMPLETE"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL)


def observe(shell, script, body, env):
    script.write_bytes(body)
    process = subprocess.Popen([shell, str(script), "--resource-host"], cwd=script.parent.parent,
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    timed_out = False
    try:
        out, err = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        out, err = process.communicate()
    clues = [name for token, name in ((b"unbound variable", "nounset"),
             (b"syntax error", "syntax"), (b"no such file", "missing-file"))
             if token in err.lower()]
    return {"exit": process.returncode, "timeout": timed_out,
            "reached": MARKER in out.splitlines(), "stderr_classifiers": clues,
            "stdout_bytes": len(out), "stdout_sha256": sha(out),
            "stderr_bytes": len(err), "stderr_sha256": sha(err)}


def main():
    phase = sys.argv[1] if len(sys.argv) == 2 else ""
    if phase not in ("before-rustup", "after-rustup"):
        raise ValueError("phase")
    if git("rev-parse", TAG + "^{commit}").decode().strip() != SOURCE:
        raise ValueError("tag-identity")
    files = {name: git("show", TAG + ":" + name) for name in PINS}
    if any(sha(files[name]) != value for name, value in PINS.items()):
        raise ValueError("frozen-source")
    text = files["scripts/build-ffi-xcframework.sh"].decode()
    if text.count("\nBUILDSTD=") != 1 or text.splitlines().count(SOURCE_LINE) != 1:
        raise ValueError("prefix-boundary")
    prefix = text.split("\nBUILDSTD=", 1)[0]
    if not prefix.rstrip().endswith('echo "engine workspace: $ENGINE_DIR (mode: $MODE)"'):
        raise ValueError("prefix-end")
    suffix = '\nprintf "%s\\n" "' + MARKER.decode() + '"\n'
    bodies = {"original": (prefix + suffix).encode(),
              "without-cargo-env": (prefix.replace(SOURCE_LINE, ": # control only", 1) + suffix).encode(),
              "cargo-env-isolated": ('#!/usr/bin/env bash\nset -euo pipefail\n'
                  'source "$HOME/.cargo/env" || true\n' + suffix).encode()}
    cargo_env = Path(os.environ["HOME"]) / ".cargo" / "env"
    cargo = {"present": cargo_env.is_file()}
    if cargo["present"]:
        if cargo_env.stat().st_size > 1024 * 1024:
            raise ValueError("cargo-env-bound")
        data = cargo_env.read_bytes()
        cargo.update(bytes=len(data), sha256=sha(data))
    report = {"schema": 1, "diagnostic_only": True, "phase": phase, "tag": TAG,
              "source": SOURCE, "frozen_files": PINS, "cargo_env": cargo,
              "prefix_sha256": {name: sha(body) for name, body in bodies.items()}, "shells": []}
    env = dict(os.environ)
    env["VORTX_ENGINE_DIR"] = "vortx-core"
    # Keep the actual runner HOME; isolate only public script paths and synthetic manifest existence.
    with tempfile.TemporaryDirectory(prefix="native-startup-probe-", dir=os.environ["RUNNER_TEMP"]) as directory:
        root = Path(directory)
        (root / "scripts").mkdir()
        crate = root / "vortx-core" / "crates" / "ffi"
        crate.mkdir(parents=True)
        (root / "vortx-core" / "Cargo.toml").write_text('[workspace]\nmembers=["crates/ffi"]\n')
        (crate / "Cargo.toml").write_text('[package]\nname="startup-fixture"\nversion="0.0.0"\n')
        (root / "scripts" / "resolve-native-engine.sh").write_bytes(files["scripts/resolve-native-engine.sh"])
        shells = list(dict.fromkeys(path for path in ("/bin/bash", shutil.which("bash")) if path))
        for shell in shells:
            version = subprocess.check_output([shell, "--version"], stderr=subprocess.DEVNULL,
                                              timeout=5).splitlines()[0].decode("ascii", "replace")[:180]
            row = {"path": shell, "version": version, "variants": {}}
            for name, body in bodies.items():
                row["variants"][name] = observe(shell, root / "scripts" / "build-ffi-xcframework.sh", body, env)
            report["shells"].append(row)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print subprocess output, external shell initialization, environment values or exception text.
        print(json.dumps({"schema": 1, "diagnostic_only": True, "error_type": type(error).__name__}))
        sys.exit(1)
