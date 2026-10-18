#if os(macOS)
import Foundation
import Darwin   // getifaddrs / ifaddrs / getnameinfo for LAN IP discovery
import os

/// Restart-only TERM grace. No numeric PID, force signal, or wait on a possibly live child.
enum MacNodeRestartPolicy {
    static func requestTermination(
        grace: TimeInterval,
        isRunning: () -> Bool,
        terminate: () -> Void,
        now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
        pause: (TimeInterval) -> Void = { Thread.sleep(forTimeInterval: $0) }
    ) -> Bool {
        guard isRunning() else { return true }
        let deadline = now() + max(0, grace)
        terminate()
        while isRunning() {
            let remaining = deadline - now()
            guard remaining > 0 else { return false }
            pause(min(0.05, remaining))
        }
        return true
    }
}

/// The UI reads this restart-failure snapshot independently of the lifecycle queue.
final class MacNodeRestartStatus: Sendable {
    private struct Failure: Sendable {
        let owner: ObjectIdentifier
        let isRunning: @Sendable () -> Bool
    }
    private let failure = OSAllocatedUnfairLock<Failure?>(initialState: nil)

    func record(owner: AnyObject, isRunning: @escaping @Sendable () -> Bool) {
        let next = Failure(owner: ObjectIdentifier(owner), isRunning: isRunning)
        let previous = failure.withLock { state in
            let previous = state
            state = next
            return previous
        }
        // Retire old captures outside the lock: their deinitializers may re-enter status.
        withExtendedLifetime(previous) {}
    }

    func clear() {
        let previous = failure.withLock { state in
            let previous = state
            state = nil
            return previous
        }
        withExtendedLifetime(previous) {}
    }

    func matches(owner: AnyObject) -> Bool {
        let identity = ObjectIdentifier(owner)
        return failure.withLock { $0?.owner == identity }
    }

    var message: String? {
        let snapshot = failure.withLock { $0 }
        guard let snapshot else { return nil }
        // Never hold our lock across a Foundation Process status read.
        if snapshot.isRunning() {
            return "Restart did not complete: the previous streaming server is still running. Try restarting again."
        }
        return "Restart did not complete: the previous streaming server has exited. Try restarting again."
    }
}

/// macOS streaming server. Runs Stremio's server.js (the torrent engine + /proxy + HLS) in a
/// CHILD PROCESS, listening on http://127.0.0.1:11470, so TORRENT streams play on the Mac.
///
/// iOS/tvOS embed `nodejs-mobile` (node as a *library*, started in-process via `node_start`).
/// nodejs-mobile has no macOS slice, so the Mac can't do that. Instead we bundle the ordinary
/// standalone `node` executable (Resources/node-darwin-arm64, fetched by scripts/fetch-node-macos.sh)
/// and spawn it with `Process`. This works because the Mac app is NOT sandboxed; it may launch
/// child processes and bind loopback ports.
///
/// This deliberately exposes the SAME API surface as the iOS/tvOS `NodeServer`
/// (`startIfNeeded()`, `statusDescription`, `logTail(_:)`) under the same type name, so the shared
/// call sites in VortXiOSApp / iOSSettingsView resolve to the right implementation per platform
/// with no `#if os(macOS)` at the call site.
///
/// NATIVE ENGINE (flag-gated, default OFF): behind `vortxNativeServer` (UserDefaults), the same
/// lifecycle spawns the native Rust streaming server (`vortx-streaming-server`, bundled from
/// app/Vendor by scripts/build-mac-server.sh) instead of node + server.js. Same port (11470),
/// same HTTP contract, same LAN-bind toggle; see spawnNative(binPath:). Native selection fails
/// explicitly if its binary is missing. Legacy selection retains the Node path.
enum NodeServer {
    private struct QuitAttempt: Sendable {
        let id: UUID
        let completion: @MainActor @Sendable (Bool) -> Void
        var termAdmitted = false
    }

    /// All mutable static state has checked storage. The lifecycle queue still serializes
    /// process operations; readers and the independent quit deadline never join that queue.
    private struct RuntimeState: Sendable {
        var started = false
        var exitCode: Int32?
        var process: Process?
        var nativePortReceipt: String?
        var nativePublication: (process: Process, receipt: String)?
        var nativeBoundPort: Int?
        var nativeReceiptReadAt: TimeInterval = -1
        var shutdownRequested = false
        var quitAttempt: QuitAttempt?
        var quitFailure: String?
    }
    private static let runtime = OSAllocatedUnfairLock(initialState: RuntimeState())

    private(set) static var started: Bool {
        get { runtime.withLock { $0.started } }
        set { runtime.withLock { $0.started = newValue } }
    }
    private(set) static var exitCode: Int32? {
        get { runtime.withLock { $0.exitCode } }
        set { runtime.withLock { $0.exitCode = newValue } }
    }
    private static var process: Process? {
        get { runtime.withLock { $0.process } }
        set {
            let previous = runtime.withLock { state in
                let previous = state.process
                state.process = newValue
                return previous
            }
            withExtendedLifetime(previous) {}
        }
    }
    private static var nativePortReceipt: String? {
        get { runtime.withLock { $0.nativePortReceipt } }
        set { runtime.withLock { $0.nativePortReceipt = newValue } }
    }
    private static var shutdownRequested: Bool {
        runtime.withLock { $0.shutdownRequested }
    }

