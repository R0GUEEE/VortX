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
        return try VortxNativeBootstrapArchive.encode(document: JSONEncoder().encode(VortxJSON.object(host)))
    }
}

/// Device-local, source-bound migration journal. These bytes never enter nativeSync or cloud
/// preferences. Historical evidence and pending sources are immutable, including after retries.
enum VortxNativeWatchedArchive {
    static let evidenceKey = "nativeWatchedMigrationEvidence"
    static let pendingKey = "nativeWatchedMigrationPending"
    /// Resolve historical pending snapshots in their original scope, even if the current cloud
    /// document changed or removed the row. No validated import rows escape this operation.
    /// The caller commits the returned immutable union under its captured source authority.
    static func retryHistorical(_ archive: Data?, scope: VortxAccountScope,
                                isCurrent: @escaping @Sendable () -> Bool,
                                fetch: LegacyWatchedBitfieldMigrationEvidence.MetadataFetcher) async throws -> Data? {
        guard let archive else { return nil }
        try validate(archive, scope: scope)
        let pending = try entries(archive, key: pendingKey)
        guard !pending.isEmpty, let owner = UUID(uuidString: scope.ownerProfileID) else { return archive }
        let retried = try await VortxLegacyWatchedMigration.retryArchivedPending(pending,
            accountID: scope.account, ownerProfileID: owner,
            archivedEvidence: entries(archive, key: evidenceKey), isCurrent: isCurrent, fetch: fetch)
        // Never replace pending with unresolved: every original raw snapshot remains recoverable.
        return try retaining(archive, evidence: retried.archives, pending: retried.unresolved, scope: scope)
    }
    enum Failure: LocalizedError, Equatable {
        case pending, malformed
        var errorDescription: String? {
            switch self {
            case .pending: return "Watched history is preserved, but episode metadata from an original addon is unavailable. Retry watched-history migration before completing this account connection."
            case .malformed: return "The saved watched-history migration evidence could not be verified. No history was replaced."
            }
        }
    }
    static func pendingProfileIDs(_ archive: Data?) throws -> [UUID] {
        func identity(_ bytes: Data) throws -> VortxJSON {
            let value = try JSONDecoder().decode(VortxJSON.self, from: bytes)
            return .array([value["accountId"] ?? .null, value["ownerProfileId"] ?? .null,
                value["profileId"] ?? .null, value["verifiedStreamingUid"] ?? .null,
                value["sourceDocumentSha256"] ?? .null, value["row"] ?? .null])
        }
        let completed = try entries(archive, key: evidenceKey).map(identity)
        var ids = Set<UUID>()
        for bytes in try entries(archive, key: pendingKey) where !completed.contains(try identity(bytes)) {
            let value = try JSONDecoder().decode(VortxJSON.self, from: bytes)
            guard case .string(let id) = value["profileId"], let profile = UUID(uuidString: id) else { throw Failure.malformed }
            ids.insert(profile)
        }
        return ids.sorted { $0.uuidString < $1.uuidString }
    }

    static func entries(_ archive: Data?, key: String) throws -> [Data] {
        guard let archive else { return [] }
        try VortxNativeBootstrapArchive.validate(archive)
        let value = try JSONDecoder().decode(VortxJSON.self, from: archive)
        guard let field = value["hostDocument"]?[key] else { return [] }
        guard let entries = field.array, entries.count <= 10_000 else { throw Failure.malformed }
        return try entries.map {
            guard case .string(let raw) = $0, let bytes = Data(base64Encoded: raw),
                  bytes.base64EncodedString() == raw else { throw Failure.malformed }
            return bytes
        }
    }
    static func validate(_ archive: Data?, scope: VortxAccountScope) throws {
        guard let owner = UUID(uuidString: scope.ownerProfileID) else { throw Failure.malformed }
        for bytes in try entries(archive, key: evidenceKey) {
            try VortxLegacyWatchedMigration.validateArchivedEvidence(bytes, accountID: scope.account, ownerProfileID: owner)
        }
        for bytes in try entries(archive, key: pendingKey) {
            try VortxLegacyWatchedMigration.validateArchivedPending(bytes, accountID: scope.account, ownerProfileID: owner)
        }
    }
    static func retaining(_ archive: Data, prior: Data? = nil, evidence: [Data] = [], pending: [Data] = [],
                          scope: VortxAccountScope) throws -> Data {
        try validate(prior, scope: scope)
        try validate(archive, scope: scope)
        var root = try JSONDecoder().decode(VortxJSON.self, from: archive)["hostDocument"]?.decode([String: VortxJSON].self) ?? [:]
        for (key, added) in [(evidenceKey, evidence), (pendingKey, pending)] {
            let combined = try entries(prior, key: key) + entries(archive, key: key) + added
            let unique = Set(combined).sorted { $0.lexicographicallyPrecedes($1) }
            guard unique.count <= 10_000, unique.reduce(0, { $0 + $1.count }) <= 32 * 1_024 * 1_024 else { throw Failure.malformed }
            if !unique.isEmpty { root[key] = .array(unique.map { .string($0.base64EncodedString()) }) }
        }
        let result = try VortxNativeBootstrapArchive.encode(document: JSONEncoder().encode(VortxJSON.object(root)))
        try validate(result, scope: scope)
        return result
    }
}
