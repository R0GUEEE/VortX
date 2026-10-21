import Foundation

/// Downloads a subtitle that a SELF-HOSTED add-on (LAN, loopback or tailnet) serves from its own origin.
///
/// Every other external subtitle uses the pinned public transport, which refuses private addresses and plain
/// HTTP by design. That correctly blocks a public add-on or a community row from steering the player at the
/// user's network, but it also blocks the one case the user chose: an add-on they installed on their own
/// server. Only a row `SubtitleAddonService` classified as coming from that add-on's own origin reaches this
/// path, and the add-on transport still enforces that origin, re-validates every redirect, keeps TLS
/// verification, the 8 MiB subtitle ceiling, the deadline and cancellation.
enum PrivateAddonSubtitle {
    typealias Completion = @Sendable (_ data: Data?, _ mimeType: String?) -> Void

    static func fetch(_ url: URL, origin: URL, timeout: TimeInterval, retriesLeft: Int = 1,
                      done: @escaping Completion) {
        // Same origin (scheme, host and port) as the installed add-on, or nothing: a self-hosted add-on
        // cannot hand out a URL on some other private server.
        guard AddonURLGuard.AccessScope.addon(origin: origin).allowsPrivateAddress(for: url) else {
            done(nil, nil)
            return
        }
        Task.detached(priority: .utility) {
            do {
                let response = try await AddonURLGuard.fetch(
                    url, context: .addonSubtitle, access: .addon(origin: origin), timeout: max(0.1, timeout))
                done(response.data.isEmpty ? nil : response.data, response.mimeType)
            } catch AddonURLGuard.Rejection.unresolvable where retriesLeft > 0 {
                // A network failure or timeout gets the same single retry as the pinned path. A policy
                // rejection (address, scheme, redirect) is final.
                fetch(url, origin: origin, timeout: timeout, retriesLeft: retriesLeft - 1, done: done)
            } catch {
                done(nil, nil)
            }
        }
    }
}
