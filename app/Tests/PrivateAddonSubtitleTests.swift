// Loopback contract for the self-hosted add-on subtitle transport. Run through
// scripts/test-private-addon-subtitles.sh, which starts two local fixture servers: ADDON_PORT plays the
// installed self-hosted add-on, OTHER_PORT plays a different private server on the same machine. Compiled
// with the production AddonURLGuard.swift and PrivateAddonSubtitle.swift; nothing leaves 127.0.0.1.
import Foundation

// The production pairing protocol owns this URL-size limit; this harness links no app or account runtime.
enum AddonPairingProtocol { static let maxManifestURLBytes = 2048 }

@main
enum PrivateAddonSubtitleTests {
    static func fetch(_ url: URL, origin: URL, timeout: TimeInterval = 5) async -> (Data?, String?) {
        await withCheckedContinuation { continuation in
            PrivateAddonSubtitle.fetch(url, origin: origin, timeout: timeout) { data, mime in
                continuation.resume(returning: (data, mime))
            }
        }
    }

    static func hits(_ base: String) async -> Int {
        guard let (data, _) = try? await URLSession.shared.data(from: URL(string: "\(base)/hits")!),
              let count = Int(String(decoding: data, as: UTF8.self)) else { return -1 }
        return count
    }

    static func main() async {
        let env = ProcessInfo.processInfo.environment
        guard let addonPort = env["ADDON_PORT"], let otherPort = env["OTHER_PORT"] else {
            fputs("FAIL  run through scripts/test-private-addon-subtitles.sh\n", stderr)
            exit(2)
        }
        let addon = "http://127.0.0.1:\(addonPort)"
        let other = "http://127.0.0.1:\(otherPort)"
        let origin = URL(string: "\(addon)/manifest.json")!
        var checks = 0
        func check(_ condition: Bool, _ label: String) {
            guard condition else { fputs("FAIL  \(label)\n", stderr); exit(1) }
            checks += 1
        }
        let expected = Data("1\n00:00:01,000 --> 00:00:02,000\nhello\n".utf8)

        let (own, mime) = await fetch(URL(string: "\(addon)/sub.srt")!, origin: origin)
        check(own == expected, "the add-on's own subtitle downloads over plain HTTP on a private address")
        check(mime == "application/x-subrip", "the response type reaches the caller for the file extension")

        let (sameRedirect, _) = await fetch(URL(string: "\(addon)/redirect-same")!, origin: origin)
        check(sameRedirect == expected, "a redirect within the add-on's own origin is followed")

        let (otherRedirect, _) = await fetch(URL(string: "\(addon)/redirect-other")!, origin: origin)
        check(otherRedirect == nil, "a redirect to a different private server is refused")
        check(await hits(other) == 0, "the refused redirect target never received a request")

        let (crossOrigin, _) = await fetch(URL(string: "\(other)/sub.srt")!, origin: origin)
        check(crossOrigin == nil, "a URL on another private origin is refused")
        check(await hits(other) == 0, "the cross-origin URL was refused before any request")

        let (oversize, _) = await fetch(URL(string: "\(addon)/big")!, origin: origin)
        check(oversize == nil, "a body over the 8 MiB subtitle ceiling is refused")
        let (unsized, _) = await fetch(URL(string: "\(addon)/big-unsized")!, origin: origin)
        check(unsized == nil, "an undeclared-length body is cut off at the 8 MiB ceiling while streaming")

        let (missing, _) = await fetch(URL(string: "\(addon)/missing")!, origin: origin)
        check(missing == nil, "an error status yields no subtitle")

        let (empty, _) = await fetch(URL(string: "\(addon)/empty")!, origin: origin)
        check(empty == nil, "an empty body yields no subtitle")

        let started = Date()
        let (slow, _) = await fetch(URL(string: "\(addon)/slow")!, origin: origin, timeout: 1)
        check(slow == nil && Date().timeIntervalSince(started) < 6, "the deadline bounds a stalled add-on (one retry)")

        print("PASS self-hosted add-on subtitle transport: \(checks) checks over loopback only")
    }
}
