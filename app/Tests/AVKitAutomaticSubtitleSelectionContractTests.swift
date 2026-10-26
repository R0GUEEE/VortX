import Foundation

/// Source-wiring contract only. This cannot prove AVFoundation settlement, actual file reading,
/// account races, native query support or platform rendering; those are separate runtime gates.
@main
enum AVKitAutomaticSubtitleSelectionContractTests {
    static func main() throws {
        let app = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let source = try String(contentsOf: app.appendingPathComponent("Sources/Player/AVPlayerEngine.swift"),
            encoding: .utf8)
        let automatic = try section(source, from: "    func selectExternalSubtitleAutomatically(",
            to: "    private func awaitAutomaticRawSettlement(")
        let settlement = try section(source, from: "    private func awaitAutomaticRawSettlement(",
            to: "    private func finishAutomaticSubtitle(")
        let current = try section(source, from: "    private func ownsAutomaticSubtitle(",
            to: "    /// Normal AVKit add-on selection")
        let ownerBranch = try section(automatic, from: "            if let owner {",
            to: "            // Owner-bearing selection registers")
        let fallback = try section(automatic, from: "            let mounted = await self.awaitAutomaticRawSettlement(request)",
            to: "            let timings:")
        let mount = try section(automatic, from: "            // Owner-bearing selection registers",
            to: "            let mounted = await self.awaitAutomaticRawSettlement(request)")
        var count = 0
        func check(_ label: String, _ condition: @autoclosure () -> Bool) {
            precondition(condition(), label)
            count += 1
        }
        check("owner remains optional; ordinary guest/legacy selection is not newly denied",
            automatic.contains("owner: PlayerSubtitleAlignmentOwner?")
                && automatic.contains("guard let remote = URL(string: url)")
                && !automatic.contains("guard let owner, let remote"))
        check("reference capture requires an actual original native owner",
            automatic.contains("let reference = owner != nil &&"))
        check("only owner-bearing mount acquires native effect and registration",
            ownerBranch.contains("tryBeginSubtitleEffect(owner.mount)")
                && ownerBranch.contains("registerSubtitleRenderer(admitted,")
                && ownerBranch.contains("request.publication = publication")
                && !mount.contains("tryBeginSubtitleEffect(")
                && !mount.contains("registerSubtitleRenderer("))
        check("ordinary mount still uses real cue loading and central native settlement",
            mount.contains("subtitleRenderer.load(cues: cues)")
                && mount.contains("selectNativeSubtitle(nil, activatingExternalAfterSettlement: true,")
                && mount.contains("preservingAutomaticRequest: request.generation)"))
        check("nil-owner fallback returns only after the actual raw settlement succeeds",
            fallback.contains("guard mounted.succeeded else")
                && fallback.contains("guard let reference, let owner,")
                && fallback.contains("completion(.selectedWithoutAlignment)"))
        let acquisition = try range(automatic, "timings = try await reference.read")
        let nativeOwner = try range(automatic, "guard let reference, let owner,")
        let confirmedMount = try range(automatic, "guard mounted.succeeded else")
        check("actual reference reading and query follow both settlement and native-owner admission",
            confirmedMount.lowerBound < nativeOwner.lowerBound
                && nativeOwner.lowerBound < acquisition.lowerBound)
        check("one original request retains load, manual, item, mount and publication identity",
            current.contains("automaticSubtitleRequest === original")
                && current.contains("subtitleAlignmentGeneration == original.generation")
                && current.contains("externalSubtitleRequestRevision == original.requestRevision")
                && current.contains("itemGeneration == original.itemGeneration")
                && current.contains("playbackMountIdentity == original.mountIdentity")
                && current.contains("owns(original.item, loadToken: original.load)")
                && current.contains("original.publication === alignedSubtitlePublication")
                && current.contains("alignedSubtitlePublication?.isRevoked != true"))
        check("raw success requires exact active overlay, revision and native deselection",
            settlement.contains("request.rawSettled, externalSubActive,")
                && settlement.contains("request.selectionRevision == subtitleSelectionRevision")
                && settlement.contains("selectedMediaOption(in: $0) == nil"))
        check("settlement timeout cannot activate a failed or successor request",
            settlement.contains("guard ownsAutomaticSubtitle(request), !Task.isCancelled else")
                && settlement.contains("retireSubtitleAlignment(revealRequestedFile: false)"))
        check("final correction uses the original private publication rather than a new receipt",
            automatic.contains("guard let publication = request.publication,")
                && automatic.contains("self.alignedSubtitlePublication === publication,"))
        let retirement = try section(source, from: "    private func retireSubtitleAlignment(",
            to: "    func cancelAutomaticSubtitleAlignment(")
        let clearing = try section(retirement, from: "        if !reveal {",
            to: "        // A manual timing/visibility decision reveals")
        let clear = try range(clearing, "subtitleOverlay?.setText(nil)")
        let receipt = try range(clearing, "originalPublication?.close()")
        check("revocation/deinit cleanup is acknowledged only after the actual actor-owned clear",
            clear.lowerBound < receipt.lowerBound)
        let teardown = try section(source, from: "    isolated deinit {", to: "\nextension AVPlayerEngineController")
        check("engine release without stop still retires its task and exact registration",
            teardown.contains("retireSubtitleAlignment(revealRequestedFile: false)"))
        print("AVKit automatic-selection source contract: \(count) checks passed")
    }

    private enum Failure: Error { case missing(String) }
    private static func range(_ source: String, _ fragment: String) throws -> Range<String.Index> {
        guard let found = source.range(of: fragment) else { throw Failure.missing(fragment) }
        return found
    }
    private static func section(_ source: String, from: String, to: String) throws -> String {
        let start = try range(source, from)
        guard let end = source.range(of: to, range: start.upperBound..<source.endIndex) else {
            throw Failure.missing(to)
        }
        return String(source[start.lowerBound..<end.lowerBound])
    }
}