    /// Serializes launch/restart/termination operations. No public reader or AppKit quit hook
    /// synchronously joins this queue. A launch/helper/Foundation call can still stall it;
    /// the independent quit deadline then cancels quit while the app keeps its child handle.
    private static let queue = DispatchQueue(label: "com.stremiox.mac.nodeserver")
    private static let restartStatus = MacNodeRestartStatus()

    /// TERM grace; it does not authorize a numeric-PID force signal or an unbounded wait.
    private static let terminateGrace: TimeInterval = 2.0
    /// Ordinary quit gives the native child's 3 s graceful + 1 s runtime-stop path margin.
    /// Restart keeps its separately reviewed two-second grace and explicit retry behavior.
    private static let quitGrace: TimeInterval = 5.0

    // MARK: - LAN sharing ("act as a server for others on this network")

    /// UserDefaults key for the "Share streaming server on this network" toggle.
    private static let shareKey = "stremiox.mac.shareServerOnLAN"

    /// When ON, the bundled server binds 0.0.0.0 (all interfaces) so other devices on the LAN
    /// (your Apple TV / phone) can use this Mac as their Stremio streaming server. When OFF
    /// (the default), it binds loopback only (127.0.0.1), the original behaviour, invisible to
    /// the network. Changing this restarts the node process so the new bind takes effect.
    static var sharedOnLAN: Bool {
        get { UserDefaults.standard.bool(forKey: shareKey) }
        set {
            guard newValue != sharedOnLAN else { return }
            UserDefaults.standard.set(newValue, forKey: shareKey)
            restart()
        }
    }

