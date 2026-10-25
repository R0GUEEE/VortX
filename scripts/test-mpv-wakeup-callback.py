#!/usr/bin/env python3
"""Run actual wakeup registration through an imported C ABI on a joined native thread."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "app/Sources/Player/MPVMetalViewController.swift"
FIXTURE = "app/Tests/MPVWakeupCallbackTests.swift"

def declaration(source, marker):
    if source.count(marker) != 1:
        raise RuntimeError("Missing or ambiguous declaration: " + marker)
    start = source.index(marker)
    end = source.index("\n    }\n", start) + len("\n    }")
    return source[start:end]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ref", default="current")
    args = parser.parse_args()
    source = (ROOT / SOURCE).read_text() if args.source_ref == "current" else subprocess.check_output(
        ["git", "-C", str(ROOT), "show", args.source_ref + ":" + SOURCE], text=True)
    fixture = (ROOT / FIXTURE).read_text()
    install = declaration(source, "    @MainActor\n    func install(_ relay: WakeupRelay)")
    callback_marker = "    private nonisolated static let wakeupCallback:"
    callback = declaration(source, callback_marker) if callback_marker in source else ""
    # Preserve the actor annotation, complete method and complete C entry point literally.
    for name, body in (("install", install), ("callback", callback)):
        marker = "// @source:" + name
        if fixture.count(marker) != 1:
            raise RuntimeError("Missing fixture slot: " + name)
        fixture = fixture.replace(marker, body)
    output = ROOT / "app/build"
    output.mkdir(exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="mpv-wakeup-callback.", dir=output))
    print("Evidence:", out, flush=True)
    headers = ROOT / "app/Vendor/MPVKit-DVFEL/artifacts/Libmpv-GPL.xcframework/macos-arm64_x86_64/Libmpv.framework/Headers/mpv"
    if not (headers / "client.h").is_file():
        raise RuntimeError("Genuine selected macOS libmpv client header is missing")
    (out / "Probe.h").write_text('#include "client.h"\nint vx_probe_worker_wakeups(int count, int nil_context);\n')
    (out / "module.modulemap").write_text('module MPVWakeupProbe { header "Probe.h" export * }\n')
    (out / "Probe.c").write_text(r'''#include "Probe.h"
#include <pthread.h>
static void (*wakeup)(void *);
static void *wakeup_context;
void mpv_set_wakeup_callback(mpv_handle *handle, void (*callback)(void *), void *context) {
    (void)handle;
    wakeup = callback; wakeup_context = context;
    if (wakeup) wakeup(wakeup_context); /* libmpv may notify inline during registration. */
}
struct worker_input { int count; int nil_context; };
static void *worker(void *opaque) {
    struct worker_input *input = opaque;
    for (int i = 0; i < input->count; i++)
        if (wakeup) wakeup(input->nil_context ? NULL : wakeup_context);
    return NULL;
}
int vx_probe_worker_wakeups(int count, int nil_context) {
    struct worker_input input = { count, nil_context };
    pthread_t thread;
    int status = pthread_create(&thread, NULL, worker, &input);
    if (status) return status;
    return pthread_join(thread, NULL);
}
''')
    (out / "ActualSourceTests.swift").write_text(fixture)
    manifest = {"source_ref": args.source_ref, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "install_sha256": hashlib.sha256(install.encode()).hexdigest(),
                "callback_sha256": hashlib.sha256(callback.encode()).hexdigest(),
                "client_header_sha256": hashlib.sha256((headers / "client.h").read_bytes()).hexdigest(),
                "scope": "Literal actual install/C entry point; synthetic C registration and relay counter. Joined pthread exercises Debug actor checks; not full-player/device acceptance."}
    (out / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    def run(name, command):
        with (out / (name + ".log")).open("wb") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        (out / (name + ".json")).write_text(json.dumps({"args": command, "returncode": result.returncode}) + "\n")
        print(name + ": " + str(result.returncode), flush=True)
        if result.returncode:
            print((out / (name + ".log")).read_text(errors="replace")[-5000:], flush=True)
            raise SystemExit(1)
    run("compile-c", ["nice", "-n", "15", "xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-I", str(headers),
                      "-c", str(out / "Probe.c"), "-o", str(out / "Probe.o")])
    run("compile-swift", ["nice", "-n", "15", "xcrun", "swiftc", "-parse-as-library", "-swift-version", "6",
        "-strict-concurrency=complete", "-warnings-as-errors", "-Onone", "-g", "-D", "DEBUG", "-j", "1",
        "-num-threads", "1", "-I", str(out), "-Xcc", "-I" + str(headers),
        "-module-cache-path", str(out / "module-cache"), str(out / "ActualSourceTests.swift"),
        str(out / "Probe.o"), "-o", str(out / "tests")])
    run("runtime", [str(out / "tests")])
    print((out / "runtime.log").read_text(), end="")

if __name__ == "__main__":
    main()
