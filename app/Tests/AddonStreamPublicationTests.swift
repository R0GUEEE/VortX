import Foundation
import os

// No app, account, Keychain, provider requests, stream start or native engine is used. The
// production resource projection and complete CoreModels decode native-shaped in-memory fixtures.
// The actual CoreBridge predicates, accepted-publication branch and assembly are extracted unchanged.
@main @MainActor private enum AddonStreamPublicationTests {
    static var checks = 0
    static var failures = 0
    static func check(_ value: Bool, _ label: String) {
        checks += 1
        if !value { failures += 1 }
        print("\(value ? "PASS" : "FAIL") \(label)")
    }
    static let base = "https://provider.invalid/synthetic-config/manifest.json"
    static let original: [String: VortxJSON] = ["url": .string("https://media.invalid/old"), "name": .string("Synthetic source")]

    static func details(_ items: [[String: VortxJSON]], base: String = "https://provider.invalid/synthetic-config/manifest.json",
                        pathID: String = "synthetic-title:1:1", type: String = "series",
                        embedded: Bool = false, status: VortxResourceGroup.Status = .ready,
                        hostState: String? = nil, metadataName: String = "Synthetic title") throws -> CoreMetaDetails {
        let metaRequest = VortxResourceRequest(resource: .meta, type: type, id: "synthetic-title")
        let streamRequest = VortxResourceRequest(resource: .stream, type: type, id: pathID)
        let registry = [VortxResourceAddon(id: "synthetic-addon", transportUrl: base, manifest: nil)]
        var metadata: [String: VortxJSON] = ["id": .string("synthetic-title"), "type": .string(type), "name": .string(metadataName)]
        if embedded {
            metadata["videos"] = .array([.object(["id": .string(pathID), "title": .string("Synthetic episode"), "streams": .array(items.map(VortxJSON.object))])])
        }
        let meta = VortxResourceSnapshot(ownerID: "synthetic-owner", requestID: "synthetic-meta", generation: 1,
            request: metaRequest, groups: [.init(addonId: "synthetic-addon", status: .ready,
                content: .object(["meta": .object(metadata)]), error: nil)], sourceURLs: ["synthetic-addon": base])
        let stream = VortxResourceSnapshot(ownerID: "synthetic-owner", requestID: "synthetic-stream", generation: 1,
            request: streamRequest, groups: embedded ? [] : [.init(addonId: "synthetic-addon", status: status,
                content: status == .ready ? .object(["streams": .array(items.map(VortxJSON.object))]) : nil,
                error: status == .ready ? nil : .init(code: "timeout"))], sourceURLs: ["synthetic-addon": base])
        let projected = try VortxResourceProjection.metaDetails(meta: meta, streams: stream,
            expectedStream: streamRequest, registry: registry)
        // Loading/missing content is host-owned initial state, not a terminal native response.
        if let hostState, case .object(var root) = projected,
           case .object(var row) = root["streams"]?.array?.first {
            row["content"] = hostState == "missing" ? nil : .object(["type": .string(hostState)])
            root["streams"] = .array([.object(row)])
            return try VortxJSON.object(root).decode(CoreMetaDetails.self)
        }
        return try projected.decode(CoreMetaDetails.self)
    }

    static func replacement(_ label: String, old: CoreMetaDetails, next: CoreMetaDetails) {
        let probe = AddonPublicationProbe()
        probe.names = [base: "Synthetic A", "https://replacement.invalid/manifest.json": "Synthetic B"]
        probe.accept(old)
        let before = probe.streamsEpoch
        check(probe.needs(next), "\(label) republishes")
        check(probe.streamsChange(next), "\(label) changes source epoch")
        probe.accept(next)
        let expected = AddonPublicationProbe(); expected.names = probe.names; expected.accept(next)
        check(probe.streamsEpoch == before + 1 && probe.groups() == expected.groups(), "\(label) accepted rows contain replacement")
        let after = probe.streamsEpoch
        probe.accept(next)
        check(probe.streamsEpoch == after, "\(label) identical repeat stays quiet")
    }