    /// This machine's primary LAN IPv4 address (e.g. 192.168.1.50), or nil if not on a network.
    /// Prefers en0/en1 (Wi-Fi / Ethernet); skips loopback, link-local (169.254/fe80) and down ifaces.
    static var lanIP: String? {
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else { return nil }
        defer { freeifaddrs(ifaddr) }

        // Rank candidates: prefer en* (Wi-Fi/Ethernet) over anything else.
        var best: (rank: Int, ip: String)?
        var ptr: UnsafeMutablePointer<ifaddrs>? = first
        while let cur = ptr {
            defer { ptr = cur.pointee.ifa_next }
            let flags = Int32(cur.pointee.ifa_flags)
            guard flags & (IFF_UP | IFF_RUNNING) == (IFF_UP | IFF_RUNNING),
                  flags & IFF_LOOPBACK == 0,
                  let sa = cur.pointee.ifa_addr, sa.pointee.sa_family == UInt8(AF_INET) else { continue }

            let name = String(cString: cur.pointee.ifa_name)
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            guard getnameinfo(sa, socklen_t(sa.pointee.sa_len), &host, socklen_t(host.count),
                              nil, 0, NI_NUMERICHOST) == 0 else { continue }
            let ip = String(decoding: host.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
            guard !ip.hasPrefix("169.254"), !ip.isEmpty else { continue }   // skip link-local

            let rank = name.hasPrefix("en") ? 0 : 1
            if best == nil || rank < best!.rank { best = (rank, ip) }
        }
        return best?.ip
    }

    /// The LAN URL other devices paste into their server config, when sharing is on and we have an IP.
    static var lanURL: String? {
        guard sharedOnLAN, let ip = lanIP else { return nil }
        return "http://\(ip):11470"
    }

    // MARK: - Native server (flag-gated, default OFF)

    /// UserDefaults key for the native-server opt-in. When ON and the bundled
    /// `vortx-streaming-server` binary is present, startIfNeeded/restart spawn the native Rust
    /// streaming server (rqbit-based, from the engine's crates/streaming-server) instead of
    /// node + server.js. It serves the same contract on the same fixed 11470 (it landed with a
    /// conformance golden against server.js), so everything downstream (StremioServer.embedded,
    /// lanURL, the /proxy route) is unchanged. Default OFF: the shipping macOS path stays
    /// node + server.js exactly as before this flag existed.
    private static let nativeServerKey = "vortxNativeServer"

    /// The opt-in. Absent key reads false (UserDefaults.bool), i.e. the node path. Flipping it
    /// restarts the child so the engine swap takes effect without an app relaunch, the same
    /// restart pattern as the LAN-sharing toggle above.
    static var nativeServerEnabled: Bool {
        get { NativeTransportPolicy.selectsNative(required: NativeTransportPolicy.isRequired,
                                                  preference: UserDefaults.standard.bool(forKey: nativeServerKey)) }
        set {
            guard !NativeTransportPolicy.isRequired else { return }
            guard newValue != nativeServerEnabled else { return }
            UserDefaults.standard.set(newValue, forKey: nativeServerKey)
            restart()
        }
    }

    /// The bundled native server binary, or nil when scripts/build-mac-server.sh has not produced
    /// it. Like the node binary it is a script-produced, gitignored artifact (app/Vendor/
    /// vortx-streaming-server); project.yml embeds it into Resources copy-if-present, so a
    /// checkout that never ran the script builds fine and simply has no native engine to offer.
    private static var nativeServerBinary: String? {
        Bundle.main.path(forResource: "vortx-streaming-server", ofType: nil)
    }

    /// Native selection includes a missing native binary: that is unavailable, never a Node fallback.
    private static var usingNativeServer: Bool { nativeServerEnabled }

    /// Exposed for protocol routing; NNTP also requires an exact local capability check.
    static var isUsingNativeServer: Bool { usingNativeServer }

    /// Publication requires this exact child to be running AND to have bound its socket. A stale
    /// Node listener on 11470 cannot become the native endpoint while the child retries its bind.
    static var nativeBaseURL: String? {
        guard usingNativeServer else { return nil }
        let now = ProcessInfo.processInfo.systemUptime
        let snapshot = runtime.withLock { state -> (Process, String, Int?, Bool)? in
            guard !state.shutdownRequested, let published = state.nativePublication else { return nil }
            let mayRead = state.nativeBoundPort == nil &&
                (state.nativeReceiptReadAt < 0 || now - state.nativeReceiptReadAt >= 0.5)
            if mayRead { state.nativeReceiptReadAt = now }
            return (published.process, published.receipt, state.nativeBoundPort, mayRead)
        }
        guard let (child, receiptPath, cachedPort, mayRead) = snapshot,
              child.isRunning else { return nil }
        if let port = cachedPort { return "http://127.0.0.1:\(port)" }
        guard mayRead else { return nil }
        // Foundation status and filesystem I/O never execute while holding runtime's lock.
        let receipt = try? String(contentsOfFile: receiptPath, encoding: .utf8)
        guard let port = NativeTransportPolicy.boundNativePort(processRunning: true, receipt: receipt) else { return nil }
        let stillPublished = runtime.withLock { state in
            guard !state.shutdownRequested, let published = state.nativePublication,
                  published.process === child, published.receipt == receiptPath else { return false }
            state.nativeBoundPort = port
            return true
        }
        guard stillPublished, child.isRunning else { return nil }
        return "http://127.0.0.1:\(port)"
    }

    private static func publishNativeProcess(_ process: Process?, receipt: String? = nil) {
        let previous = runtime.withLock { state in
            let previous = state.nativePublication
            if let process, let receipt { state.nativePublication = (process, receipt) }
            else { state.nativePublication = nil }
            state.nativeBoundPort = nil
            state.nativeReceiptReadAt = -1
            return previous
        }
        withExtendedLifetime(previous) {}
    }

    /// Locate an ffmpeg/ffprobe pair the server can use for VideoToolbox transcoding. server.js
    /// searches a fixed set of paths but NOT Homebrew's Apple-silicon prefix (/opt/homebrew/bin),
    /// so on most Macs it finds nothing and transcoding silently no-ops. We probe the common
    /// locations and, when found, hand the pair to node via FFMPEG_BIN / FFPROBE_BIN (the first
    /// entries server.js honours). With ffmpeg present, server.js auto-detects the macOS
    /// `videotoolbox` hw-accel profile on boot and uses h264_videotoolbox / hevc_videotoolbox.
    private static func ffmpegBinaries() -> (ffmpeg: String, ffprobe: String)? {
        // SECURITY NOTE: we hand whichever ffmpeg/ffprobe we find here to node, which
        // execs it for transcoding. There is no signature/ownership check, so we trust
        // whatever sits at these paths. Homebrew prefixes are world-writable on many
        // Macs, so we probe the system path (/usr/bin) FIRST and only fall back to a
        // Homebrew-installed binary when the system has none (macOS ships no ffmpeg by
        // default, so the Homebrew fallback is what actually enables transcoding).
        let prefixes = [
            "/usr/bin",            // system (preferred: not world-writable)
            "/opt/homebrew/bin",   // Homebrew, Apple silicon
            "/usr/local/bin",      // Homebrew, Intel / manual installs
        ]
        let fm = FileManager.default
        for prefix in prefixes {
            let ff = "\(prefix)/ffmpeg", fp = "\(prefix)/ffprobe"
            if fm.isExecutableFile(atPath: ff) && fm.isExecutableFile(atPath: fp) {
                return (ff, fp)
            }
        }
        return nil
    }

    /// Whether VideoToolbox transcoding can run: a discoverable ffmpeg/ffprobe pair exists.
    /// (server.js always carries the darwin `videotoolbox` profile and enables it by default.)
    static var canTranscode: Bool { ffmpegBinaries() != nil }

    /// Checked restart-only snapshot for UI readers; never joins the lifecycle queue or reads
    /// the unrelated started/exitCode state. A retained child's late exit changes this message.
    static var restartFailureDescription: String? { restartStatus.message }

    /// One-line state for the Settings diagnostics (mirrors the iOS/tvOS NodeServer wording).
    static var statusDescription: String {
        if PlaybackSettings.torrentsDisabled { return "Disabled by Direct Links Only" }
        let status = runtime.withLock { ($0.started, $0.exitCode, $0.quitFailure) }
        if let failure = status.2 { return failure }
        if let message = restartFailureDescription { return message }
        if usingNativeServer {
            if nativeServerBinary == nil { return "Native streaming server is missing from this build." }
            if !status.0 { return "Not started (native server)" }
            if let code = status.1 { return "Native server exited with code \(code). Relaunch the app to restart it." }
            if sharedOnLAN, let url = lanURL { return "Sharing on this network at \(url) (native server)" }
            return "Native server process running"
        }
        if Bundle.main.path(forResource: "node-darwin-arm64", ofType: nil) == nil {
            return "Not started (node binary missing from the bundle)"
        }
        if !status.0 { return "Not started (server.js missing from the bundle)" }
        if let code = status.1 { return "Server exited with code \(code). Relaunch the app to restart it." }
        if sharedOnLAN, let url = lanURL { return "Sharing on this network at \(url)" }
        return "Server process running"
    }

    /// The last lines of the server's own log (console output + crashes are teed to a file).
    static func logTail(_ lines: Int = 4) -> [String] {
        guard let text = try? String(contentsOfFile: logPath, encoding: .utf8) else { return [] }
        return text.split(separator: "\n").suffix(lines).map(String.init)
    }

    /// Spawn the node server once. Idempotent. No-op if the node binary or server.js is missing,
    /// or if the app is already shutting down (so a late call can't resurrect a killed server).
    static func startIfNeeded() {
        // Wire the shared diagnostics indirection so the VXProbe heartbeat + the diagnostics export can
        // surface this server's state without SourcesShared referencing a concrete NodeServer. Idempotent.
        ServerDiagnostics.register(status: { statusDescription }, logTail: { logTail($0) })
        queue.async {
            guard !started, !shutdownRequested else { return }
            // Native selection is terminal here, including an unavailable bundle.
            if nativeServerEnabled {
                if let nativeBin = nativeServerBinary {
                    reclaimStalePort()
                    started = true
                    spawnNative(binPath: nativeBin)
                    return
                }
                DiagnosticsLog.log("server", "native streaming selected but server binary is missing")
                return
            }
            guard let nodeBin = Bundle.main.path(forResource: "node-darwin-arm64", ofType: nil) else {
                NSLog("StremioX: node binary not found in bundle, streaming server disabled")
                return
            }
            guard let serverJs = Bundle.main.path(forResource: "server", ofType: "js") else {
                NSLog("StremioX: server.js not found in bundle, streaming server disabled")
                return
            }
            // A prior run that was force-killed / crashed before `stop()` could fire may have left a
            // node child reparented to launchd, still holding 11470. Clear that stale orphan before we
            // spawn, so this launch gets a free port instead of a server that can't bind.
            reclaimStalePort()
            started = true
            spawn(nodeBin: nodeBin, scriptPath: serverJs)
        }
    }

    /// Terminate the running node process (if any) and relaunch it. Used when the LAN-sharing
    /// toggle flips, so the new bind (loopback vs all-interfaces) takes effect without an app
    /// restart. No-op if the server was never eligible to start (missing binary / disabled) or if
    /// the app is shutting down. This is a RESTART, NOT a shutdown: it does NOT set
    /// `shutdownRequested`, so the server comes back up afterwards.
    static func restart() {
        queue.async {
            guard !shutdownRequested, !PlaybackSettings.torrentsDisabled else { return }
            runtime.withLock { $0.quitFailure = nil }
            // Reap whichever engine is running (node or native), then respawn per the flag, so
            // both the LAN-bind toggle and the native-engine toggle swap in place.
            func reapCurrent() -> Bool {
                publishNativeProcess(nil)
                if let proc = process {
                    proc.terminationHandler = nil   // expected stop; don't surface it as a crash
                    guard MacNodeRestartPolicy.requestTermination(
                        grace: terminateGrace,
                        isRunning: { proc.isRunning },
                        terminate: { proc.terminate() }
                    ) else {
                        // Retain this exact child and receipt. A later retry/stop still owns it;
                        // a replacement must not overwrite ownership or assume the port is free.
                        restartStatus.record(owner: proc, isRunning: { proc.isRunning })
                        DiagnosticsLog.log("server", "restart did not complete within TERM grace; existing child retained")
                        return false
                    }
                }
                process = nil
                exitCode = nil
                retireNativePortReceipt()
                restartStatus.clear()
                return true
            }
            if nativeServerEnabled {
                guard reapCurrent() else { return }
                started = false
                guard let nativeBin = nativeServerBinary else {
                    DiagnosticsLog.log("server", "native streaming selected but server binary is missing")
                    return
                }
                started = true
                spawnNative(binPath: nativeBin)
                return
            }
            guard let nodeBin = Bundle.main.path(forResource: "node-darwin-arm64", ofType: nil),
                  let serverJs = Bundle.main.path(forResource: "server", ofType: "js") else { return }
            guard reapCurrent() else { return }
            started = true
            spawn(nodeBin: nodeBin, scriptPath: serverJs)
        }
    }

    /// Ask AppKit to defer quit while this request observes the exact child exiting. A deadline
    /// replies false and restores normal operation; it never discards a live child or its receipt.
    /// The callback fires once for this attempt, even if launch/TERM outlives the deadline.
    static func stop(completion: @escaping @MainActor @Sendable (Bool) -> Void) {
        let id = UUID()
        let accepted = runtime.withLock { state in
            guard state.quitAttempt == nil else { return false }
            state.quitAttempt = QuitAttempt(id: id, completion: completion)
            state.shutdownRequested = true
            state.quitFailure = nil
            return true
        }
        guard accepted else {
            Task { @MainActor in completion(false) }
            return
        }
        let deadline = ProcessInfo.processInfo.systemUptime + quitGrace
        DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + quitGrace + 0.25) {
            _ = completeQuitAttempt(id, stopped: false, owner: nil)
        }
        queue.async {
            guard runtime.withLock({ $0.quitAttempt?.id == id }) else { return }
            guard let proc = process else {
                _ = completeQuitAttempt(id, stopped: true, owner: nil)
                return
            }
            let previousHandler = proc.terminationHandler
            proc.terminationHandler = nil
            let stopped = MacNodeRestartPolicy.requestTermination(
                grace: max(0, deadline - ProcessInfo.processInfo.systemUptime),
                isRunning: { proc.isRunning },
                terminate: {
                    // Liveness/handler access above may have outlived this quit attempt.
                    // Claim this exact action only now, then leave the lock before Foundation.
                    guard admitQuitTermination(id, owner: proc) else { return }
                    // Admission is irrevocable: cancellation can win after this check but
                    // cannot retract the Foundation call already admitted for this child.
                    proc.terminate()
                }
            )
            let resolved = completeQuitAttempt(id, stopped: stopped, owner: proc)
            // A cancelled attempt may already have issued TERM. Restore observation for the
            // retained child; a late exit is recorded without acknowledging an old quit request.
            if (!resolved || !stopped), process === proc {
                proc.terminationHandler = previousHandler
                if !proc.isRunning {
                    exitCode = proc.terminationStatus
                    retireNativePortReceipt()
                    DiagnosticsLog.log("server", "child exited after quit was cancelled; retry Quit or Restart streaming server")
                }
            }
        }
    }

