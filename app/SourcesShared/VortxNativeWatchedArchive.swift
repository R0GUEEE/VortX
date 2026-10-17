import Foundation
import CryptoKit

enum VortxNativeUnattributedOverlayArchive {
    static let key = "ownAccountOverlayQuarantine"
    private static let pendingOrigin = "first_import_missing_credential"
    /// Schema 2 records retain an already-made isolation decision, never account attribution.
    /// Ordinary schema 1 recovery records cannot authorize this first-import path.
    struct FirstImportRecovery {
        let pending: [String: VortxJSON]
        let quarantinedProfiles: Set<UUID>
    }
    static func firstImportRecovery(_ archive: Data?, scope: VortxAccountScope) throws -> FirstImportRecovery {
        guard let archive else { return .init(pending: [:], quarantinedProfiles: []) }
        try VortxNativeBootstrapArchive.validate(archive)
        let root = try JSONDecoder().decode(VortxJSON.self, from: archive)
        guard let validated = try merge(nil, root["hostDocument"]?[key], scope: scope),
              case .object(let records) = validated else { return .init(pending: [:], quarantinedProfiles: []) }
        var pending: [String: VortxJSON] = [:], profiles = Set<UUID>()
        for identity in records.keys.sorted() {
            let record = records[identity]!
            guard case .string(let id) = record["profileId"], let profile = UUID(uuidString: id) else { throw VortxNativeError.invalidSnapshot }
            profiles.insert(profile)
            if record["schemaVersion"] == .integer(2), pending[id] == nil {
                pending[id] = .object(["profileOverlayBase64": record["profileOverlayBase64"]!, "reason": .string("missing_witness")])
            }
        }
        return .init(pending: pending, quarantinedProfiles: profiles)
    }
    private static func overlay(document: VortxJSON, profileID: UUID) throws -> Data {
        func selected(_ value: VortxJSON?) throws -> VortxJSON? {
            guard let value else { return nil }
            guard case .object(let fields) = value else { throw VortxNativeError.invalidSnapshot }
            let matches = fields.filter { UUID(uuidString: $0.key) == profileID }
            guard matches.count <= 1, matches.isEmpty || matches.first?.key == profileID.uuidString else { throw VortxNativeError.invalidSnapshot }
            return matches.first?.value
        }
        var result: [String: VortxJSON] = [:]
        if let bucket = try selected(document["vortx"]?["byProfile"]) {
            guard case .object = bucket else { throw VortxNativeError.invalidSnapshot }
            result["vortx"] = .object(["byProfile": .object([profileID.uuidString: bucket])])
        }
        if let removed = try selected(document["webProgress"]?["removed"]?["byProfile"]) {
            guard case .array = removed else { throw VortxNativeError.invalidSnapshot }
            result["webProgress"] = .object(["removed": .object(["byProfile": .object([profileID.uuidString: removed])])])
        }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(VortxJSON.object(result))
    }