    static func metadataWire() -> [String: VortxJSON] {
        let path: VortxJSON = .object(["resource": .string("meta"), "type": .string("series"), "id": .string("synthetic-title")])
        let stream: VortxJSON = .object(["resource": .string("stream"), "type": .string("series"), "id": .string("synthetic-title:1:1")])
        let primary: VortxJSON = .object([
            "request": .object(["base": .string(base), "path": path]),
            "content": .object(["type": .string("Ready"), "content": .object([
                "id": .string("synthetic-title"), "type": .string("series"), "name": .string("Synthetic primary")])])])
        let secondary: VortxJSON = .object([
            "request": .object(["base": .string("https://secondary.invalid/manifest.json"), "path": path]),
            "content": .object(["type": .string("Loading")])])
        return ["selected": .object(["metaPath": path, "streamPath": stream]),
            "metaItems": .array([primary, secondary]), "streams": .array([]), "metaStreams": .array([])]
    }
    static func fields(_ value: VortxJSON) -> [String: VortxJSON] {
        guard case .object(let result) = value else { preconditionFailure("malformed synthetic fixture") }
        return result
    }
    static func replaceMetaRow(_ original: [String: VortxJSON], index: Int,
                               changes: [String: VortxJSON]) -> [String: VortxJSON] {
        var root = original, rows = root["metaItems"]!.array!
        var row = fields(rows[index])
        for (key, value) in changes { row[key] = value }
        rows[index] = .object(row); root["metaItems"] = .array(rows)
        return root
    }
    static func decoded(_ root: [String: VortxJSON]) throws -> CoreMetaDetails {
        try VortxJSON.object(root).decode(CoreMetaDetails.self)
    }
    static func metadataReplacement(_ label: String, old: CoreMetaDetails,
                                    next: CoreMetaDetails, sourceChange: Bool = false) {
        let probe = AddonPublicationProbe(); probe.accept(old)
        let epoch = probe.streamsEpoch, publications = probe.metadataPublications
        check(probe.needs(next), "\(label) whole decoded value republishes")
        check(probe.streamsChange(next) == sourceChange, "\(label) independent source-epoch policy")
        probe.accept(next)
        check(probe.metadataPublications == publications + 1, "\(label) actual accepted branch stores once")
        check(probe.streamsEpoch == epoch + (sourceChange ? 1 : 0), "\(label) actual source epoch")
        probe.accept(next)
        check(probe.metadataPublications == publications + 1, "\(label) identical repeat remains quiet")
    }
    static func metadataValueCases() throws {
        let root = metadataWire(), old = try decoded(root)
        check(Set(Mirror(reflecting: old).children.compactMap { $0.label })
            == Set(["selected", "metaItems", "streams", "metaStreams", "libraryItem", "watchedVideoIds"]),
            "metadata equality schema covers all six stored fields; new fields require gate review")
        let terminal = replaceMetaRow(root, index: 1,
            changes: ["content": .object(["type": .string("Err"), "content": .object(["code": .string("timeout")])])])
        metadataReplacement("nonwinning provider Loading to Err", old: old, next: try decoded(terminal))
        let ready = replaceMetaRow(root, index: 1,
            changes: ["content": .object(["type": .string("Ready"), "content": .object([
                "id": .string("synthetic-title"), "type": .string("series"), "name": .string("Synthetic secondary")])])])
        metadataReplacement("nonwinning provider Ready", old: old, next: try decoded(ready))
        let row = root["metaItems"]!.array![1]
        var request = fields(row["request"]!); request["base"] = .string("https://replacement.invalid/manifest.json")
        metadataReplacement("nonwinning provider request", old: old,
            next: try decoded(replaceMetaRow(root, index: 1, changes: ["request": .object(request)])))
        let retainedA: VortxJSON = .object(["type": .string("Ready"), "content": .object([
            "id": .string("synthetic-title"), "type": .string("series"), "name": .string("Retained A")])])
        let retainedB: VortxJSON = .object(["type": .string("Ready"), "content": .object([
            "id": .string("synthetic-title"), "type": .string("series"), "name": .string("Retained B")])])
        let prior = try decoded(replaceMetaRow(root, index: 1, changes: ["retainedWhileLoading": retainedA]))
        let next = try decoded(replaceMetaRow(root, index: 1, changes: ["retainedWhileLoading": retainedB]))
        metadataReplacement("nonwinning retained metadata", old: prior, next: next)
        var ordered = root
        ordered["metaItems"] = .array(Array(root["metaItems"]!.array!.reversed()))
        metadataReplacement("provider row order", old: old, next: try decoded(ordered))

        // Full selected route identity must change the source epoch even before
        // any group request exists to expose a type/resource/episode mismatch.
        for pathKey in ["metaPath", "streamPath"] {
            for field in ["resource", "type", "id"] {
                var changed = root, selected = fields(root["selected"]!)
                var path = fields(selected[pathKey]!)
                path[field] = .string("synthetic-replacement")
                selected[pathKey] = .object(path); changed["selected"] = .object(selected)
                metadataReplacement("selected \(pathKey).\(field) empty groups", old: old,
                    next: try decoded(changed), sourceChange: true)
            }
        }
        var library = root
        library["libraryItem"] = .object(["_id": .string("synthetic-title"), "type": .string("series"),
            "name": .string("Synthetic library"), "poster": .string("https://poster.invalid/old"),
            "removed": .bool(false), "temp": .bool(false), "previouslyWatched": .bool(false),
            "state": .object(["timeOffset": .integer(5), "duration": .integer(100),
                "video_id": .string("synthetic-title:1:1"), "lastWatched": .string("2026-10-24"),
                "flaggedWatched": .integer(0), "timesWatched": .integer(0)])])
        let libraryOld = try decoded(library)
        for (field, value) in [("type", VortxJSON.string("movie")), ("name", .string("Replacement name")),
                               ("poster", .string("https://poster.invalid/new")), ("removed", .bool(true)),
                               ("temp", .bool(true)), ("previouslyWatched", .bool(true))] {
            var changed = library, item = fields(library["libraryItem"]!)
            item[field] = value; changed["libraryItem"] = .object(item)
            metadataReplacement("library \(field)", old: libraryOld, next: try decoded(changed))
        }
        for (field, value) in [("timeOffset", VortxJSON.integer(6)), ("duration", .integer(101)),
                               ("video_id", .string("synthetic-title:1:2")), ("lastWatched", .string("2026-10-25")),
                               ("flaggedWatched", .integer(1)), ("timesWatched", .integer(1))] {
            var changed = library, item = fields(library["libraryItem"]!), state = fields(item["state"]!)
            state[field] = value; item["state"] = .object(state); changed["libraryItem"] = .object(item)
            metadataReplacement("library state \(field)", old: libraryOld, next: try decoded(changed))
        }
        var watched = root, reordered = root, changedWatched = root
        watched["watchedVideoIds"] = .array([.string("a"), .string("b")])
        reordered["watchedVideoIds"] = .array([.string("b"), .string("a"), .string("a")])
        changedWatched["watchedVideoIds"] = .array([.string("a"), .string("c")])
        let watchedOld = try decoded(watched), membershipProbe = AddonPublicationProbe()
        membershipProbe.accept(watchedOld); let membershipCount = membershipProbe.metadataPublications
        membershipProbe.accept(try decoded(reordered))
        check(membershipProbe.metadataPublications == membershipCount, "watched order and duplicate equivalence stays quiet")
        metadataReplacement("same-count watched membership", old: watchedOld, next: try decoded(changedWatched))
        var empty = root; empty["watchedVideoIds"] = .array([]); empty.removeValue(forKey: "metaStreams")
        check(old == (try decoded(empty)), "nil/empty watched and effective embedded-stream equivalence")

        // No raw-to-Double canonicalization, serialization cache, or unknown-field
        // rebuild is added by the production change. Native raw evidence stays raw.
        let extensionValue: VortxJSON = .object(["sequence": .unsigned(UInt64.max),
            "nested": .array([.null, .bool(true), .string("opaque-synthetic")])])
        var unknown = root; unknown["providerFuture"] = extensionValue
        let retainedRaw = VortxJSON.object(unknown), decodedUnknown = try retainedRaw.decode(CoreMetaDetails.self)
        check(retainedRaw["providerFuture"] == extensionValue, "raw unknown UInt64-max extension retained independently")
        check(old == decodedUnknown, "unknown-only raw change remains truly decoded-equivalent")
        let exact = Int64(9_007_199_254_740_993)
        let numeric = try details([["url": .string("https://media.invalid/precise"),
            "behaviorHints": .object(["videoSize": .integer(exact)])]])
        let string = try details([["url": .string("https://media.invalid/precise"),
            "behaviorHints": .object(["videoSize": .string(String(exact))])]])
        let adjacent = try details([["url": .string("https://media.invalid/precise"),
            "behaviorHints": .object(["videoSize": .integer(exact + 1)])]])
        check(numeric.allStreamGroups.first?.content?.ready?.first?.behaviorHints?.videoSize == exact,
            "decoded Int64 byte count above Double-exact range preserves precision")
        check(numeric == string, "exact numeric/string byte count remains decoded-equivalent")
        metadataReplacement("adjacent large Int64 byte count", old: numeric, next: adjacent, sourceChange: true)
    }