    /// Admit at most one TERM for the exact active quit and child. No Process method, status
    /// getter, or callback runs under this lock. The caller must invoke the admitted action
    /// outside the lock; a later cancellation cannot retract that already-admitted action.
    private static func admitQuitTermination(_ id: UUID, owner: Process) -> Bool {
        runtime.withLock { state in
            guard state.shutdownRequested,
                  var attempt = state.quitAttempt, attempt.id == id,
                  state.process === owner, !attempt.termAdmitted else { return false }
            attempt.termAdmitted = true
            state.quitAttempt = attempt
            return true
        }
    }

    /// Winner selection and the quit fence share the same lock as the child owner. Old timers
    /// and late queue completions cannot clear a newer attempt, change its fence, or reply twice.
    @discardableResult
    private static func completeQuitAttempt(_ id: UUID, stopped: Bool, owner: Process?) -> Bool {
        let resolution = runtime.withLock { state -> (QuitAttempt, String?, Process?)? in
            guard let attempt = state.quitAttempt, attempt.id == id else { return nil }
            if stopped, state.process !== owner { return nil }
            let retained = state.process
            state.quitAttempt = nil
            if stopped {
                let receipt = state.nativePortReceipt
                state.shutdownRequested = true
                state.started = false
                state.exitCode = nil
                state.process = nil
                state.nativePortReceipt = nil
                state.nativePublication = nil
                state.nativeBoundPort = nil
                state.nativeReceiptReadAt = -1
                state.quitFailure = nil
                return (attempt, receipt, retained)
            }
            state.shutdownRequested = false
            state.quitFailure = "Quit was canceled because streaming-server shutdown was not confirmed. You can retry Quit."
            // Publication stayed retained behind the quit fence. Canceling exposes that same
            // snapshot, never inventing a publication suppressed by an earlier failed restart.
            return (attempt, nil, retained)
        }
        guard let resolution else { return false }
        let completion = resolution.0.completion
        Task { @MainActor in completion(stopped) }
        // The reply depends on observed child exit, not filesystem cleanup. Never hold the
        // state lock across FileManager or across the release of a retained Process/callback.
        if stopped {
            restartStatus.clear()
            if let receipt = resolution.1 { try? FileManager.default.removeItem(atPath: receipt) }
        }
        withExtendedLifetime(resolution.2) {}
        return true
    }