    /// Append-only recovery evidence. No UID or independent-source digest is allowed here.
    /// This journal is deliberately never an input to pendingOverlay or account authentication.
    static func merge(_ prior: VortxJSON?, _ incoming: VortxJSON?,
                                         scope: VortxAccountScope, state: VortxJSON? = nil) throws -> VortxJSON? {
        var merged: [String: VortxJSON] = [:]
        for candidate in [prior, incoming].compactMap({ $0 }) {
            guard case .object(let records) = candidate, records.count <= 10_000 else { throw VortxNativeError.invalidSnapshot }
            for (key, record) in records {
                let originalMissing = record["schemaVersion"] == .integer(2) && record["origin"] == .string(pendingOrigin)
                let expectedFields: Set<String> = ["schemaVersion", "scope", "ownerProfileId", "profileId", "rootDocumentSha256", "profileOverlaySha256", "profileOverlayBase64"]
                guard case .object(let fields) = record,
                      Set(fields.keys) == (originalMissing ? expectedFields.union(["origin"]) : expectedFields),
                      originalMissing || record["schemaVersion"] == .integer(1), record["scope"] == .string(scope.account),
                      record["ownerProfileId"] == .string(scope.ownerProfileID),
                      case .string(let id) = record["profileId"], let profile = UUID(uuidString: id), profile.uuidString == id,
                      id != scope.ownerProfileID,
                      case .string(let rootHash) = record["rootDocumentSha256"], rootHash.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                      case .string(let overlayHash) = record["profileOverlaySha256"],
                      case .string(let raw) = record["profileOverlayBase64"], let bytes = Data(base64Encoded: raw), bytes.base64EncodedString() == raw,
                      try VortxProfileOverlayWitness.digest(json: bytes) == overlayHash,
                      let value = try? JSONDecoder().decode(VortxJSON.self, from: bytes),
                      try overlay(document: value, profileID: profile) == bytes else { throw VortxNativeError.invalidSnapshot }
                if let state {
                    guard state["roster"]?["profiles"]?[id]?["owner"] == .bool(false) else { throw VortxNativeError.invalidSnapshot }
                }
                let identity = VortxJSON.array([.string(scope.account), .string(scope.ownerProfileID), .string(id), .string(rootHash), .string(overlayHash)]
                    + (originalMissing ? [.string(pendingOrigin)] : []))
                let digest = SHA256.hash(data: try JSONEncoder().encode(identity)).map { String(format: "%02x", $0) }.joined()
                guard key == digest, merged[key] == nil || merged[key] == record else { throw VortxNativeError.invalidSnapshot }
                merged[key] = record
            }
        }
        guard merged.count <= 10_000 else { throw VortxNativeError.invalidSnapshot }
        guard !merged.isEmpty else { return nil }
        let result = VortxJSON.object(merged)
        guard try JSONEncoder().encode(result).count <= 32 * 1_024 * 1_024 else { throw VortxNativeError.invalidSnapshot }
        return result
    }
    static func retaining(_ archive: Data, prior: Data?, document: Data,
                          profileIDs: Set<UUID>, pendingOriginProfileIDs: Set<UUID> = [], scope: VortxAccountScope) throws -> Data {
        guard pendingOriginProfileIDs.isSubset(of: profileIDs) else { throw VortxNativeError.invalidSnapshot }
        try VortxNativeBootstrapArchive.validate(archive)
        if let prior { try VortxNativeBootstrapArchive.validate(prior) }
        var host = try JSONDecoder().decode(VortxJSON.self, from: archive)["hostDocument"]!.decode([String: VortxJSON].self)
        let old = try prior.map { try JSONDecoder().decode(VortxJSON.self, from: $0)["hostDocument"]?[key] } ?? nil
        var added: [String: VortxJSON] = [:]
        let rootHash = SHA256.hash(data: document).map { String(format: "%02x", $0) }.joined()
        let root = try JSONDecoder().decode(VortxJSON.self, from: document)
        for profile in profileIDs {
            let bytes = try overlay(document: root, profileID: profile)
            let overlayHash = try VortxProfileOverlayWitness.digest(json: bytes)
            let originalMissing = pendingOriginProfileIDs.contains(profile)
            let identity = VortxJSON.array([.string(scope.account), .string(scope.ownerProfileID), .string(profile.uuidString), .string(rootHash), .string(overlayHash)]
                + (originalMissing ? [.string(pendingOrigin)] : []))
            let key = SHA256.hash(data: try JSONEncoder().encode(identity)).map { String(format: "%02x", $0) }.joined()
            var record: [String: VortxJSON] = ["schemaVersion": .integer(originalMissing ? 2 : 1), "scope": .string(scope.account),
                "ownerProfileId": .string(scope.ownerProfileID), "profileId": .string(profile.uuidString),
                "rootDocumentSha256": .string(rootHash), "profileOverlaySha256": .string(overlayHash),
                "profileOverlayBase64": .string(bytes.base64EncodedString())]
            if originalMissing { record["origin"] = .string(pendingOrigin) }
            added[key] = .object(record)
        }
        let retained = try merge(old, host[key], scope: scope)
        host[key] = try merge(retained, .object(added), scope: scope)
        return try VortxNativeBootstrapArchive.replacingHostDocument(in: archive,
            with: JSONEncoder().encode(VortxJSON.object(host)))
    }
}

