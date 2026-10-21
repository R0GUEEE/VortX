#!/usr/bin/env bash
# Self-hosted add-on subtitle transport over loopback only: two local fixture servers stand in for the
# installed add-on and for a different private server. No external network is used.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p app/build
run_dir=$(mktemp -d "$PWD/app/build/private-addon-subtitles.XXXXXX")

cat > "$run_dir/fixture.py" <<'PY'
import http.server, os, socketserver, sys, time
other_port = sys.argv[2]
hits = 0
BODY = b"1\n00:00:01,000 --> 00:00:02,000\nhello\n"
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def send(self, status, body=b"", headers=(), sized=True):
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        if sized:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The client refused an oversized body and closed early, as intended.
    def do_GET(self):
        global hits
        if self.path == "/hits":
            return self.send(200, str(hits).encode())
        hits += 1
        if self.path == "/sub.srt":
            return self.send(200, BODY, [("Content-Type", "application/x-subrip")])
        if self.path == "/redirect-same":
            return self.send(302, headers=[("Location", "/sub.srt")])
        if self.path == "/redirect-other":
            return self.send(302, headers=[("Location", "http://127.0.0.1:%s/sub.srt" % other_port)])
        if self.path == "/big":
            return self.send(200, b"a" * (8 * 1024 * 1024 + 1))
        if self.path == "/big-unsized":
            # No Content-Length: only the streaming byte cap can stop this body.
            return self.send(200, b"a" * (8 * 1024 * 1024 + 1), sized=False)
        if self.path == "/empty":
            return self.send(200)
        if self.path == "/slow":
            time.sleep(3)
            return self.send(200, BODY)
        return self.send(404)
class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
server = Server(("127.0.0.1", 0), Handler)
with open(sys.argv[1] + ".tmp", "w") as port_file:
    port_file.write(str(server.server_address[1]))
os.replace(sys.argv[1] + ".tmp", sys.argv[1])
server.serve_forever()
PY

# Wait for a fixture to publish its port; hosted runners can take several seconds to start Python.
wait_port() {
  local file=$1 pid=$2
  for _ in $(seq 300); do
    [ -s "$file" ] && return 0
    kill -0 "$pid" 2>/dev/null || { echo "fixture server exited before listening: $file" >&2; return 1; }
    sleep 0.1
  done
  echo "fixture server did not listen within 30 s: $file" >&2
  return 1
}

# The add-on fixture redirects to OTHER, so OTHER starts first.
python3 -I "$run_dir/fixture.py" "$run_dir/other.port" 0 & other_pid=$!
trap 'kill "$other_pid" ${addon_pid:-} 2>/dev/null || true' EXIT
wait_port "$run_dir/other.port" "$other_pid"
OTHER_PORT=$(cat "$run_dir/other.port")
python3 -I "$run_dir/fixture.py" "$run_dir/addon.port" "$OTHER_PORT" & addon_pid=$!
wait_port "$run_dir/addon.port" "$addon_pid"
ADDON_PORT=$(cat "$run_dir/addon.port")

xcrun swiftc -swift-version 6 -strict-concurrency=complete -warnings-as-errors \
  -parse-as-library -j 1 -num-threads 1 app/SourcesShared/AddonURLGuard.swift \
  app/SourcesShared/PrivateAddonSubtitle.swift app/Tests/PrivateAddonSubtitleTests.swift \
  -o "$run_dir/checks"
ADDON_PORT="$ADDON_PORT" OTHER_PORT="$OTHER_PORT" "$run_dir/checks"
printf 'Retained private add-on subtitle checks: %s\n' "$run_dir"