    /// Park ownership before Foundation launch can block or the child can exit. A queued quit
    /// suppresses launch; a canceled quit restores the ability to start normally.
    private static func beginLaunch(_ child: Process) -> Bool {
        runtime.withLock { state in
            guard !state.shutdownRequested, state.process == nil else { return false }
            state.process = child
            return true
        }
    }

    // MARK: - Private

    /// The server's writable app-data root. The server reads HOME for its cache/settings path; we
    /// point it at a per-user Application Support dir (Caches would be purgeable mid-stream).
    private static var serverHome: String {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return support.appendingPathComponent("StremioX").path
    }

    private static var logPath: String {
        (serverHome as NSString).appendingPathComponent("stremio-server.log")
    }

    // MARK: - Stale-port reclaim

    /// Reclaim port 11470 if a STALE copy of OUR OWN streaming server (node or native) is still
    /// holding it, e.g. a prior run that was force-killed / crashed before `stop()` could fire,
    /// leaving the child reparented to launchd (PPID 1). The node preload's parent-death watchdog
    /// stops new node orphans from forming; this clears any that predate it (or slipped through its
    /// ~1s poll window) plus any native-server orphan (which has no watchdog). We match "ours"
    /// narrowly, argv referencing the `stremiox-preload.js` we inject or the
    /// `vortx-streaming-server` binary name, markers nothing else uses,
    /// so an unrelated process that merely happens to hold the port is left untouched (we log it and
    /// let server.js fail to bind, surfacing the exit in Settings, rather than killing a stranger).
    /// SIGTERM first, escalate to SIGKILL, mirroring `stop()`. Cheap on the common path: a free port
    /// costs one `lsof` that returns nothing.
    private static func reclaimStalePort() {
        for pid in listeners(onPort: 11470) where isOurStreamingServer(pid) {
            NSLog("%@", "StremioX: reclaiming port 11470 from a stale streaming server (pid \(pid))")
            kill(pid, SIGTERM)
            let deadline = Date().addingTimeInterval(terminateGrace)
            while pidIsAlive(pid) && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if pidIsAlive(pid) { kill(pid, SIGKILL) }
        }
    }