/// Device-local, source-bound migration journal. These bytes never enter nativeSync or cloud
/// preferences. Historical evidence and pending sources are immutable, including after retries.
enum VortxNativeWatchedArchive {
    static let evidenceKey = "nativeWatchedMigrationEvidence"
    static let pendingKey = "nativeWatchedMigrationPending"
    static let sourcesKey = "nativeWatchedMigrationSources"
    private static let maximumJournalBytes = 32 * 1_024 * 1_024
    // A compact file bounds disk bytes, not replay work. Preflight both journals before any
    // receipt expansion/replay. This separate processing budget admits the 24+24 regression
    // while refusing source-reference amplification without discarding recovery evidence.
    private static let maximumExpandedJournalBytes = 96 * 1_024 * 1_024
    private static let maximumEntries = 10_000

    /// Version-1 inline receipts remain readable. Version-2 entries omit only the repeated
    /// source field, whose exact bytes are held once by SHA-256. The migration API still sees
    /// the original version-1 bytes and performs its complete source/UID/metadata validation.
    /// Noncanonical older receipt spellings remain inline rather than being rewritten.
    private struct Journal: Sendable {
        var host: [String: VortxJSON] = [:]
        var sources: [String: String] = [:]
        var expandedByteCount = 0

        init(_ archive: Data?) throws {
            guard let archive else { return }
            try VortxNativeBootstrapArchive.validate(archive)
            host = try JSONDecoder().decode(VortxJSON.self, from: archive)["hostDocument"]!.decode([String: VortxJSON].self)
            if let field = host[sourcesKey] {
                let journal = host.filter { [evidenceKey, pendingKey, sourcesKey].contains($0.key) }
                guard try JSONEncoder().encode(VortxJSON.object(journal)).count <= maximumJournalBytes else { throw Failure.malformed }
                guard case .object(let values) = field, values.count <= 2 * maximumEntries else { throw Failure.malformed }
                for (digest, value) in values {
                    guard digest.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                          case .string(let raw) = value, let bytes = Data(base64Encoded: raw),
                          bytes.count <= maximumJournalBytes, bytes.base64EncodedString() == raw,
                          sha256(bytes) == digest else { throw Failure.malformed }
                    sources[digest] = raw
                }
            }
            var used = Set<String>()
            for key in [evidenceKey, pendingKey] {
                for entry in try records(key) {
                    if case .object(let fields) = entry {
                        guard Set(fields.keys) == ["schemaVersion", "sourceDocumentSha256", "receiptBase64"],
                              fields["schemaVersion"] == .integer(2),
                              case .string(let digest) = fields["sourceDocumentSha256"], sources[digest] != nil,
                              case .string = fields["receiptBase64"] else { throw Failure.malformed }
                        used.insert(digest)
                    } else if case .string = entry {} else { throw Failure.malformed }
                }
            }
            // Unreferenced sources are not silently removed on the next retention pass.
            guard used == Set(sources.keys) else { throw Failure.malformed }
            var remaining = maximumExpandedJournalBytes
            for key in [evidenceKey, pendingKey] {
                for entry in try records(key) {
                    let size = try expandedSize(entry)
                    guard size <= remaining else { throw Failure.processingLimit }
                    remaining -= size
                }
            }
            expandedByteCount = maximumExpandedJournalBytes - remaining
        }

        func records(_ key: String) throws -> [VortxJSON] {
            guard [evidenceKey, pendingKey].contains(key) else { throw Failure.malformed }
            guard let value = host[key] else { return [] }
            guard let records = value.array, records.count <= maximumEntries else { throw Failure.malformed }
            return records
        }