    static func finiteLargeValueCases() throws {
        let values: [[String: VortxJSON]] = (0..<3_000).map { index in
            ["url": .string("https://media.invalid/finite/\(index)"), "name": .string("Synthetic \(index)")]
        }
        let first = try details(values), equal = try details(values)
        var replacement = values; replacement[2_999]["url"] = .string("https://media.invalid/finite/tail-new")
        let tail = try details(replacement), probe = AddonPublicationProbe()
        probe.accept(first); let count = probe.metadataPublications, epoch = probe.streamsEpoch
        probe.accept(equal)
        check(probe.metadataPublications == count && probe.streamsEpoch == epoch, "3000-row functional equal receipt is quiet")
        probe.accept(tail)
        check(probe.metadataPublications == count + 1 && probe.streamsEpoch == epoch + 1,
            "3000-row functional tail replacement publishes once")
        check(probe.groups().first?.streams.count == 3_000, "3000-row functional projection retains all rows")
    }

    static func dateParserCases() {
        // Legacy instances stay local to the oracle; worker checks use only actual Sendable parsers.
        let plain = ISO8601DateFormatter()
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let inputs = ["2026-10-24T12:34:56Z", "2026-10-24T12:34:56.123Z",
            "2026-10-24T12:34:56.123456Z", "2026-10-24T12:34:56.123456789Z",
            "2026-10-24T12:34:56+05:30", "2026-10-24T12:34:56+0530",
            "2026-10-24T12:34:56-04:00", "2026-10-24T12:34:56.999+05:30",
            "", "not-a-date", "2026", "2026-10-24", "2026-99-24T12:34:56Z",
            "2026-10-24T99:00:00Z", "2026-10-24T12:34:56Z trailing"]
        let expected = inputs.map { raw in
            (plain.date(from: raw) ?? fractional.date(from: raw))?.timeIntervalSince1970
        }
        for (index, raw) in inputs.enumerated() {
            let date = (ISO8601DateFormatter.epg.date(from: raw)
                ?? ISO8601DateFormatter.epgFractional.date(from: raw))?.timeIntervalSince1970
            let reverse = (ISO8601DateFormatter.epgFractional.date(from: raw)
                ?? ISO8601DateFormatter.epg.date(from: raw))?.timeIntervalSince1970
            let same = date == expected[index]
            check(same && date == reverse, "ISO parser legacy date/acceptance parity case \(index)")
        }
        let failures = OSAllocatedUnfairLock(initialState: 0)
        DispatchQueue.concurrentPerform(iterations: 4) { _ in
            var missed = 0
            for (index, raw) in inputs.enumerated() {
                let date = (ISO8601DateFormatter.epg.date(from: raw)
                    ?? ISO8601DateFormatter.epgFractional.date(from: raw))?.timeIntervalSince1970
                let same = date == expected[index]
                if !same { missed += 1 }
            }
            failures.withLock { [missed] in $0 += missed }
        }
        check(failures.withLock { $0 } == 0, "shared actual ISO parser values support bounded worker reads")
    }