    /// PIDs holding a LISTEN socket on `port` (via `lsof`); empty when the port is free. Any failure to
    /// run or parse `lsof` yields no PIDs, so we simply skip reclaiming.
    private static func listeners(onPort port: Int) -> [pid_t] {
        let out = runTool("/usr/sbin/lsof", ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN", "-t"]) ?? ""
        return out.split(whereSeparator: \.isNewline)
            .compactMap { pid_t(String($0).trimmingCharacters(in: .whitespaces)) }
    }

    /// True if `pid` is one of OUR streaming servers: a node child (the `stremiox-preload.js`
    /// marker we inject into its argv, which nothing else uses) or a native child (the
    /// `vortx-streaming-server` binary name in its argv). Read via `ps -o command=`. Either
    /// engine reclaims either engine's stale orphan, so an engine-flag flip between runs can
    /// never leave the old engine squatting on 11470.
    private static func isOurStreamingServer(_ pid: pid_t) -> Bool {
        let command = runTool("/bin/ps", ["-o", "command=", "-p", String(pid)]) ?? ""
        return command.contains("stremiox-preload.js") || command.contains("vortx-streaming-server")
    }

    /// `kill(pid, 0)` probes for existence without delivering a signal: success ⇒ the process is alive.
    private static func pidIsAlive(_ pid: pid_t) -> Bool { kill(pid, 0) == 0 }

    /// Run a short helper tool to completion and return its stdout (nil on launch failure). Used only
    /// for the small, bounded `lsof`/`ps` probes above; output is tiny, so reading then waiting can't
    /// deadlock on a full pipe.
    private static func runTool(_ launchPath: String, _ args: [String]) -> String? {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: launchPath)
        proc.arguments = args
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
        } catch {
            return nil
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        return String(data: data, encoding: .utf8)
    }