        func fields(_ entry: VortxJSON) throws -> [String: Any] {
            let raw: String
            switch entry {
            case .string(let inline): raw = inline
            case .object(let compact):
                guard case .string(let encoded) = compact["receiptBase64"] else { throw Failure.malformed }
                raw = encoded
            default: throw Failure.malformed
            }
            guard let bytes = Data(base64Encoded: raw), bytes.base64EncodedString() == raw else { throw Failure.malformed }
            let fields = try VortxProfileOverlayWitness.decodeObject(json: bytes)
            if case .object(let compact) = entry {
                guard fields["sourceDocumentBase64"] == nil,
                      let digest = fields["sourceDocumentSha256"] as? String,
                      compact["sourceDocumentSha256"] == .string(digest), sources[digest] != nil else { throw Failure.malformed }
            }
            return fields
        }

        func expandedSize(_ entry: VortxJSON) throws -> Int {
            if case .string(let raw) = entry {
                guard let bytes = Data(base64Encoded: raw), bytes.base64EncodedString() == raw else { throw Failure.malformed }
                return bytes.count
            }
            let object = try fields(entry)
            guard let digest = object["sourceDocumentSha256"] as? String, let source = sources[digest] else { throw Failure.malformed }
            // Base64 needs no JSON escaping with withoutEscapingSlashes. The object is nonempty
            // (it contains the source digest), so adding one field adds exactly one comma.
            let header = try canonical(object).count
            let overhead = Data(",\"sourceDocumentBase64\":\"\"".utf8).count
            let (withSource, overflow) = header.addingReportingOverflow(source.utf8.count)
            let (total, overheadOverflow) = withSource.addingReportingOverflow(overhead)
            guard !overflow, !overheadOverflow else { throw Failure.processingLimit }
            return total
        }

        func expanded(_ entry: VortxJSON) throws -> Data {
            if case .string(let inline) = entry {
                guard let bytes = Data(base64Encoded: inline), bytes.base64EncodedString() == inline else { throw Failure.malformed }
                return bytes
            }
            var object = try fields(entry)
            guard let digest = object["sourceDocumentSha256"] as? String, let source = sources[digest] else { throw Failure.malformed }
            object["sourceDocumentBase64"] = source
            return try canonical(object)
        }
    }