    static func finiteResumeTimecodeCases() {
        for value in [-1.0, 0, 0.99, Double.nan, .infinity, -.infinity,
                      Double(Int.max), Double.greatestFiniteMagnitude] {
            check(resumeTimecode(value) == nil, "nonpositive/nonfinite/out-of-Int duration rejected")
        }
        for (value, expected) in [(1.0, "0:01"), (59.99, "0:59"), (60, "1:00"),
                                  (3_600, "1:00:00"), (7_730_941_132_800, "2147483648:00:00")] {
            check(resumeTimecode(value) == expected, "resume timecode finite formatting \(expected)")
        }
        check(resumeTimecode(Double(Int.max).nextDown) != nil, "largest representable Double below Int bound formats safely")
    }

    static func timing() throws {
        let count = 3_000, iterations = 100
        let values: [[String: VortxJSON]] = (0..<count).map { index in
            ["url": .string("https://media.invalid/synthetic/\(index)"), "name": .string("Synthetic \(index)"),
             "description": .string("Synthetic description"), "fileIdx": .integer(Int64(index)),
             "behaviorHints": .object(["bingeGroup": .string("synthetic-group"), "filename": .string("synthetic.mkv"),
                 "proxyHeaders": .object(["request": .object(["Referer": .string("https://referrer.invalid/synthetic")])])])]
        }
        let first = try details(values), equal = try details(values)
        var changed = values
        changed[count - 1]["url"] = .string("https://media.invalid/synthetic/tail-replacement")
        let tail = try details(changed)
        let probe = AddonPublicationProbe(); probe.accept(first)
        let initialEpoch = probe.streamsEpoch
        let equalStart = DispatchTime.now().uptimeNanoseconds
        for _ in 0..<iterations { probe.accept(equal) }
        let equalElapsed = DispatchTime.now().uptimeNanoseconds - equalStart
        check(probe.streamsEpoch == initialEpoch, "3000 separately decoded equal streams do not republish")
        let tailStart = DispatchTime.now().uptimeNanoseconds
        for index in 0..<iterations { probe.accept(index.isMultiple(of: 2) ? tail : first) }
        let tailElapsed = DispatchTime.now().uptimeNanoseconds - tailStart
        check(probe.streamsEpoch == initialEpoch + iterations, "3000-stream tail changes all publish through both actual comparisons")
        check(probe.groups().first?.streams.count == count, "3000-stream timing keeps every row")
        print(String(format: "TIMING actual accepted branch streams=%d iterations=%d optimization=none equal_total_ms=%.3f equal_mean_ms=%.3f tail_total_ms=%.3f tail_mean_ms=%.3f",
            count, iterations, Double(equalElapsed) / 1_000_000, Double(equalElapsed) / Double(iterations) / 1_000_000,
            Double(tailElapsed) / 1_000_000, Double(tailElapsed) / Double(iterations) / 1_000_000))
    }