    private static func spawn(nodeBin: String, scriptPath: String) {
        let home = serverHome
        let serverData = (home as NSString).appendingPathComponent("stremio-server")
        try? FileManager.default.createDirectory(atPath: serverData, withIntermediateDirectories: true)

        // Tee console + uncaught errors to a log file (the server's own stdout/stderr are also
        // redirected to it below). Lets a dead/misbehaving server explain itself in Settings.
        //
        // It also gates the bind address: server.js always calls `server.listen(port)` with no
        // host, which Node interprets as 0.0.0.0 (every interface) -- i.e. ALWAYS LAN-reachable.
        // We don't want that unless the user opted in. The preload monkeypatches
        // net.Server.prototype.listen so that, when sharing is OFF, any host-less listen is
        // pinned to 127.0.0.1 (loopback only, the original private behaviour). When sharing is
        // ON we leave Node's default, so the Mac serves the whole LAN. The server still computes
        // its own enginefs.baseUrl from ip.address(), which we surface in Settings as the LAN URL.
        //
        // Third, it installs a parent-death watchdog. `applicationWillTerminate` -> stop() only fires
        // on a CLEAN quit; on a crash / Force Quit / SIGKILL the app dies without it, and Foundation
        // does NOT signal this child, so it gets reparented to launchd and keeps holding 11470 forever
        // (the orphaned-:11470 leak). The watchdog records our parent pid and exits the child the
        // moment it changes, i.e. once we've been reparented, releasing the port within ~1s.
        let bindHost = sharedOnLAN ? "0.0.0.0" : "127.0.0.1"
        let preloadPath = (home as NSString).appendingPathComponent("stremiox-preload.js")
        let preload = """
        const fs=require('fs'),L=\(jsString(logPath));
        const w=(t,a)=>{try{fs.appendFileSync(L,t+' '+Array.prototype.map.call(a,String).join(' ')+'\\n')}catch(e){}};
        process.on('uncaughtException',function(e){w('[uncaught]',[e&&e.stack||e])});
        process.on('unhandledRejection',function(e){w('[rej]',[e&&e.stack||e])});
        try{
          const net=require('net'),HOST=\(jsString(bindHost)),orig=net.Server.prototype.listen;
          net.Server.prototype.listen=function(){
            const a=Array.prototype.slice.call(arguments);
            // Only rewrite the simple `listen(port[, cb])` form server.js uses for the HTTP(S)
            // endpoints. If a host (string 2nd arg) or an options object is already given, leave it.
            if(typeof a[0]==='number' && (a.length===1 || typeof a[1]==='function')){
              const cb=a[1]; a[1]=HOST; if(cb)a[2]=cb;
              w('[bind]',['listen',a[0],'->',HOST]);
            }
            return orig.apply(this,a);
          };
          w('[boot]',['mac preload active; bind='+HOST]);
        }catch(e){w('[bind-err]',[e&&e.stack||e]);}
        // parent-death watchdog (see the Swift note above): if the app dies WITHOUT calling stop()
        // (crash / Force Quit / SIGKILL), Foundation reparents us to launchd and we'd keep holding
        // 11470. Exit once our parent pid changes so the orphaned port is always released. .unref()
        // keeps this poll timer from holding the process open on its own.
        const PPID0=process.ppid;
        setInterval(function(){if(process.ppid!==PPID0){w('[watchdog]',['parent gone; exiting']);process.exit(0);}},1000).unref();
        """
        try? preload.write(toFile: preloadPath, atomically: true, encoding: .utf8)

        // Keep the tail of the previous boot's log instead of wiping it, so a crash that took the
        // whole app down leaves its last lines readable after relaunch. Capped so it can't grow.
        let prior = (try? String(contentsOfFile: logPath, encoding: .utf8)) ?? ""
        let keptTail = prior.count > 48_000 ? String(prior.suffix(48_000)) : prior
        try? (keptTail + "\n===== BOOT =====\n").write(toFile: logPath, atomically: true, encoding: .utf8)

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: nodeBin)
        proc.arguments = ["-r", preloadPath, scriptPath]
        proc.currentDirectoryURL = URL(fileURLWithPath: home)

        var env = ProcessInfo.processInfo.environment
        env["HOME"] = home               // server reads HOME for its app-data path
        env["APP_PATH"] = serverData      // torrent cache + settings
        env["NO_CORS"] = "1"
        // Disable Chromecast/DLNA discovery. The native Mac UI has no cast feature, and the
        // server's SSDP multicast loop is pure overhead here. Matches the iOS/tvOS embed config.
        env["CASTING_DISABLED"] = "1"
        // More libuv workers for tracker DNS + the engine's disk/crypto (same rationale as iOS).
        env["UV_THREADPOOL_SIZE"] = "16"
        // Point the server at a real ffmpeg/ffprobe so HLS transcoding works and VideoToolbox
        // hardware acceleration (h264_videotoolbox / hevc_videotoolbox) kicks in. server.js's
        // built-in search misses Homebrew's Apple-silicon prefix, so without this it finds no
        // ffmpeg and transcoding is a silent no-op. FFMPEG_BIN / FFPROBE_BIN are the first paths
        // server.js consults. With ffmpeg present it auto-profiles the darwin `videotoolbox`
        // hw-accel on boot (transcodeHardwareAccel defaults on) and uses the GPU encoders.
        if let bins = ffmpegBinaries() {
            env["FFMPEG_BIN"] = bins.ffmpeg
            env["FFPROBE_BIN"] = bins.ffprobe
            NSLog("%@", "StremioX: ffmpeg for transcoding: \(bins.ffmpeg) (VideoToolbox hw-accel)")
        } else {
            NSLog("StremioX: no ffmpeg found; HLS transcoding disabled (install via `brew install ffmpeg`)")
        }
        proc.environment = env

        // Redirect the node process's own stdout/stderr to the same log file we tee console into.
        if let fh = FileHandle(forWritingAtPath: logPath) {
            fh.seekToEndOfFile()
            proc.standardOutput = fh
            proc.standardError = fh
        }

        // Fires on a background thread only for an UNEXPECTED exit (crash): stop()/restart() detach
        // this handler before terminating, so an intentional kill never lands here. Route through
        // the serial queue so the state write is race-free, and ignore it once shutdown began.
        proc.terminationHandler = { p in
            queue.async {
                guard !shutdownRequested, process === p,
                      !restartStatus.matches(owner: p) else { return }
                exitCode = p.terminationStatus
                NSLog("%@", "StremioX: node server exited rc=\(p.terminationStatus)")
                // Mirror the unexpected exit into the EXPORTABLE diagnostics log so a server death is visible
                // in the file the owner actually sends, not just the unified log.
                DiagnosticsLog.log("server", "node child exited rc=\(p.terminationStatus) (unexpected; relaunch to restart)")
            }
        }