    private static func canonical(_ object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
    }
    private static func sha256(_ bytes: Data) -> String {
        SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }
    private static func compact(_ bytes: Data, sources: inout [String: String]) throws -> VortxJSON {
        var object = try VortxProfileOverlayWitness.decodeObject(json: bytes)
        guard let raw = object["sourceDocumentBase64"] as? String, let source = Data(base64Encoded: raw),
              source.base64EncodedString() == raw, let digest = object["sourceDocumentSha256"] as? String,
              sha256(source) == digest else { throw Failure.malformed }
        guard try canonical(object) == bytes else { return .string(bytes.base64EncodedString()) }
        guard sources[digest] == nil || sources[digest] == raw else { throw Failure.malformed }
        sources[digest] = raw
        object.removeValue(forKey: "sourceDocumentBase64")
        return .object(["schemaVersion": .integer(2), "sourceDocumentSha256": .string(digest),
                        "receiptBase64": .string(try canonical(object).base64EncodedString())])
    }
    /// Resolve historical pending snapshots in their original scope, even if the current cloud
    /// document changed or removed the row. No validated import rows escape this operation.
    /// The caller commits the returned immutable union under its captured source authority.
    static func retryHistorical(_ archive: Data?, scope: VortxAccountScope,
                                isCurrent: @escaping @Sendable () -> Bool,
                                fetch: LegacyWatchedBitfieldMigrationEvidence.MetadataFetcher) async throws -> Data? {
        guard let archive else { return nil }
        try validate(archive, scope: scope)
        let journal = try Journal(archive)
        let pending = try journal.records(pendingKey)
        guard !pending.isEmpty, let owner = UUID(uuidString: scope.ownerProfileID) else { return archive }
        let indexed = try evidenceIndex(journal)
        var sources = journal.sources
        var added: [String: VortxJSON] = [:]
        var remaining = maximumExpandedJournalBytes - journal.expandedByteCount
        var seen = Set<String>()
        for record in pending {
            try Task.checkCancellation()
            guard isCurrent() else { throw LegacyWatchedBitfieldMigrationEvidence.Failure.admissionRevoked }
            let bytes = try journal.expanded(record)
            guard seen.insert(sha256(bytes)).inserted else { continue }
            let matching = indexed[try identity(journal.fields(record))] ?? []
            guard matching.count <= 1 else { throw Failure.malformed }
            // Only one pending tuple and its matching evidence are expanded at a time. Each
            // captured result is compacted immediately; old pending bytes stay in the journal.
            let retried = try await VortxLegacyWatchedMigration.retryArchivedPending([bytes],
                accountID: scope.account, ownerProfileID: owner,
                archivedEvidence: matching.map(journal.expanded), isCurrent: isCurrent, fetch: fetch)
            if matching.isEmpty {
                for receipt in retried.archives {
                    let digest = sha256(receipt)
                    guard added[digest] == nil else { continue }
                    // Newly fetched metadata also consumes the aggregate work budget. Check
                    // before accumulating it; a failed retry leaves the original archive intact.
                    guard receipt.count <= remaining else { throw Failure.processingLimit }
                    remaining -= receipt.count
                    added[digest] = try compact(receipt, sources: &sources)
                }
            }
        }
        try Task.checkCancellation()
        guard isCurrent() else { throw LegacyWatchedBitfieldMigrationEvidence.Failure.admissionRevoked }
        guard !added.isEmpty else { return archive }
        var host = journal.host
        host[evidenceKey] = .array(try journal.records(evidenceKey) + added.keys.sorted().map { added[$0]! })
        host[sourcesKey] = .object(sources.mapValues(VortxJSON.string))
        let candidate = try VortxNativeBootstrapArchive.replacingHostDocument(in: archive,
            with: JSONEncoder().encode(VortxJSON.object(host)))
        return try retaining(candidate, prior: archive, scope: scope)
    }
    enum Failure: LocalizedError, Equatable {
        case pending, malformed, processingLimit
        var errorDescription: String? {
            switch self {
            case .pending: return "Watched history is preserved, but episode metadata from an original addon is unavailable. Retry watched-history migration before completing this account connection."
            case .malformed: return "The saved watched-history migration evidence could not be verified. No history was replaced."
            case .processingLimit: return "Watched-history recovery exceeds this version's processing limit. Your original history is preserved. Keep this account data and contact support or update the app before trying recovery again."
            }
        }
    }
    private static func identity(_ value: [String: Any]) throws -> Data {
        var fields = Dictionary(uniqueKeysWithValues: ["accountId", "ownerProfileId", "profileId",
            "verifiedStreamingUid", "sourceDocumentSha256", "row"].map { ($0, value[$0] ?? NSNull()) })
        for key in ["ownerProfileId", "profileId"] {
            if let raw = fields[key] as? String, let id = UUID(uuidString: raw) { fields[key] = id.uuidString }
        }
        return try canonical(fields)
    }
    private static func evidenceIndex(_ journal: Journal) throws -> [Data: [VortxJSON]] {
        var index: [Data: [VortxJSON]] = [:]
        for entry in try journal.records(evidenceKey) {
            index[try identity(journal.fields(entry)), default: []].append(entry)
        }
        return index
    }
    /// Current preparation only expands the receipt for its exact current tuple. Historical
    /// evidence remains in the sealed journal and cannot inflate an eager input array.
    static func evidenceLookup(_ archive: Data?, scope: VortxAccountScope) throws -> VortxLegacyWatchedMigration.ArchivedEvidenceLookup {
        try validate(archive, scope: scope)
        let journal = try Journal(archive)
        let index = try evidenceIndex(journal)
        return { requested, digest, locator in
            guard Data(requested.accountID.utf8) == Data(scope.account.utf8),
                  requested.ownerProfileID == UUID(uuidString: scope.ownerProfileID) else { throw Failure.malformed }
            let key = try identity(["accountId": requested.accountID, "ownerProfileId": requested.ownerProfileID.uuidString,
                "profileId": requested.profileID.uuidString, "verifiedStreamingUid": requested.verifiedStreamingUID as Any? ?? NSNull(),
                "sourceDocumentSha256": digest, "row": VortxLegacyWatchedMigration.encodeLocator(locator)])
            let matching = index[key] ?? []
            guard matching.count <= 1 else { throw Failure.malformed }
            return try matching.map(journal.expanded)
        }
    }
    static func pendingProfileIDs(_ archive: Data?) throws -> [UUID] {
        let journal = try Journal(archive)
        let completed = try Set(journal.records(evidenceKey).map { try identity(journal.fields($0)) })
        var ids = Set<UUID>()
        for entry in try journal.records(pendingKey) {
            let value = try journal.fields(entry)
            guard !completed.contains(try identity(value)) else { continue }
            guard let id = value["profileId"] as? String, let profile = UUID(uuidString: id) else { throw Failure.malformed }
            ids.insert(profile)
        }
        return ids.sorted { $0.uuidString < $1.uuidString }
    }