    static func main() throws {
        let statusProbe = AddonPublicationProbe()
        let currentRequest: [String: Any] = ["base": base,
            "path": ["resource": "stream", "type": "series", "id": "synthetic-title:1:2"]]
        let goodGroup: [String: Any] = ["request": currentRequest,
            "content": ["type": "Ready", "content": [["url": "https://media.invalid/current"]]]]
        check(statusProbe.readyCount(goodGroup) == 1, "current typed stream response has the exact ready count")
        let badGroup: [String: Any] = ["request": currentRequest,
            "content": ["type": "Ready", "content": [["url": 42]]]]
        check(statusProbe.readyCount(badGroup) == nil, "unreadable Ready stream is terminal rather than falsely reported ready")
        let missingGroup: [String: Any] = ["request": currentRequest, "content": ["type": "Ready"]]
        check(statusProbe.readyCount(missingGroup) == nil, "missing Ready stream payload is not reported as a successful empty response")
        let emptyGroup: [String: Any] = ["request": currentRequest, "content": ["type": "Ready", "content": []]]
        check(statusProbe.readyCount(emptyGroup) == 0, "valid successful empty response remains non-error")
        check(statusProbe.readyCount(["request": ["base": base], "content": ["type": "Ready", "content": []]]) == nil,
              "malformed current request remains failed rather than borrowing a previous episode")
        let old = try details([original])
        let changes: [(String, String, VortxJSON)] = [
            ("same-count URL", "url", .string("https://media.invalid/refreshed")),
            ("same-count torrent selector", "fileIdx", .integer(7)),
            ("same-count torrent hash", "infoHash", .string(String(repeating: "b", count: 40))),
            ("same-count tracker sources", "sources", .array([.string("tracker:synthetic")])),
            ("same-count external URL", "externalUrl", .string("https://external.invalid/new")),
            ("same-count YouTube selector", "ytId", .string("synthetic-new")),
            ("same-count NZB", "nzbUrl", .string("https://nzb.invalid/new.nzb")),
            ("same-count NZB mirrors", "nzbUrls", .array([.string("https://nzb.invalid/mirror.nzb")])),
            ("same-count NNTP hints", "servers", .array([.string("nntps://synthetic.invalid")])),
            ("same-count file matcher", "fileMustInclude", .string("episode-2")),
            ("same-count name", "name", .string("Updated source")),
            ("same-count description", "description", .string("Updated description")),
            ("same-count provenance", "vortxProvider", .string("synthetic-provider")),
            ("same-count required headers", "behaviorHints", .object(["proxyHeaders": .object(["request": .object(["Referer": .string("https://referrer.invalid/new")])])])),
            ("same-count binge hints", "behaviorHints", .object(["bingeGroup": .string("updated"), "filename": .string("episode.mkv"), "notWebReady": .bool(true)])),
            ("same-count video identity", "behaviorHints", .object(["videoHash": .string("synthetic-hash"), "videoSize": .integer(42)]))
        ]
        for (label, field, value) in changes {
            var changed = original; changed[field] = value
            replacement(label, old: old, next: try details([changed]))
        }
        replacement("same-count provider", old: old, next: try details([original], base: "https://replacement.invalid/manifest.json"))
        replacement("episode request", old: old, next: try details([original], pathID: "synthetic-title:1:2"))
        replacement("resource type", old: old, next: try details([original], type: "movie"))
        let second: [String: VortxJSON] = ["url": .string("https://media.invalid/second")]
        replacement("same-count stream order", old: try details([original, second]), next: try details([second, original]))
        var changed = original; changed["url"] = .string("https://media.invalid/new-embedded")
        replacement("embedded streams", old: try details([original], embedded: true), next: try details([changed], embedded: true))
        replacement("ready to error", old: old, next: try details([], status: .timeout))
        replacement("error to ready", old: try details([], status: .timeout), next: old)
        replacement("ready to empty", old: old, next: try details([]))
        replacement("loading to error", old: try details([], hostState: "Loading"), next: try details([], status: .timeout))
        replacement("loading to ready", old: try details([], hostState: "Loading"), next: old)
        replacement("missing to loading", old: try details([], hostState: "missing"), next: try details([], hostState: "Loading"))

        let probe = AddonPublicationProbe(); probe.accept(old)
        let identical = try details([original])
        check(!probe.needs(identical) && !probe.streamsChange(identical), "decoded identical native-shaped result stays quiet")
        check(probe.groups(streamID: "synthetic-title:1:2").isEmpty, "exact-episode assembly excludes previous episode")
        let oldEpoch = probe.streamsEpoch; probe.accept(nil)
        check(probe.groups().isEmpty && probe.streamsEpoch == oldEpoch + 1, "unload clears sources")
        probe.accept(old)
        check(probe.groups().count == 1 && probe.streamsEpoch == oldEpoch + 2, "same-value reload after unload republishes")
        let changedMetadata = try details([original], metadataName: "New metadata")
        let sourceEpoch = probe.streamsEpoch
        check(probe.needs(changedMetadata) && !probe.streamsChange(changedMetadata), "metadata-only update does not change source epoch")
        probe.accept(changedMetadata)
        check(probe.streamsEpoch == sourceEpoch && probe.metaDetails?.meta?.name == "New metadata", "metadata-only update still publishes")
        let numeric = try details([["url": .string("https://media.invalid/size"), "behaviorHints": .object(["videoSize": .integer(42)])]])
        let stringNumeric = try details([["url": .string("https://media.invalid/size"), "behaviorHints": .object(["videoSize": .string("42")])]])
        probe.accept(numeric)
        check(!probe.needs(stringNumeric) && !probe.streamsChange(stringNumeric), "decoder-equivalent byte counts stay quiet")
        try metadataValueCases()
        try finiteLargeValueCases()
        finiteResumeTimecodeCases()
        dateParserCases()
        if ProcessInfo.processInfo.environment["VORTX_ADDON_PUBLICATION_BENCHMARK"] == "1" {
            try timing()
        } else {
            print("SKIP timing benchmark (explicit --benchmark opt-in); finite 3000-row correctness covered")
        }
        print("\(checks) checks, \(failures) failures; synthetic values only; provider health unproven")
        if failures != 0 { exit(1) }
    }
}