        do {
            NSLog("%@", "StremioX: starting node streaming server (bin=\(nodeBin), HOME=\(home))")
            guard beginLaunch(proc) else { started = false; return }
            try proc.run()
        } catch {
            if process === proc { process = nil }
            started = false
            NSLog("%@", "StremioX: failed to launch node server: \(error)")
        }
    }

    /// Spawn the NATIVE Rust streaming server (`vortx-streaming-server`). Same child-process
    /// lifecycle as the node path (spawn here, SIGTERM via stop()/restart(), crash surfaced by
    /// the terminationHandler, stdout/stderr teed into the same Settings log), but none of the
    /// node scaffolding: no preload monkeypatch (the bind host is a plain env var), no ffmpeg
    /// discovery (the native server has no transcoding path to feed), and no parent-death
    /// watchdog (it exits cleanly on SIGTERM; a crash orphan is cleared by reclaimStalePort on
    /// the next launch, and its own deterministic 11470 bind retries instead of drifting ports).
    private static func spawnNative(binPath: String) {
        let home = serverHome
        let serverData = (home as NSString).appendingPathComponent("stremio-server")
        try? FileManager.default.createDirectory(atPath: serverData, withIntermediateDirectories: true)

        // Same keep-the-tail log policy as the node path, so both engines explain themselves in
        // Settings through the same file and a crash's last lines survive the relaunch.
        let prior = (try? String(contentsOfFile: logPath, encoding: .utf8)) ?? ""
        let keptTail = prior.count > 48_000 ? String(prior.suffix(48_000)) : prior
        try? (keptTail + "\n===== BOOT (native) =====\n").write(toFile: logPath, atomically: true, encoding: .utf8)

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: binPath)
        proc.currentDirectoryURL = URL(fileURLWithPath: home)

        // The native server's whole config is environment (see the engine's
        // crates/streaming-server/src/main.rs):
        //   VORTX_SERVER_HOME  data root (settings.json + stremio-cache/), the same
        //                      <AppSupport>/StremioX/stremio-server dir the node server uses
        //   VORTX_PORT_FILE    a unique per-launch bind receipt, read before publishing the endpoint
        //   VORTX_SERVER_PORT fixed 11470, overriding an inherited development port
        //   VORTX_BIND         0.0.0.0 (LAN sharing ON) vs 127.0.0.1 (private, the default),
        //                      replacing the node preload's listen() monkeypatch
        //   VORTX_LAN_IP       the advertised /settings baseUrl host while sharing
        // It binds 11470 DETERMINISTICALLY (bounded retry, then exit nonzero; never drifts to
        // 11471+), so lanURL's fixed :11470 and StremioServer.embedded stay correct.
        var env = ProcessInfo.processInfo.environment
        env["VORTX_SERVER_HOME"] = serverData
        retireNativePortReceipt()
        nativePortReceipt = (home as NSString).appendingPathComponent("native-server-\(UUID().uuidString).port")
        env["VORTX_PORT_FILE"] = nativePortReceipt
        env["VORTX_SERVER_PORT"] = "11470"
        if sharedOnLAN {
            env["VORTX_BIND"] = "0.0.0.0"
            if let ip = lanIP { env["VORTX_LAN_IP"] = ip }
        } else {
            env["VORTX_BIND"] = "127.0.0.1"
        }
        proc.environment = env

        // Tee the server's stdout/stderr into the same log file Settings reads (logTail).
        if let fh = FileHandle(forWritingAtPath: logPath) {
            fh.seekToEndOfFile()
            proc.standardOutput = fh
            proc.standardError = fh
        }

        // Same crash-surfacing contract as the node path: fires only for an UNEXPECTED exit
        // (stop()/restart() detach it first), routed through the serial queue.
        proc.terminationHandler = { p in
            queue.async {
                guard !shutdownRequested, process === p,
                      !restartStatus.matches(owner: p) else { return }
                exitCode = p.terminationStatus
                retireNativePortReceipt()
                NSLog("%@", "StremioX: native streaming server exited rc=\(p.terminationStatus)")
                DiagnosticsLog.log("server", "native server exited rc=\(p.terminationStatus) (unexpected; relaunch to restart)")
            }
        }

        do {
            NSLog("%@", "StremioX: starting native streaming server (bin=\(binPath), home=\(serverData))")
            guard beginLaunch(proc) else {
                started = false
                retireNativePortReceipt()
                return
            }
            try proc.run()
            publishNativeProcess(proc, receipt: nativePortReceipt)
        } catch {
            if process === proc { process = nil }
            started = false
            retireNativePortReceipt()
            NSLog("%@", "StremioX: failed to launch native streaming server: \(error)")
        }
    }

    /// Queue-only cleanup after the owning process was observed exited or failed to launch.
    private static func retireNativePortReceipt() {
        publishNativeProcess(nil)
        guard let path = nativePortReceipt else { return }
        nativePortReceipt = nil
        try? FileManager.default.removeItem(atPath: path)
    }

    /// JSON-encode a string for safe embedding in the preload JS literal.
    private static func jsString(_ s: String) -> String {
        let data = try? JSONSerialization.data(withJSONObject: [s])
        let arr = data.flatMap { String(data: $0, encoding: .utf8) } ?? "[\"\"]"
        return String(arr.dropFirst().dropLast())   // unwrap the [ ... ] → the quoted string
    }
}
#endif