    static func entries(_ archive: Data?, key: String) throws -> [Data] {
        let journal = try Journal(archive)
        return try journal.records(key).map(journal.expanded)
    }
    static func validate(_ archive: Data?, scope: VortxAccountScope) throws {
        guard let owner = UUID(uuidString: scope.ownerProfileID) else { throw Failure.malformed }
        let journal = try Journal(archive)
        // Expand one receipt at a time during checkpoint validation. A compact on-disk journal
        // must not require allocating its entire source-per-row representation just to open it.
        for entry in try journal.records(evidenceKey) {
            try VortxLegacyWatchedMigration.validateArchivedEvidence(journal.expanded(entry), accountID: scope.account, ownerProfileID: owner)
        }
        for entry in try journal.records(pendingKey) {
            try VortxLegacyWatchedMigration.validateArchivedPending(journal.expanded(entry), accountID: scope.account, ownerProfileID: owner)
        }
    }
    static func retaining(_ archive: Data, prior: Data? = nil, evidence: [Data] = [], pending: [Data] = [],
                          scope: VortxAccountScope) throws -> Data {
        try validate(prior, scope: scope)
        try validate(archive, scope: scope)
        let journals = try [Journal(prior), Journal(archive)]
        var root = journals[1].host
        var sources: [String: String] = [:]
        for (key, added) in [(evidenceKey, evidence), (pendingKey, pending)] {
            var unique: [String: VortxJSON] = [:]
            func retain(_ bytes: Data) throws {
                let digest = sha256(bytes)
                let record = try compact(bytes, sources: &sources)
                guard unique[digest] == nil || unique[digest] == record else { throw Failure.malformed }
                unique[digest] = record
                guard unique.count <= maximumEntries else { throw Failure.malformed }
            }
            for journal in journals {
                for entry in try journal.records(key) { try retain(journal.expanded(entry)) }
            }
            for bytes in added { try retain(bytes) }
            if !unique.isEmpty { root[key] = .array(unique.keys.sorted().map { unique[$0]! }) }
        }
        if sources.isEmpty { root.removeValue(forKey: sourcesKey) }
        else { root[sourcesKey] = .object(sources.mapValues(VortxJSON.string)) }
        let journal = root.filter { [evidenceKey, pendingKey, sourcesKey].contains($0.key) }
        guard try JSONEncoder().encode(VortxJSON.object(journal)).count <= maximumJournalBytes else { throw Failure.malformed }
        // Retention only changes the journal. Preserve the enclosing material, original-source
        // archive and exclusion receipt instead of rebuilding an empty bootstrap envelope.
        let result = try VortxNativeBootstrapArchive.replacingHostDocument(in: archive,
            with: JSONEncoder().encode(VortxJSON.object(root)))
        try validate(result, scope: scope)
        return result
    }
}
