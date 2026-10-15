import Foundation
import CryptoKit
import Darwin
import Security

/// File errors carry private URLs and account paths in userInfo/localizedDescription. Keep only
/// allowlisted domains, numeric codes, and a static stage while rethrowing the original error.
enum VortxCheckpointDiagnostics {
    enum Stage: String {
        case validate, retainExisting = "retain_existing", seal, createDirectory = "create_directory"
        case stageWrite = "stage_write", stageSync = "stage_sync", stageReadback = "stage_readback"
        case install, directorySync = "directory_sync", installedReadback = "installed_readback"
        case transactionCommit = "transaction_commit"
    }
    static func summary(stage: Stage, error: Error) -> String {
        func code(_ error: Error) -> String {
            if error is VortxNativeError { return "domain=native code=" + VortxNativeError.diagnosticCode(error) }
            let value = error as NSError
            let domain: String
            switch value.domain {
            case NSCocoaErrorDomain: domain = "cocoa"
            case NSPOSIXErrorDomain: domain = "posix"
            case NSOSStatusErrorDomain: domain = "osstatus"
            default: domain = error is CryptoKitError ? "cryptokit" : "other"
            }
            return "domain=\(domain) code=\(value.code)"
        }
        let underlying = (error as NSError).userInfo[NSUnderlyingErrorKey] as? Error
        return "stage=\(stage.rawValue) \(code(error))" + (underlying.map { " underlying_" + code($0) } ?? "")
    }
    static func report(stage: Stage, error: Error) {
        VXProbe.log("native-checkpoint", summary(stage: stage, error: error))
    }
}

/// Immutable, owner-only installation identity. Publication never replaces an existing key,
/// including when two independent app processes race. Legacy access is migration-only.
final class VortxNativeInstallationKeyFileStore: @unchecked Sendable {
    enum Boundary { case beforePublish, afterPublish }
    private let directory: URL
    private let legacyRead: () throws -> Data?
    private let randomBytes: () throws -> Data
    private let boundary: (Boundary) throws -> Void
    private let lock = NSLock()
    private var cached: Data?
    private let filename = "native-installation-v2.key"
    init(directory: URL, legacyRead: @escaping () throws -> Data?, randomBytes: @escaping () throws -> Data,
         boundary: @escaping (Boundary) throws -> Void = { _ in }) {
        self.directory = directory; self.legacyRead = legacyRead; self.randomBytes = randomBytes; self.boundary = boundary
    }
    private func openDirectory() throws -> Int32 {
        let parent = Darwin.open(directory.deletingLastPathComponent().path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW)
        guard parent >= 0 else { throw VortxNativeError.unavailable }
        defer { Darwin.close(parent) }
        let name = directory.lastPathComponent
        guard !name.isEmpty, name != ".", name != "..", !name.contains("/") else { throw VortxNativeError.unavailable }
        guard Darwin.mkdirat(parent, name, mode_t(0o700)) == 0 || errno == EEXIST else { throw VortxNativeError.unavailable }
        let fd = Darwin.openat(parent, name, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW)
        guard fd >= 0 else { throw VortxNativeError.unavailable }
        var info = stat()
        guard Darwin.fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFDIR, info.st_uid == geteuid(),
              Darwin.fchmod(fd, mode_t(0o700)) == 0, Darwin.fstat(fd, &info) == 0,
              info.st_mode & mode_t(0o7777) == mode_t(0o700), Darwin.fsync(parent) == 0 else {
            Darwin.close(fd); throw VortxNativeError.unavailable
        }
        return fd
    }
    private func readFinal(_ directoryFD: Int32) throws -> Data? {
        let fd = Darwin.openat(directoryFD, filename, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)
        if fd < 0 {
            if errno == ENOENT { return nil }
            throw VortxNativeError.unavailable
        }
        var closed = false
        defer { if !closed { Darwin.close(fd) } }
        var info = stat()
        guard Darwin.fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == geteuid(),
              info.st_mode & mode_t(0o7777) == mode_t(0o600), info.st_size == 32 else { throw VortxNativeError.unavailable }
        var bytes = Data(count: 33), count = 0
        try bytes.withUnsafeMutableBytes { buffer in
            while count < 33 {
                let read = Darwin.read(fd, buffer.baseAddress!.advanced(by: count), 33 - count)
                if read < 0 && errno == EINTR { continue }
                guard read >= 0 else { throw VortxNativeError.unavailable }
                if read == 0 { break }
                count += read
            }
        }
        guard count == 32 else { throw VortxNativeError.unavailable }
        let result = Darwin.close(fd); closed = true
        guard result == 0 else { throw VortxNativeError.unavailable }
        return Data(bytes.prefix(count))
    }
    func loadOrCreateBytes() throws -> Data {
        lock.lock(); defer { lock.unlock() }
        if let cached { return cached }
        let directoryFD = try openDirectory()
        defer { Darwin.close(directoryFD) }
        if let existing = try readFinal(directoryFD) {
            // Also heals a prior publication whose directory sync was interrupted.
            guard Darwin.fsync(directoryFD) == 0 else { throw VortxNativeError.unavailable }
            cached = existing; return existing
        }
        let legacy = try legacyRead(), candidate = try legacy ?? randomBytes()
        guard candidate.count == 32 else { throw VortxNativeError.unavailable }
        let stage = ".native-installation-\(UUID().uuidString).stage"
        let fd = Darwin.openat(directoryFD, stage, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, mode_t(0o600))
        guard fd >= 0 else { throw VortxNativeError.unavailable }
        var closed = false
        defer { if !closed { Darwin.close(fd) }; Darwin.unlinkat(directoryFD, stage, 0) }
        guard Darwin.fchmod(fd, mode_t(0o600)) == 0 else { throw VortxNativeError.unavailable }
        try candidate.withUnsafeBytes { buffer in
            var count = 0
            while count < candidate.count {
                let written = Darwin.write(fd, buffer.baseAddress!.advanced(by: count), candidate.count - count)
                if written < 0 && errno == EINTR { continue }
                guard written > 0 else { throw VortxNativeError.unavailable }
                count += written
            }
        }
        guard Darwin.fsync(fd) == 0 else { throw VortxNativeError.unavailable }
        let closeResult = Darwin.close(fd); closed = true
        guard closeResult == 0 else { throw VortxNativeError.unavailable }
        try boundary(.beforePublish)
        let linked = Darwin.linkat(directoryFD, stage, directoryFD, filename, 0)
        let collision = linked != 0 && errno == EEXIST
        guard linked == 0 || collision else { throw VortxNativeError.unavailable }
        try boundary(.afterPublish)
        guard Darwin.unlinkat(directoryFD, stage, 0) == 0, Darwin.fsync(directoryFD) == 0,
              let winner = try readFinal(directoryFD),
              (collision && legacy == nil) || winner == candidate else { throw VortxNativeError.unavailable }
        cached = winner; return winner
    }
}
// End immutable installation-key file store (mechanically extracted by its regression gate).

/// Root for the native account stores (checkpoints, preference intents). A physical Apple TV cannot
/// create Library/Application Support (the simulator can), so tvOS keeps them in Caches. Caches may be
/// purged under storage pressure; the encrypted cloud account stays authoritative, so a purge only means
/// the next launch re-adopts the account. The installation key stays in the tvOS Keychain.
enum VortxNativeStorage {
    static func root() throws -> URL {
        #if os(tvOS)
        try FileManager.default.url(for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        #else
        try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        #endif
    }
}

/// This key authenticates only the local checkpoint inventory. Account ciphertext remains readable
/// with its independent account key after device-key loss; no installation identity is exported.
enum VortxNativeInstallationKey {
    private static let lock = NSLock()
    private static func readLegacy() throws -> Data? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "tv.vortx.native-installation", kSecAttrAccount as String: "checkpoint-inventory-v2",
            kSecAttrSynchronizable as String: false]
        var request = query; request[kSecReturnData as String] = true; request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let bytes = result as? Data, bytes.count == 32 else { throw VortxNativeError.unavailable }
        return bytes
    }
    private static func randomBytes() throws -> Data {
        var bytes = Data(count: 32)
        let randomStatus = bytes.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 32, $0.baseAddress!) }
        guard randomStatus == errSecSuccess else { throw VortxNativeError.unavailable }
        return bytes
    }
    #if os(macOS)
    private static let fileStore = VortxNativeInstallationKeyFileStore(
        directory: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("VortX", isDirectory: true),
        legacyRead: { try readLegacy() }, randomBytes: { try randomBytes() })
    #endif
    static func loadOrCreate() throws -> SymmetricKey {
        #if os(macOS)
        return SymmetricKey(data: try fileStore.loadOrCreateBytes())
        #else
        lock.lock(); defer { lock.unlock() }
        if let existing = try readLegacy() { return SymmetricKey(data: existing) }
        let bytes = try randomBytes()
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "tv.vortx.native-installation", kSecAttrAccount as String: "checkpoint-inventory-v2",
            kSecAttrSynchronizable as String: false]
        var item = query; item[kSecValueData as String] = bytes
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess || status == errSecDuplicateItem, let confirmed = try readLegacy(),
              status == errSecDuplicateItem || confirmed == bytes else { throw VortxNativeError.unavailable }
        return SymmetricKey(data: confirmed)
        #endif
    }
}

/// Native-only exports cannot acknowledge legacy host edits they deliberately do not serialize.
enum VortxNativeSyncExportPolicy {
    static func acknowledgesSetting(_ key: String, syncable: Bool, profileProjection: Bool, projectionAcknowledged: Bool) -> Bool {
        if !syncable { return true } // Retire obsolete device-local stamps, never export their values.
        if VortxNativeHostPreferences.knownGlobals.contains(key) { return true }
        if profileProjection { return projectionAcknowledged }
        return ["stremiox.profiles.active", "stremiox.activeProfileId"].contains(key)
    }
    static func permitsStateOnlyExport(hasDirtySettings: Bool, hasLegacyAddonOrderIntent: Bool,
                                       overridingLegacySource: Bool = false) -> Bool {
        !hasDirtySettings && !hasLegacyAddonOrderIntent && !overridingLegacySource
    }
}

struct VortxAccountScope: Codable, Hashable, Sendable {
    let account: String
    let ownerProfileID: String
    var authenticatedData: Data { Data((account + "\u{0}" + ownerProfileID).utf8) }
    func validate() throws {
        guard !account.isEmpty, !ownerProfileID.isEmpty,
              !account.contains("\u{0}"), !ownerProfileID.contains("\u{0}") else { throw VortxNativeError.invalidSnapshot }
    }
    func validateSnapshot(_ snapshot: String) throws -> VortxJSON {
        let value = try JSONDecoder().decode(VortxJSON.self, from: Data(snapshot.utf8))
        guard case .object(let profiles) = value["roster"]?["profiles"],
              profiles[ownerProfileID]?["owner"] == .bool(true),
              profiles[ownerProfileID]?["deleted"] != .bool(true),
              profiles.values.filter({ $0["owner"] == .bool(true) }).count == 1,
              profiles.allSatisfy({ $0.value["id"] == .string($0.key) }),
              case .string(let active) = value["activeProfileId"],
              profiles[active] != nil, profiles[active]?["deleted"] != .bool(true),
              case .object(let libraries) = value["libraries"], case .object = libraries[active],
              libraries.keys.allSatisfy({ profiles[$0] != nil }) else { throw VortxNativeError.invalidSnapshot }
        if let sync = value["nativeSync"], sync != .null {
            guard sync["scope"] == .string(account), sync["ownerProfileId"] == .string(ownerProfileID),
                  Self.supportedNativeSyncSchema(sync["schemaVersion"]) else { throw VortxNativeError.invalidSnapshot }
        }
        return value
    }
    private static func supportedNativeSyncSchema(_ value: VortxJSON?) -> Bool {
        switch value {
        case .integer(let schema): return (1...5).contains(schema)
        case .unsigned(let schema): return (1...5).contains(schema)
        default: return false
        }
    }
    func validateHydration(from original: String, to hydrated: String) throws {
        let before = try validateSnapshot(original), after = try validateSnapshot(hydrated)
        guard before["nativeSync"] == after["nativeSync"] else { throw VortxNativeError.invalidSnapshot }
        guard case .object(let libraries) = before["libraries"] else { throw VortxNativeError.invalidSnapshot }
        for (profile, library) in libraries {
            if let context = library["watchContexts"] {
                let hydrated = after["libraries"]?[profile]?["watchContexts"]
                if case .object(let retained) = context, case .object(let projected)? = hydrated {
                    // The unchanged nativeSync authority can synthesize a missing context during
                    // hydration. Additions are lossless; every previously sealed entry must still
                    // survive exactly, including metadata and duration, before mounting/committing.
                    guard retained.allSatisfy({ projected[$0.key] == $0.value }) else { throw VortxNativeError.invalidSnapshot }
                } else {
                    guard context == hydrated else { throw VortxNativeError.invalidSnapshot }
                }
            }
        }
    }
}

protocol VortxCheckpointStore: Sendable {
    /// nil means absent only. Corruption, inaccessible keys, wrong scope and I/O failures must throw.
    func read(scope: VortxAccountScope) throws -> String?
    /// Must atomically replace and read back before returning. Never log or write plaintext state.
    func commit(_ snapshot: String, scope: VortxAccountScope) throws
    func readHostPreferences(scope: VortxAccountScope) throws -> Data?
    func readLegacyMaterial(scope: VortxAccountScope) throws -> Data?
    func readLegacyProfileEdits(scope: VortxAccountScope) throws -> VortxJSON?
    func commit(_ snapshot: String, scope: VortxAccountScope, hostPreferences: Data) throws
}
/// Optional external-source authentication lease. Its implementation linearizes credential
/// invalidation with the final checkpoint, without exposing credentials to the native runtime.
protocol VortxMutationAuthority: Sendable {
    func withActive(_ operation: () throws -> Void) throws
}
extension VortxCheckpointStore {
    func readHostPreferences(scope: VortxAccountScope) throws -> Data? { nil }
    func readLegacyMaterial(scope: VortxAccountScope) throws -> Data? { nil }
    func readLegacyProfileEdits(scope: VortxAccountScope) throws -> VortxJSON? { nil }
    func commit(_ snapshot: String, scope: VortxAccountScope, hostPreferences: Data) throws { throw VortxNativeError.unavailable }
}

/// The host supplies a key from its existing account secure store. This class never persists that
/// key or plaintext snapshots; the bootstrap archive excludes known credential carriers. The sealed
/// file is separate from the original legacy/account documents and never replaces them.
final class VortxEncryptedCheckpointStore: VortxCheckpointStore, @unchecked Sendable {
    private struct Envelope: Codable {
        let format: String
        let state: String
        let bootstrap: Data?
        var hostPreferences: Data? = nil
    }
    private let directory: URL
    private let key: SymmetricKey
    private let installationKey: SymmetricKey?
    private let lock = NSLock()
    private static let migrationDraftLock = NSLock()
    private var bootstraps: [VortxAccountScope: Data] = [:]
    /// The checkpoint this store last wrote or fully opened, per scope: the digest of the exact file bytes
    /// and the parts a commit carries forward. While the file still hashes the same, a commit skips the full
    /// decrypt and validation of it; a fresh store instance, or a file replaced underneath, opens it in full.
    private struct Installed {
        let digest: SHA256.Digest
        let state: String
        let bootstrap: Data?
        let hostPreferences: Data?
    }
    private var installed: [VortxAccountScope: Installed] = [:]
    private struct AccountLocator: Codable {
        let format: String
        let scope: VortxAccountScope
        var accountLocatorDigest: Data? = nil
    }
    private struct InventoryProof: Codable { let scope: VortxAccountScope; let ciphertextDigest: Data }
    private struct IndexedCiphertext: Codable { let format: String; let ciphertext: Data; let proof: Data }
    private static let indexedPrefix = Data("vortx-native-indexed-ciphertext-v2\n".utf8)
    struct Recovery {
        let scope: VortxAccountScope
        let state: String
        let bootstrap: Data
    }
    init(directory: URL, key: SymmetricKey, installationKey: SymmetricKey? = nil, bootstrap: Data? = nil, bootstrapScope: VortxAccountScope? = nil) throws {
        guard key.bitCount == 256, installationKey == nil || installationKey?.bitCount == 256 else { throw VortxNativeError.invalidSnapshot }
        if let bootstrap {
            guard let bootstrapScope else { throw VortxNativeError.invalidSnapshot }
            try bootstrapScope.validate(); try VortxNativeBootstrapArchive.validate(bootstrap)
            bootstraps[bootstrapScope] = bootstrap
        }
        self.directory = directory; self.key = key; self.installationKey = installationKey
    }
    private func url(_ scope: VortxAccountScope) -> URL {
        let digest = SHA256.hash(data: scope.authenticatedData).map { String(format: "%02x", $0) }.joined()
        return directory.appendingPathComponent("native-state-v1-\(digest).sealed")
    }
    private func migrationDraftURL(_ scope: VortxAccountScope) -> URL {
        let digest = SHA256.hash(data: scope.authenticatedData).map { String(format: "%02x", $0) }.joined()
        return directory.appendingPathComponent("native-migration-draft-v1-\(digest).sealed")
    }
    private func migrationDraftAAD(_ scope: VortxAccountScope) -> Data {
        Data("vortx-native-migration-draft-v1\u{0}".utf8) + scope.authenticatedData
    }
    /// A draft cannot mount an account, pin an owner, or certify an import. It only retains exact
    /// authenticated recovery evidence before the first complete kernel checkpoint exists.
    func readMigrationDraft(scope: VortxAccountScope) throws -> Data? {
        Self.migrationDraftLock.lock(); defer { Self.migrationDraftLock.unlock() }
        try scope.validate()
        let bytes: Data
        do { bytes = try Data(contentsOf: migrationDraftURL(scope)) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return nil }
        let archive = try AES.GCM.open(AES.GCM.SealedBox(combined: bytes), using: key, authenticating: migrationDraftAAD(scope))
        try VortxNativeWatchedArchive.validate(archive, scope: scope)
        return archive
    }
    func retainMigrationDraft(_ archive: Data, scope: VortxAccountScope, expected: Data? = nil, authority: any VortxMutationAuthority) throws {
        Self.migrationDraftLock.lock(); defer { Self.migrationDraftLock.unlock() }
        try scope.validate(); try VortxNativeWatchedArchive.validate(archive, scope: scope)
        guard archive.count <= 48 * 1_024 * 1_024 else { throw VortxNativeError.invalidSnapshot }
        try authority.withActive {
            try Task.checkCancellation()
            var current: Data?
            do {
                current = try AES.GCM.open(AES.GCM.SealedBox(combined: Data(contentsOf: migrationDraftURL(scope))),
                    using: key, authenticating: migrationDraftAAD(scope))
            } catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError {}
            guard current == expected else { throw VortxNativeError.superseded }
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let sealed = try AES.GCM.seal(archive, using: key, authenticating: migrationDraftAAD(scope))
            guard let bytes = sealed.combined else { throw VortxNativeError.invalidSnapshot }
            try durableInstall(bytes, at: migrationDraftURL(scope))
        }
    }
    private func locatorAAD(_ account: String) -> Data { Data(("vortx-native-account-locator-v1\u{0}" + account).utf8) }
    private func locatorURL(_ account: String) -> URL {
        let digest = SHA256.hash(data: locatorAAD(account)).map { String(format: "%02x", $0) }.joined()
        return directory.appendingPathComponent("native-account-v1-\(digest).sealed")
    }
    private func indexURL(_ account: String) -> URL {
        directory.appendingPathComponent(locatorURL(account).lastPathComponent.replacingOccurrences(of: "native-account-v1-", with: "native-account-v2-"))
    }
    private func indexAAD(_ filename: String) -> Data { Data(("vortx-native-inventory-v2\u{0}" + filename).utf8) }
    private func indexedCiphertext(_ ciphertext: Data, scope: VortxAccountScope) throws -> Data {
        guard let installationKey else { return ciphertext }
        let proof = InventoryProof(scope: scope, ciphertextDigest: Data(SHA256.hash(data: ciphertext)))
        guard let sealed = try AES.GCM.seal(JSONEncoder().encode(proof), using: installationKey,
            authenticating: indexAAD(url(scope).lastPathComponent)).combined else { throw VortxNativeError.invalidSnapshot }
        return Self.indexedPrefix + (try JSONEncoder().encode(IndexedCiphertext(format: "vortx-native-indexed-ciphertext-v2", ciphertext: ciphertext, proof: sealed)))
    }
    private func accountCiphertext(_ data: Data) throws -> Data {
        // The original account-key ciphertext is never discarded or made dependent on the install key.
        guard data.starts(with: Self.indexedPrefix) else { return data }
        let indexed = try JSONDecoder().decode(IndexedCiphertext.self, from: data.dropFirst(Self.indexedPrefix.count))
        guard indexed.format == "vortx-native-indexed-ciphertext-v2" else { throw VortxNativeError.invalidSnapshot }
        return indexed.ciphertext
    }
    private func authenticatedInventory(for requestedScope: VortxAccountScope) throws -> Set<String> {
        let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
        func isStaging(_ name: String) -> Bool {
            name.hasSuffix(".sealed") && [".native-checkpoint-", ".native-locator-", ".native-index-"].contains(where: name.hasPrefix)
        }
        // A durable stage may belong to an account whose final file/index was never published.
        // Ignore unrelated files, but never turn recognized interrupted transaction state into absence.
        guard !files.contains(where: { isStaging($0.lastPathComponent) }) else { throw VortxNativeError.invalidSnapshot }
        guard let installationKey else {
            guard !files.contains(where: { file in
                ["native-state-v1-", "native-account-v1-", "native-account-v2-"].contains(where: file.lastPathComponent.hasPrefix)
            }) else { throw VortxNativeError.invalidSnapshot }
            return []
        }
        var covered: Set<String> = []
        var authenticatedIndexes: Set<String> = []
        for file in files where file.lastPathComponent.hasPrefix("native-account-v2-") {
            let plain = try AES.GCM.open(AES.GCM.SealedBox(combined: Data(contentsOf: file)), using: installationKey,
                                         authenticating: indexAAD(file.lastPathComponent))
            let locator = try JSONDecoder().decode(AccountLocator.self, from: plain)
            try locator.scope.validate()
            guard locator.format == "vortx-native-account-locator-v2", indexURL(locator.scope.account).path == file.path else { throw VortxNativeError.invalidSnapshot }
            guard locator.scope.account != requestedScope.account || locator.scope == requestedScope else { throw VortxNativeError.invalidSnapshot }
            let accountLocator = try Data(contentsOf: locatorURL(locator.scope.account))
            guard locator.accountLocatorDigest == Data(SHA256.hash(data: accountLocator)) else { throw VortxNativeError.invalidSnapshot }
            let stateURL = url(locator.scope)
            let bytes = try Data(contentsOf: stateURL)
            guard bytes.starts(with: Self.indexedPrefix) else { throw VortxNativeError.invalidSnapshot }
            let indexed = try JSONDecoder().decode(IndexedCiphertext.self, from: bytes.dropFirst(Self.indexedPrefix.count))
            guard indexed.format == "vortx-native-indexed-ciphertext-v2" else { throw VortxNativeError.invalidSnapshot }
            let proofBytes = try AES.GCM.open(AES.GCM.SealedBox(combined: indexed.proof), using: installationKey,
                                             authenticating: indexAAD(stateURL.lastPathComponent))
            let proof = try JSONDecoder().decode(InventoryProof.self, from: proofBytes)
            guard proof.scope == locator.scope, proof.ciphertextDigest == Data(SHA256.hash(data: indexed.ciphertext)),
                  covered.insert(stateURL.path).inserted else { throw VortxNativeError.invalidSnapshot }
            authenticatedIndexes.insert(file.lastPathComponent)
        }
        guard files.filter({ $0.lastPathComponent.hasPrefix("native-account-v1-") }).allSatisfy({
            authenticatedIndexes.contains($0.lastPathComponent.replacingOccurrences(of: "native-account-v1-", with: "native-account-v2-"))
        }) else { throw VortxNativeError.invalidSnapshot }
        return covered
    }
    private func durableInstall(_ bytes: Data, at destination: URL) throws {
        let staged = directory.appendingPathComponent(".native-index-\(UUID().uuidString).sealed")
        defer { try? FileManager.default.removeItem(at: staged) }
        try bytes.write(to: staged, options: [.withoutOverwriting, .completeFileProtection])
        let file = try FileHandle(forWritingTo: staged); defer { try? file.close() }; try file.synchronize()
        guard try Data(contentsOf: staged) == bytes else { throw VortxNativeError.invalidSnapshot }
        guard Darwin.rename(staged.path, destination.path) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        let fd = Darwin.open(directory.path, O_RDONLY); guard fd >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        defer { Darwin.close(fd) }; guard Darwin.fsync(fd) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        guard try Data(contentsOf: destination) == bytes else { throw VortxNativeError.invalidSnapshot }
    }
    /// Publish only after a full authenticated account open. The account key and namespace
    /// authenticate this owner lookup; global profile preferences never establish ownership.
    func rememberAuthenticatedScope(_ scope: VortxAccountScope) throws {
        lock.lock(); defer { lock.unlock() }
        try scope.validate()
        do {
            let prior = try AES.GCM.open(AES.GCM.SealedBox(combined: Data(contentsOf: locatorURL(scope.account))),
                                         using: key, authenticating: locatorAAD(scope.account))
            let pinned = try JSONDecoder().decode(AccountLocator.self, from: prior)
            guard pinned.format == "vortx-native-account-locator-v1", pinned.scope == scope else { throw VortxNativeError.invalidSnapshot }
        } catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError {}
        let existing = try open(Data(contentsOf: url(scope)), scope: scope)
        guard existing.bootstrap != nil else { throw VortxNativeError.invalidSnapshot }
        let payload = try JSONEncoder().encode(AccountLocator(format: "vortx-native-account-locator-v1", scope: scope))
        let sealed = try AES.GCM.seal(payload, using: key, authenticating: locatorAAD(scope.account))
        guard let bytes = sealed.combined else { throw VortxNativeError.invalidSnapshot }
        let staged = directory.appendingPathComponent(".native-locator-\(UUID().uuidString).sealed")
        defer { try? FileManager.default.removeItem(at: staged) }
        try bytes.write(to: staged, options: [.withoutOverwriting, .completeFileProtection])
        let file = try FileHandle(forWritingTo: staged); defer { try? file.close() }
        try file.synchronize()
        let stagedPlain = try AES.GCM.open(AES.GCM.SealedBox(combined: Data(contentsOf: staged)), using: key, authenticating: locatorAAD(scope.account))
        guard stagedPlain == payload else { throw VortxNativeError.invalidSnapshot }
        guard Darwin.rename(staged.path, locatorURL(scope.account).path) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        let directoryFD = Darwin.open(directory.path, O_RDONLY)
        guard directoryFD >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        defer { Darwin.close(directoryFD) }
        guard Darwin.fsync(directoryFD) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        let installed = try AES.GCM.open(AES.GCM.SealedBox(combined: Data(contentsOf: locatorURL(scope.account))),
                                        using: key, authenticating: locatorAAD(scope.account))
        guard installed == payload else { throw VortxNativeError.invalidSnapshot }
        if let installationKey {
            // Only account-key-authenticated state may acquire an installation index. A crash before
            // either publication leaves the file unindexed and blocks new-account admission.
            let ciphertext = try accountCiphertext(Data(contentsOf: url(scope)))
            try durableInstall(indexedCiphertext(ciphertext, scope: scope), at: url(scope))
            let indexPayload = try JSONEncoder().encode(AccountLocator(format: "vortx-native-account-locator-v2", scope: scope,
                                                                       accountLocatorDigest: Data(SHA256.hash(data: bytes))))
            guard let indexBytes = try AES.GCM.seal(indexPayload, using: installationKey,
                authenticating: indexAAD(indexURL(scope.account).lastPathComponent)).combined else { throw VortxNativeError.invalidSnapshot }
            try durableInstall(indexBytes, at: indexURL(scope.account))
        }
    }
    /// Offline recovery is read-only until the caller hydrates the proven checkpoint. Missing
    /// locator/checkpoint/archive is not permission to provision a new account.
    func recovery(account: String) throws -> Recovery? {
        lock.lock(); defer { lock.unlock() }
        let bytes: Data
        do { bytes = try Data(contentsOf: locatorURL(account)) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return nil }
        let plain = try AES.GCM.open(AES.GCM.SealedBox(combined: bytes), using: key, authenticating: locatorAAD(account))
        let locator = try JSONDecoder().decode(AccountLocator.self, from: plain)
        guard locator.format == "vortx-native-account-locator-v1", locator.scope.account == account else { throw VortxNativeError.invalidSnapshot }
        try locator.scope.validate()
        let stateBytes = try Data(contentsOf: url(locator.scope))
        let envelope = try open(stateBytes, scope: locator.scope)
        guard let bootstrap = envelope.bootstrap else { throw VortxNativeError.invalidSnapshot }
        bootstraps[locator.scope] = bootstrap
        remember(stateBytes, envelope, scope: locator.scope)
        return Recovery(scope: locator.scope, state: envelope.state, bootstrap: bootstrap)
    }
    /// A new cloud owner cannot reset a known account. Older unindexed checkpoints require
    /// their exact authenticated owner before opening; unknown files are never proof of absence.
    func authenticatedCheckpoint(scope: VortxAccountScope) throws -> String? {
        let known = try recovery(account: scope.account)
        guard known == nil || known?.scope == scope else { throw VortxNativeError.invalidSnapshot }
        let checkpoint = try read(scope: scope)
        if known == nil, checkpoint == nil {
            let files: [URL]
            do { files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) }
            catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return nil }
            let covered = try authenticatedInventory(for: scope)
            guard files.filter({ $0.lastPathComponent.hasPrefix("native-state-v1-") }).allSatisfy({ covered.contains($0.path) }) else { throw VortxNativeError.invalidSnapshot }
        }
        return checkpoint
    }
    private func open(_ data: Data, scope: VortxAccountScope) throws -> Envelope {
        let plain = try AES.GCM.open(AES.GCM.SealedBox(combined: accountCiphertext(data)), using: key, authenticating: scope.authenticatedData)
        guard let snapshot = String(data: plain, encoding: .utf8) else { throw VortxNativeError.invalidSnapshot }
        if let object = try JSONSerialization.jsonObject(with: plain) as? [String: Any], object["format"] != nil {
            let envelope = try JSONDecoder().decode(Envelope.self, from: plain)
            guard envelope.format == "vortx-native-checkpoint-v1" else { throw VortxNativeError.invalidSnapshot }
            _ = try scope.validateSnapshot(envelope.state)
            if let bootstrap = envelope.bootstrap { try VortxNativeBootstrapArchive.validate(bootstrap) }
            if let host = envelope.hostPreferences { try VortxNativeHostPreferences.validateSealed(host, scope: scope) }
            return envelope
        }
        // Dual-read the prior raw runtime format without ever treating a failed decode as absence.
        _ = try scope.validateSnapshot(snapshot)
        return Envelope(format: "vortx-native-checkpoint-v1", state: snapshot, bootstrap: nil)
    }
    func read(scope: VortxAccountScope) throws -> String? {
        lock.lock(); defer { lock.unlock() }
        try scope.validate()
        do {
            let bytes = try Data(contentsOf: url(scope))
            let envelope = try open(bytes, scope: scope)
            if let retained = envelope.bootstrap { bootstraps[scope] = retained }
            remember(bytes, envelope, scope: scope)
            return envelope.state
        }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return nil }
    }
    func commit(_ snapshot: String, scope: VortxAccountScope) throws {
        try commitSnapshot(snapshot, scope: scope, hostPreferences: nil)
    }
    func readHostPreferences(scope: VortxAccountScope) throws -> Data? {
        lock.lock(); defer { lock.unlock() }
        do { return try open(Data(contentsOf: url(scope)), scope: scope).hostPreferences }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return nil }
    }
    func readLegacyMaterial(scope: VortxAccountScope) throws -> Data? {
        lock.lock(); defer { lock.unlock() }
        let bootstrap: Data?
        do { bootstrap = try open(Data(contentsOf: url(scope)), scope: scope).bootstrap }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { bootstrap = bootstraps[scope] }
        guard let bootstrap, let material = try JSONDecoder().decode(VortxJSON.self, from: bootstrap)["legacyImportMaterial"] else { return nil }
        return try JSONEncoder().encode(material)
    }
    func readLegacyProfileEdits(scope: VortxAccountScope) throws -> VortxJSON? {
        lock.lock(); defer { lock.unlock() }
        let bootstrap: Data?
        do { bootstrap = try open(Data(contentsOf: url(scope)), scope: scope).bootstrap }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { bootstrap = bootstraps[scope] }
        guard let bootstrap else { return nil }
        return try JSONDecoder().decode(VortxJSON.self, from: bootstrap)["hostDocument"]?["profileEdits"]
    }
    func commit(_ snapshot: String, scope: VortxAccountScope, hostPreferences: Data) throws {
        try VortxNativeHostPreferences.validateSealed(hostPreferences, scope: scope)
        try commitSnapshot(snapshot, scope: scope, hostPreferences: hostPreferences)
    }
    private func remember(_ bytes: Data, _ envelope: Envelope, scope: VortxAccountScope) {
        installed[scope] = Installed(digest: SHA256.hash(data: bytes), state: envelope.state,
                                     bootstrap: envelope.bootstrap, hostPreferences: envelope.hostPreferences)
    }
    private func commitSnapshot(_ snapshot: String, scope: VortxAccountScope, hostPreferences: Data?) throws {
        lock.lock(); defer { lock.unlock() }
        var stage = VortxCheckpointDiagnostics.Stage.validate
        do {
            try scope.validate(); _ = try scope.validateSnapshot(snapshot)
            var retainedHost = hostPreferences
            stage = .retainExisting
            var existing: Data?
            do { existing = try Data(contentsOf: url(scope)) }
            catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError {}
            if let existing {
                if let known = installed[scope], known.digest == SHA256.hash(data: existing) {
                    // The exact bytes this store last wrote or fully opened: carry their parts forward.
                    if let bootstrap = known.bootstrap { bootstraps[scope] = bootstrap }
                    if retainedHost == nil { retainedHost = known.hostPreferences }
                    // Nothing would change on disk, so there is nothing to write.
                    if known.state == snapshot, known.hostPreferences == retainedHost, known.bootstrap == bootstraps[scope] { return }
                } else {
                    // The first migration source is immutable, even for a fresh store instance making a
                    // later commit. A decode/read failure must not replace it with a newer/empty carrier.
                    let retained = try open(existing, scope: scope)
                    if let bootstrap = retained.bootstrap { bootstraps[scope] = bootstrap }
                    if retainedHost == nil { retainedHost = retained.hostPreferences }
                }
            }
            let bootstrap = bootstraps[scope]
            let envelope = Envelope(format: "vortx-native-checkpoint-v1", state: snapshot, bootstrap: bootstrap, hostPreferences: retainedHost)
            stage = .seal
            let sealed = try AES.GCM.seal(JSONEncoder().encode(envelope), using: key, authenticating: scope.authenticatedData)
            guard let combined = sealed.combined else { throw VortxNativeError.invalidSnapshot }
            let bytes = try indexedCiphertext(combined, scope: scope)
            stage = .createDirectory
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let staged = directory.appendingPathComponent(".native-checkpoint-\(UUID().uuidString).sealed")
            defer { try? FileManager.default.removeItem(at: staged) }
            stage = .stageWrite
            try bytes.write(to: staged, options: [.withoutOverwriting, .completeFileProtection])
            stage = .stageSync
            let file = try FileHandle(forWritingTo: staged); defer { try? file.close() }
            try file.synchronize()
            // The sealed bytes come from parts validated above, so decoding them again proves nothing new.
            // Byte equality proves the disk holds exactly what was sealed (as durableInstall checks).
            stage = .stageReadback
            guard try Data(contentsOf: staged) == bytes else { throw VortxNativeError.invalidSnapshot }
            stage = .install
            guard Darwin.rename(staged.path, url(scope).path) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
            stage = .directorySync
            let directoryFD = Darwin.open(directory.path, O_RDONLY)
            guard directoryFD >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
            defer { Darwin.close(directoryFD) }
            guard Darwin.fsync(directoryFD) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
            stage = .installedReadback
            guard try Data(contentsOf: url(scope)) == bytes else { throw VortxNativeError.invalidSnapshot }
            installed[scope] = Installed(digest: SHA256.hash(data: bytes), state: snapshot, bootstrap: bootstrap,
                                         hostPreferences: retainedHost)
        } catch {
            // The file is uncertain now: the next commit opens it in full.
            installed[scope] = nil
            VortxCheckpointDiagnostics.report(stage: stage, error: error)
            throw error
        }
    }
}

/// Read-only migration carrier. The authenticated sync owner supplies documents from either legacy
/// collection. No token is accepted, no old key is erased, and no unscoped roster is auto-adopted.
struct VortxLegacyImport: Sendable {
    static let collection = "stremioxProfiles"
    static let rosterID = "stremiox:profiles"
    let scope: VortxAccountScope
    let documents: [String: String]
    init(scope: VortxAccountScope, documents: [String: String]) throws {
        try scope.validate()
        for (id, json) in documents {
            guard id == Self.rosterID || (id.hasPrefix("stremiox:watch:") && UUID(uuidString: String(id.dropFirst("stremiox:watch:".count))) != nil)
            else { throw VortxNativeError.invalidSnapshot }
            _ = try JSONDecoder().decode(VortxJSON.self, from: Data(json.utf8))
        }
        self.scope = scope; self.documents = documents
    }
}

enum VortxNativeScreenState: Sendable { case loading, ready(VortxJSON), failed }

private final class VortxSessionLease: @unchecked Sendable {
    private let lock = NSLock()
    private var active = true
    func revoke() { lock.lock(); active = false; lock.unlock() }
    func withActive<T>(_ operation: () throws -> T) throws -> T {
        lock.lock(); defer { lock.unlock() }
        guard active else { throw VortxNativeError.closed }; return try operation()
    }
}

/// One live writer per scope in this process, even across separate encrypted-store instances.
/// Replacement must close the existing session first; racing opens fail without touching disk.
private final class VortxScopeWriter: @unchecked Sendable {
    private final class Registry: @unchecked Sendable {
        let lock = NSLock()
        var leases: [String: VortxSessionLease] = [:]
    }
    private static let registry = Registry()
    private let scope: VortxAccountScope
    private let lock = NSLock()
    private var released = true
    init(scope: VortxAccountScope, lease: VortxSessionLease) throws {
        self.scope = scope
        let inserted = Self.registry.lock.withLock { () -> Bool in
            guard Self.registry.leases[scope.account] == nil else { return false }
            Self.registry.leases[scope.account] = lease; return true
        }
        guard inserted else { throw VortxNativeError.unavailable }
        released = false
    }
    func release() {
        lock.lock(); defer { lock.unlock() }; guard !released else { return }; released = true
        _ = Self.registry.lock.withLock { Self.registry.leases.removeValue(forKey: scope.account) }
    }
    static func revokeAll() {
        let leases = registry.lock.withLock { Array(registry.leases.values) }
        leases.forEach { $0.revoke() }
    }
    deinit { release() }
}

/// An explicitly constructed, account-owned native session, selected only by the compile-time gate,
/// never a UI preference. All state mutation is copy/apply/checkpoint/swap: a failed durable write leaves the
/// published runtime unchanged. Actor isolation fences logout/profile switches across async loads.
/// Privacy-safe outline of a held legacy delta, for diagnostics: which material buckets changed for
/// which profile slot (owner, p1, p2…), entry counts, and the schema field names that differ.
/// Never titles, names, identifiers or values, so a device log can say what was held.
enum VortxLegacyDeltaSummary {
    private static let perProfile = ["addons", "libraries", "watches", "identityLinks", "ownAccountSources"]
    /// Same row identity the kernel reconciles on (watch: videoId, else metaId).
    private static let identityKeys = ["videoId", "metaId", "_id", "id", "key", "transportUrl"]
    private static let maxLength = 600

    static func describe(base: VortxJSON, next: VortxJSON, ownerProfileID: String) -> String {
        var parts: [String] = []
        if let before = number(base["rosterModifiedSeconds"]), let after = number(next["rosterModifiedSeconds"]), before != after {
            parts.append("rosterClock=" + (after < before ? "older" : "newer"))
        }
        if let change = diff(base["roster"], next["roster"]) { parts.append("roster(\(change))") }
        if let change = diff(base["deletedProfileIds"], next["deletedProfileIds"]) { parts.append("deletedProfiles(\(change))") }
        let slots = slotLabels(base: base, next: next, owner: ownerProfileID)
        for field in perProfile {
            let lhs = object(base[field]), rhs = object(next[field])
            for id in Set(lhs.keys).union(rhs.keys).sorted(by: { (slots[$0] ?? "px") < (slots[$1] ?? "px") }) {
                let slot = slots[id] ?? "px"
                if isObject(lhs[id]) || isObject(rhs[id]) {
                    let before = object(lhs[id]), after = object(rhs[id])
                    for sub in Set(before.keys).union(after.keys).sorted() {
                        if let change = diff(before[sub], after[sub]) { parts.append("\(field).\(sub):\(slot)(\(change))") }
                    }
                } else if let change = diff(lhs[id], rhs[id]) {
                    parts.append("\(field):\(slot)(\(change))")
                }
            }
        }
        let text = parts.isEmpty ? "none" : parts.joined(separator: ",")
        return text.count <= maxLength ? text : String(text.prefix(maxLength)) + "..."
    }

    /// `+added/-removed/~changed:fields`, `reordered`, or `~` for a changed scalar/object.
    private static func diff(_ lhs: VortxJSON?, _ rhs: VortxJSON?) -> String? {
        guard lhs != rhs else { return nil }
        guard isArrayOrAbsent(lhs), isArrayOrAbsent(rhs) else { return "~" }
        let before = keyed(lhs?.array ?? []), after = keyed(rhs?.array ?? [])
        let added = after.keys.filter { before[$0] == nil }.count
        let removed = before.keys.filter { after[$0] == nil }.count
        var changed = 0, fields = Set<String>()
        for (key, row) in after {
            guard let old = before[key], old != row else { continue }
            changed += 1
            let oldFields = object(old), newFields = object(row)
            fields.formUnion(Set(oldFields.keys).union(newFields.keys).filter { oldFields[$0] != newFields[$0] })
        }
        var out: [String] = []
        if added > 0 { out.append("+\(added)") }
        if removed > 0 { out.append("-\(removed)") }
        if changed > 0 { out.append("~\(changed)" + (fields.isEmpty ? "" : ":" + fields.sorted().prefix(6).joined(separator: "|"))) }
        return out.isEmpty ? "reordered" : out.joined(separator: "/")
    }
    private static func keyed(_ rows: [VortxJSON]) -> [String: VortxJSON] {
        var result: [String: VortxJSON] = [:]
        for row in rows { result[identity(row)] = row }
        return result
    }
    private static func identity(_ row: VortxJSON) -> String {
        for key in identityKeys { if case .string(let id)? = row[key] { return key + ":" + id } }
        let encoder = JSONEncoder(); encoder.outputFormatting = .sortedKeys
        return (try? encoder.encode(row)).map { String(decoding: $0, as: UTF8.self) } ?? "?"
    }
    private static func slotLabels(base: VortxJSON, next: VortxJSON, owner: String) -> [String: String] {
        var labels = [owner: "owner"], index = 0
        for row in (base["roster"]?.array ?? []) + (next["roster"]?.array ?? []) {
            guard case .string(let id)? = row["id"], labels[id] == nil else { continue }
            index += 1
            labels[id] = "p\(index)"
        }
        return labels
    }
    private static func object(_ value: VortxJSON?) -> [String: VortxJSON] {
        if case .object(let fields)? = value { return fields }
        return [:]
    }
    private static func isObject(_ value: VortxJSON?) -> Bool {
        if case .object? = value { return true }
        return false
    }
    private static func isArrayOrAbsent(_ value: VortxJSON?) -> Bool {
        switch value {
        case nil, .null?, .array?: return true
        default: return false
        }
    }
    private static func number(_ value: VortxJSON?) -> Double? {
        switch value {
        case .integer(let v)?: return Double(v)
        case .unsigned(let v)?: return Double(v)
        case .number(let v)?: return v
        default: return nil
        }
    }
}

actor VortxNativeSession {
    enum CatalogScreen: String, Sendable { case board, discover, search }
    let scope: VortxAccountScope
    private let abi: any VortxRuntimeABI
    private let store: any VortxCheckpointStore
    private let transport: any VortxResourceTransport
    private var runtime: VortxNativeRuntime
    private var hostPreferences: VortxNativeHostPreferences
    private let legacyBaseline: Data?
    private let legacyProfileEdits: VortxJSON?
    /// Outcome of the latest legacy carrier offer (first adoption or dispatch); nil before one.
    private(set) var legacyReconciliation: LegacyReconciliation?
    /// Host diagnostics sink. Only outcome transitions are reported, never material bytes.
    private let diagnostics: (@Sendable (String) -> Void)?
    private var closed = false
    private var persistenceFailed = false
    // Playback position ticks are applied in memory at once but written to the sealed checkpoint at most
    // every 30 s. Every other mutation writes immediately and carries the latest position with it, and
    // flushPendingProgress writes a held-back position on pause, player close, background or quit. A crash
    // can lose at most the last 30 s of watch position, never anything else.
    private var pendingProgressCommit = false
    private var lastDurableCommit: TimeInterval?
    private var writeProgressUntil: TimeInterval = 0
    static let progressCommitInterval: TimeInterval = 30
    private static func isProgressAction(_ action: String) -> Bool {
        (try? JSONDecoder().decode(VortxJSON.self, from: Data(action.utf8)))?["type"] == .string("report_progress")
    }
    private var epoch = UUID()
    private var tickets: [String: UUID] = [:]
    private var bridges: [String: VortxResourceBridge] = [:]
    private var resourceBatchBridges: [String: [VortxResourceBridge]] = [:]
    private var catalogPlans: [String: VortxJSON] = [:]
    private var pages: [String: [VortxResourceSnapshot]] = [:]
    private var screens: [String: VortxNativeScreenState] = [:]
    private var catalogRegistries: [String: [VortxResourceAddon]] = [:]
    private nonisolated let lease = VortxSessionLease()
    private let writer: VortxScopeWriter

    init(scope: VortxAccountScope, ownerName: String, abi: any VortxRuntimeABI,
         store: any VortxCheckpointStore, transport: any VortxResourceTransport,
         allowNewAccount: Bool = false, legacy: VortxLegacyImport? = nil,
         initialActions: [String] = [], hostActor: String = UUID().uuidString.lowercased(),
         sourceAuthority: (any VortxMutationAuthority)? = nil, authenticatedSourceArchive: Data? = nil,
         initialLegacyMaterial: Data? = nil, diagnostics: (@Sendable (String) -> Void)? = nil) throws {
        try scope.validate()
        self.diagnostics = diagnostics
        guard legacy == nil || legacy?.scope == scope else { throw VortxNativeError.invalidSnapshot }
        self.scope = scope; self.abi = abi; self.store = store; self.transport = transport
        legacyBaseline = try store.readLegacyMaterial(scope: scope)
        legacyProfileEdits = try store.readLegacyProfileEdits(scope: scope)
        hostPreferences = try VortxNativeHostPreferences(scope: scope, actor: hostActor, sealed: store.readHostPreferences(scope: scope))
        try hostPreferences.retainAuthenticatedSourceArchive(authenticatedSourceArchive)
        let initialHost = try hostPreferences.encoded()
        writer = try VortxScopeWriter(scope: scope, lease: lease)
        if let captured = try store.read(scope: scope) {
            _ = try scope.validateSnapshot(captured)
            runtime = try VortxNativeRuntime(abi: abi, snapshot: captured)
            // Hydration may migrate projections. Commit that full state before publishing it.
            let hydrated = try runtime.stateJSON()
            try scope.validateHydration(from: captured, to: hydrated)
            try Self.bind(runtime, scope: scope)
            let bound = try runtime.stateJSON()
            try Self.validateAuthenticatedSources(authenticatedSourceArchive, state: scope.validateSnapshot(bound))
            try lease.withActive {
                func commit() throws {
                    if authenticatedSourceArchive != nil { try store.commit(bound, scope: scope, hostPreferences: initialHost) }
                    else { try store.commit(bound, scope: scope) }
                }
                if let sourceAuthority { try sourceAuthority.withActive(commit) } else { try commit() }
            }
        } else {
            guard allowNewAccount, legacy?.documents.isEmpty != false else { throw VortxNativeError.invalidSnapshot }
            runtime = try VortxNativeRuntime(abi: abi, ownerID: scope.ownerProfileID, ownerName: ownerName)
            try Self.bind(runtime, scope: scope)
            for action in initialActions { try Self.apply(runtime, action: action) }
            if let initialLegacyMaterial {
                let outcome = try Self.validateLegacyReceipt(runtime, scope: scope, material: initialLegacyMaterial, baselineMaterial: legacyBaseline, abi: abi)
                legacyReconciliation = outcome
                if case .held(let reason, let delta) = outcome {
                    diagnostics?("legacy_reconcile result=held stage=adoption reason=\(reason) delta=\(delta)")
                }
            }
            let bound = try runtime.stateJSON()
            _ = try scope.validateSnapshot(bound)
            try Self.validateAuthenticatedSources(authenticatedSourceArchive, state: scope.validateSnapshot(bound))
            try lease.withActive {
                func commit() throws {
                    if authenticatedSourceArchive != nil { try store.commit(bound, scope: scope, hostPreferences: initialHost) }
                    else { try store.commit(bound, scope: scope) }
                }
                if let sourceAuthority { try sourceAuthority.withActive(commit) } else { try commit() }
            }
        }
    }
    private static func bind(_ runtime: VortxNativeRuntime, scope: VortxAccountScope) throws {
        let action = VortxJSON.object(["type": .string("bind_sync_scope"), "scope": .string(scope.account)])
        try apply(runtime, action: String(decoding: JSONEncoder().encode(action), as: UTF8.self))
        let bound = try scope.validateSnapshot(runtime.stateJSON())
        guard bound["nativeSync"]?["scope"] == .string(scope.account) else { throw VortxNativeError.invalidSnapshot }
    }
    private static func validateAuthenticatedSources(_ archive: Data?, state: VortxJSON) throws {
        guard let archive else { return }
        try VortxNativeBootstrapArchive.validate(archive)
        guard case .string(let account) = state["nativeSync"]?["scope"],
              case .string(let owner) = state["nativeSync"]?["ownerProfileId"] else { throw VortxNativeError.invalidSnapshot }
        try VortxNativeWatchedArchive.validate(archive, scope: .init(account: account, ownerProfileID: owner))
        let decoded = try JSONDecoder().decode(VortxJSON.self, from: archive)
        guard case .object(let sources) = decoded["hostDocument"]?["ownAccountSources"] else { throw VortxNativeError.invalidSnapshot }
        for (id, value) in sources {
            guard case .object(let fields) = value, Set(fields.keys) == ["verifiedStreamingUid", "sourceDocumentBase64"],
                  case .string(let raw) = fields["sourceDocumentBase64"], let bytes = Data(base64Encoded: raw), bytes.base64EncodedString() == raw else { throw VortxNativeError.invalidSnapshot }
            let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
            let legacy = state["nativeSync"]?["legacyImport"]
            let proof = legacy?["baseline"]?["ownAccountSources"]?[id]
            var paired = proof?["verifiedStreamingUid"] == fields["verifiedStreamingUid"] &&
                (proof?["sourceDocumentSha256"] == .string(digest) || legacy?["ownAccountSourceHistory"]?[id]?[digest] != nil)
            if let slots = state["nativeSync"]?["accountSlots"]?[id],
               case .object(let retained) = slots["slots"] {
                let matches = retained.values.filter { $0["account"] == slots["activeBinding"]?["account"] }
                guard matches.count == 1 else { throw VortxNativeError.invalidSnapshot }
                if matches[0]["sourceBaseline"]?["source"]?["verifiedStreamingUid"] == value["verifiedStreamingUid"] {
                    paired = paired || matches[0]["sourceBaseline"]?["source"]?["sourceDocumentSha256"] == .string(digest)
                        || matches[0]["sourceHistory"]?[digest] != nil
                }
            }
            guard paired else { throw VortxNativeError.invalidSnapshot }
        }
        if let pending = decoded["hostDocument"]?["ownAccountOverlayPending"] {
            guard case .object(let profiles) = pending else { throw VortxNativeError.invalidSnapshot }
            for (id, entry) in profiles {
                guard UUID(uuidString: id)?.uuidString == id, case .object(let fields) = entry,
                      Set(fields.keys) == ["verifiedStreamingUid", "sourceDocumentSha256", "profileOverlayBase64", "reason"]
                        || Set(fields.keys) == ["profileOverlayBase64", "reason"],
                      case .string(let reason) = fields["reason"], ["missing_witness", "changed_witness"].contains(reason),
                      case .string(let raw) = fields["profileOverlayBase64"], let bytes = Data(base64Encoded: raw), bytes.base64EncodedString() == raw,
                      (try? VortxProfileOverlayWitness.decodeObject(json: bytes)) != nil,
                      let overlay = try? JSONDecoder().decode(VortxJSON.self, from: bytes),
                      case .object(let root) = overlay, Set(root.keys).isSubset(of: ["vortx", "webProgress"]) else { throw VortxNativeError.invalidSnapshot }
                if let vortx = root["vortx"] {
                    guard case .object(let fields) = vortx, Set(fields.keys) == ["byProfile"],
                          case .object(let profiles) = fields["byProfile"], Set(profiles.keys) == [id] else { throw VortxNativeError.invalidSnapshot }
                }
                if let web = root["webProgress"] {
                    guard case .object(let fields) = web, Set(fields.keys) == ["removed"],
                          case .object(let removed) = fields["removed"], Set(removed.keys) == ["byProfile"],
                          case .object(let profiles) = removed["byProfile"], Set(profiles.keys) == [id] else { throw VortxNativeError.invalidSnapshot }
                }
                // Pending is not an import receipt. It may only refer to an already validated
                // source for this exact profile, including a dormant historical account slot.
                if fields["verifiedStreamingUid"] == nil {
                    guard reason == "missing_witness", state["roster"]?["profiles"]?[id] != nil else { throw VortxNativeError.invalidSnapshot }
                    continue // No attribution: no reconnect may consume this raw intent.
                }
                guard case .string(let uid) = fields["verifiedStreamingUid"], !uid.isEmpty,
                      case .string(let digest) = fields["sourceDocumentSha256"],
                      digest.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { throw VortxNativeError.invalidSnapshot }
                let receipt = state["nativeSync"]?["legacyImport"]
                let legacy = receipt?["baseline"]?["ownAccountSources"]?[id]
                var proven = legacy?["verifiedStreamingUid"] == .string(uid)
                    && (legacy?["sourceDocumentSha256"] == .string(digest) || receipt?["ownAccountSourceHistory"]?[id]?[digest] != nil)
                if case .object(let slots) = state["nativeSync"]?["accountSlots"]?[id]?["slots"] {
                    proven = proven || slots.values.contains {
                        $0["sourceBaseline"]?["source"]?["verifiedStreamingUid"] == .string(uid)
                            && ($0["sourceBaseline"]?["source"]?["sourceDocumentSha256"] == .string(digest) || $0["sourceHistory"]?[digest] != nil)
                    }
                }
                guard proven else { throw VortxNativeError.invalidSnapshot }
            }
        }
    }
    private static func apply(_ runtime: VortxNativeRuntime, action: String) throws {
        let result = try runtime.dispatch(action, now: UInt64(Date().timeIntervalSince1970))
        guard try JSONDecoder().decode(VortxJSON.self, from: Data(result.utf8))["ok"] == .bool(true) else { throw VortxNativeError.invalidResponse }
    }
    /// A never-backed account must win create-only publication before it owns a durable local
    /// checkpoint. This detached candidate has no writer lease, checkpoint or mounted session.
    nonisolated static func detachedLegacySync(scope: VortxAccountScope, ownerName: String, material: Data,
                                               abi: any VortxRuntimeABI) throws -> VortxJSON {
        try scope.validate()
        let candidate = try VortxNativeRuntime(abi: abi, ownerID: scope.ownerProfileID, ownerName: ownerName)
        defer { candidate.close() }
        try bind(candidate, scope: scope)
        let action: VortxJSON = .object(["type": .string("import_legacy_sync"), "scope": .string(scope.account),
            "ownerProfileId": .string(scope.ownerProfileID), "material": try JSONDecoder().decode(VortxJSON.self, from: material)])
        try apply(candidate, action: String(decoding: JSONEncoder().encode(action), as: UTF8.self))
        guard let sync = try scope.validateSnapshot(candidate.stateJSON())["nativeSync"] else { throw VortxNativeError.invalidSnapshot }
        return sync
    }
    /// Before durable reopen/adoption, prove the complete legacy material against a detached
    /// kernel candidate. Exact receipt replay is compatible with older artifacts; changed intent
    /// requires the private kernel's causal reconciliation action. Missing receipts never seed.
    nonisolated static func authenticatedOwnAccountBaseline(scope: VortxAccountScope, ownerName: String,
                                                            snapshot: String?, nativeSync: VortxJSON?,
                                                            abi: any VortxRuntimeABI) throws -> Data? {
        let sync = try authenticatedAccountSync(scope: scope, ownerName: ownerName, snapshot: snapshot, nativeSync: nativeSync, abi: abi)
        guard [.integer(3), .integer(4)].contains(sync?["schemaVersion"] ?? .null),
              sync?["legacyImport"]?["schemaVersion"] == .integer(2) else { return nil }
        guard let baseline = sync?["legacyImport"]?["baseline"], baseline["schemaVersion"] == .integer(2),
              case .object(let proofs) = baseline["ownAccountSources"], !proofs.isEmpty else { throw VortxNativeError.invalidSnapshot }
        return try JSONEncoder().encode(baseline)
    }
    nonisolated static func authenticatedAccountSync(scope: VortxAccountScope, ownerName: String,
                                                     snapshot: String?, nativeSync: VortxJSON?, abi: any VortxRuntimeABI) throws -> VortxJSON? {
        guard snapshot != nil || nativeSync != nil else { return nil }
        try scope.validate()
        let candidate: VortxNativeRuntime
        if let snapshot {
            _ = try scope.validateSnapshot(snapshot)
            candidate = try VortxNativeRuntime(abi: abi, snapshot: snapshot)
            try scope.validateHydration(from: snapshot, to: candidate.stateJSON())
        } else { candidate = try VortxNativeRuntime(abi: abi, ownerID: scope.ownerProfileID, ownerName: ownerName) }
        defer { candidate.close() }
        try bind(candidate, scope: scope)
        if let nativeSync {
            let action = VortxJSON.object(["type": .string("merge_native_sync"), "document": nativeSync])
            try apply(candidate, action: String(decoding: JSONEncoder().encode(action), as: UTF8.self))
        }
        return try scope.validateSnapshot(candidate.stateJSON())["nativeSync"]
    }
    enum LegacyCompatibilityStage: String { case scope, hydration, binding, nativeMerge = "native_merge", receipt, replay, reconciliation }
    /// The kernel's answer when offered the current legacy cloud carriers.
    enum LegacyReconciliation: Equatable, Sendable {
        case accepted
        /// The kernel rejected this legacy delta. Its reconcile is atomic, so nothing from it was
        /// imported; the immutable migration bootstrap is unchanged and the untouched legacy
        /// carriers are offered again on the next sync. Native state keeps mounting and syncing.
        /// `delta` is a `VortxLegacyDeltaSummary` outline of what was held.
        case held(reason: String, delta: String)
    }
    @discardableResult
    nonisolated static func validateLegacyCompatibility(scope: VortxAccountScope, ownerName: String,
                                                        snapshot: String?, nativeSync: VortxJSON?, material: Data,
                                                        abi: any VortxRuntimeABI, baselineMaterial: Data? = nil,
                                                        onStage: (LegacyCompatibilityStage) -> Void = { _ in }) throws -> LegacyReconciliation {
        onStage(.scope)
        try scope.validate()
        let candidate: VortxNativeRuntime
        if let snapshot {
            onStage(.hydration)
            _ = try scope.validateSnapshot(snapshot)
            candidate = try VortxNativeRuntime(abi: abi, snapshot: snapshot)
        } else { candidate = try VortxNativeRuntime(abi: abi, ownerID: scope.ownerProfileID, ownerName: ownerName) }
        defer { candidate.close() }
        onStage(.binding)
        try bind(candidate, scope: scope)
        if let nativeSync {
            onStage(.nativeMerge)
            let action: VortxJSON = .object(["type": .string("merge_native_sync"), "document": nativeSync])
            try apply(candidate, action: String(decoding: JSONEncoder().encode(action), as: UTF8.self))
        }
        return try validateLegacyReceipt(candidate, scope: scope, material: material, baselineMaterial: baselineMaterial, abi: abi, onStage: onStage)
    }
    @discardableResult
    private static func validateLegacyReceipt(_ candidate: VortxNativeRuntime, scope: VortxAccountScope, material: Data, baselineMaterial: Data?,
                                             abi: any VortxRuntimeABI,
                                             onStage: (LegacyCompatibilityStage) -> Void = { _ in }) throws -> LegacyReconciliation {
        onStage(.receipt)
        let state = try scope.validateSnapshot(candidate.stateJSON())
        let nativeSchema = legacyImportSchema(state["nativeSync"]?["legacyImport"]?["schemaVersion"])
        let materialValue = try strictJSONObject(material)
        guard let nativeSchema, nativeSchema == legacyImportSchema(materialValue["schemaVersion"]), nativeSchema == 1 || nativeSchema == 2 else {
            throw VortxNativeError.invalidSnapshot
        }
        let replay: VortxJSON = .object(["type": .string("import_legacy_sync"), "scope": .string(scope.account),
                                       "ownerProfileId": .string(scope.ownerProfileID),
                                       "material": materialValue])
        onStage(.replay)
        let replayResult = try candidate.dispatch(String(decoding: JSONEncoder().encode(replay), as: UTF8.self), now: UInt64(Date().timeIntervalSince1970))
        if try JSONDecoder().decode(VortxJSON.self, from: Data(replayResult.utf8))["ok"] == .bool(true) { return .accepted }
        // Only the private kernel decides which old-peer causal changes are supported. Older
        // artifacts reject this additive action and remain closed; there is no host reducer.
        var reconciliation: [String: VortxJSON] = ["type": .string("reconcile_legacy_sync"), "scope": .string(scope.account),
            "ownerProfileId": .string(scope.ownerProfileID), "material": materialValue]
        if state["nativeSync"]?["legacyImport"]?["baseline"] == nil, let baselineMaterial {
            reconciliation["baselineMaterial"] = try strictJSONObject(baselineMaterial)
        }
        onStage(.reconciliation)
        let result = try candidate.dispatch(String(decoding: JSONEncoder().encode(VortxJSON.object(reconciliation)), as: UTF8.self),
                                            now: UInt64(Date().timeIntervalSince1970))
        let response = try JSONDecoder().decode(VortxJSON.self, from: Data(result.utf8))
        if response["ok"] == .bool(true) { return .accepted }
        // A rejected legacy delta must not take native restore, export and receive down with it.
        let base = state["nativeSync"]?["legacyImport"]?["baseline"] ?? baselineMaterial.flatMap { canonicalLegacyMaterial($0, scope: scope, abi: abi) }
        let next = canonicalLegacyMaterial(material, scope: scope, abi: abi)
        let delta: String
        if let base, let next { delta = VortxLegacyDeltaSummary.describe(base: base, next: next, ownerProfileID: scope.ownerProfileID) }
        else { delta = base == nil ? "baseline_unavailable" : "canonical_unavailable" }
        return .held(reason: legacyRejectionCode(response["error"]), delta: delta)
    }
    /// The kernel's canonical form of a legacy material (what it stores as a receipt baseline), via a
    /// detached scratch import, so a held delta is outlined like-for-like rather than raw vs normalized.
    private static func canonicalLegacyMaterial(_ material: Data, scope: VortxAccountScope, abi: any VortxRuntimeABI) -> VortxJSON? {
        (try? detachedLegacySync(scope: scope, ownerName: "Owner", material: material, abi: abi))?["legacyImport"]?["baseline"]
    }
    /// The kernel's reconcile rejections are fixed lowercase phrases. Anything else (for example
    /// a serde message quoting material bytes) is reduced to a constant so no account data is logged.
    nonisolated static func legacyRejectionCode(_ error: VortxJSON?) -> String {
        guard case .string(let text) = error, text.count <= 120,
              text.range(of: "^[a-z][a-z0-9 /_-]*$", options: .regularExpression) != nil else { return "unclassified" }
        return text.replacingOccurrences(of: " ", with: "_")
    }
    /// Which profiles' saved Library lost titles across a sync merge, as `owner(n)` / `p<tag>(n)`, so a
    /// title that disappears can be traced to the sync that removed it. The tag is a short digest of the
    /// profile ID, stable across devices; titles and IDs are never logged. Nil when nothing was removed.
    nonisolated static func libraryShrink(from old: VortxJSON, to new: VortxJSON, ownerProfileID: String) -> String? {
        guard case .object(let before)? = old["libraries"] else { return nil }
        func members(_ library: VortxJSON?) -> Set<String> {
            Set((library?["items"]?.array ?? []).map { item in
                ["kind", "type", "id"].map { field in item[field].map { String(describing: $0) } ?? "" }.joined(separator: "|")
            })
        }
        let parts = before.keys.sorted().compactMap { id -> String? in
            let removed = members(before[id]).subtracting(members(new["libraries"]?[id])).count
            guard removed > 0 else { return nil }
            let tag = id == ownerProfileID ? "owner"
                : "p" + SHA256.hash(data: Data(id.utf8)).prefix(2).map { String(format: "%02x", $0) }.joined()
            return "\(tag)(\(removed))"
        }
        return parts.isEmpty ? nil : parts.joined(separator: ",")
    }
    private func recordLegacyReconciliation(_ outcome: LegacyReconciliation) {
        defer { legacyReconciliation = outcome }
        guard outcome != legacyReconciliation else { return }
        switch outcome {
        case .held(let reason, let delta): diagnostics?("legacy_reconcile result=held stage=sync reason=\(reason) delta=\(delta)")
        case .accepted where legacyReconciliation != nil: diagnostics?("legacy_reconcile result=accepted stage=sync")
        case .accepted: break
        }
    }
    private static func strictJSONObject(_ data: Data) throws -> VortxJSON {
        let object = try VortxProfileOverlayWitness.decodeObject(json: data)
        return try JSONDecoder().decode(VortxJSON.self,
                                       from: JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes]))
    }
    private static func legacyImportSchema(_ value: VortxJSON?) -> Int? {
        switch value {
        case .integer(let schema): return schema >= 0 && schema <= Int64(Int.max) ? Int(schema) : nil
        case .unsigned(let schema): return schema <= UInt64(Int.max) ? Int(schema) : nil
        default: return nil
        }
    }
    func close() {
        revoke(); closed = true; invalidateScreens(); runtime.close(); writer.release()
    }
    /// Synchronous logout boundary: waits for an already-running atomic commit, then forbids later
    /// commits even before the actor processes close(). No outgoing account writes occur after return.
    nonisolated func revoke() { lease.revoke() }
    /// Also reaches an authenticated mount that has opened a checkpoint but has not yet installed
    /// its facade. Owner/profile boundaries therefore revoke writes during every bootstrap await.
    nonisolated static func revokeAllForOwnerBoundary() { VortxScopeWriter.revokeAll() }
    private func invalidateScreens() {
        epoch = UUID(); bridges.values.forEach { $0.invalidate() }
        resourceBatchBridges.values.flatMap { $0 }.forEach { $0.invalidate() }
        resourceBatchBridges.removeAll(); catalogPlans.removeAll()
        tickets.removeAll(); pages.removeAll(); screens.removeAll(); catalogRegistries.removeAll()
    }
    func invalidateResources() { invalidateScreens() }
    func stateJSON() throws -> String {
        guard !closed else { throw VortxNativeError.closed }; return try lease.withActive { try runtime.stateJSON() }
    }
    func playbackProjection() throws -> VortxJSON {
        let state = try scope.validateSnapshot(stateJSON())
        guard let profile = state["activeProfileId"] else { throw VortxNativeError.invalidSnapshot }
        let query = VortxJSON.object(["kind": .string("profile_playback"), "profileId": profile])
        let result = try lease.withActive { try runtime.resolve(String(decoding: JSONEncoder().encode(query), as: UTF8.self)) }
        let response = try JSONDecoder().decode(VortxJSON.self, from: Data(result.utf8))
        guard response["kind"] == .string("profile_playback"), response["profileId"] == profile,
              response["continueWatching"]?.array != nil, response["history"]?.array != nil,
              case .object = response["watchedVideoIdsByTitle"], case .object = response["watchedTitles"],
              case .object = response["resumeById"] else { throw VortxNativeError.invalidResponse }
        return response
    }
    func resumeSeconds(id: String, profileID: String) throws -> Double {
        guard try scope.validateSnapshot(stateJSON())["activeProfileId"] == .string(profileID) else { throw VortxNativeError.superseded }
        let query = VortxJSON.object(["kind": .string("resume_point"), "id": .string(id)])
        let result = try lease.withActive { try runtime.resolve(String(decoding: JSONEncoder().encode(query), as: UTF8.self)) }
        let response = try JSONDecoder().decode(VortxJSON.self, from: Data(result.utf8))
        guard response["kind"] == .string("resume_point") else { throw VortxNativeError.invalidResponse }
        if response["resume"] == .null { return 0 }
        guard let offset = try response["resume"]?["offsetMs"]?.decode(UInt64.self) else { throw VortxNativeError.invalidResponse }
        return Double(offset) / 1000
    }
    /// The kernel materializes membership/order; hosts do not reproduce its merge reducer.
    func addonSnapshot() throws -> (state: VortxJSON, inventories: [String: [VortxJSON]]) {
        let state = try scope.validateSnapshot(stateJSON())
        guard case .object(let profiles) = state["roster"]?["profiles"] else { throw VortxNativeError.invalidSnapshot }
        var inventories: [String: [VortxJSON]] = [:]
        for (id, profile) in profiles where profile["deleted"] != .bool(true) {
            let query: VortxJSON = .object(["kind": .string("installed_addons"), "profileId": .string(id)])
            let raw = try lease.withActive { try runtime.resolve(String(decoding: JSONEncoder().encode(query), as: UTF8.self)) }
            let result = try JSONDecoder().decode(VortxJSON.self, from: Data(raw.utf8))
            guard result["kind"] == .string("installed_addons"), result["profileId"] == .string(id), let rows = result["addons"]?.array else {
                throw VortxNativeError.invalidResponse
            }
            inventories[id] = rows
        }
        return (state, inventories)
    }
    func resourceRegistry() throws -> [VortxResourceAddon] {
        let state = try scope.validateSnapshot(stateJSON())
        guard case .string(let active) = state["activeProfileId"], let profile = state["roster"]?["profiles"]?[active],
              let binding = profile["addons"], [.string("own"), .string("share_primary")].contains(binding) else { throw VortxNativeError.invalidSnapshot }
        let bucket = binding == .string("share_primary") ? scope.ownerProfileID : active
        let query = VortxJSON.object(["kind": .string("installed_addons"), "profileId": .string(bucket)])
        let result = try lease.withActive { try runtime.resolve(String(decoding: JSONEncoder().encode(query), as: UTF8.self)) }
        let response = try JSONDecoder().decode(VortxJSON.self, from: Data(result.utf8))
        guard response["kind"] == .string("installed_addons"), response["profileId"] == .string(bucket),
              let addons = response["addons"]?.array else { throw VortxNativeError.invalidResponse }
        let disabled = (try? profile["settings"]?["disabledAddons"]?.decode([String].self)) ?? []
        return try addons.compactMap { addon in
            guard case .string(let url) = addon["transportUrl"], !url.isEmpty,
                  case .object = addon["manifest"] else { throw VortxNativeError.invalidResponse }
            if disabled.contains(url) { return nil }
            let id = SHA256.hash(data: Data(url.utf8)).map { String(format: "%02x", $0) }.joined()
            return VortxResourceAddon(id: id, transportUrl: url, manifest: addon["manifest"])
        }
    }
    /// Native action wire only; full Stremio Ctx/player action compatibility is still an explicit gate.
    func hostPreferencesDocument() throws -> VortxJSON {
        try lease.withActive { try hostPreferences.document }
    }
    func websiteEditOutcome() throws -> VortxJSON {
        try lease.withActive {
            guard case .object(var result) = try VortxNativeProfileEditHost.outcome(hostPreferences.local) else { throw VortxNativeError.invalidResponse }
            result["addonConflicts"] = try JSONDecoder().decode(VortxJSON.self, from: JSONEncoder().encode(hostPreferences.local.websiteAddonConflicts ?? []))
            return .object(result)
        }
    }
    func pendingOwnAccountOverlays() throws -> VortxJSON {
        try lease.withActive {
            guard let archive = hostPreferences.local.authenticatedSourceArchive else { return .object([:]) }
            return try JSONDecoder().decode(VortxJSON.self, from: archive)["hostDocument"]?["ownAccountOverlayPending"] ?? .object([:])
        }
    }
    func authenticatedSourceArchive() throws -> Data? {
        try lease.withActive { hostPreferences.local.authenticatedSourceArchive }
    }
    @discardableResult func dispatch(_ actions: [String], now: UInt64, legacyMaterial: Data? = nil,
                                    hostRemote: VortxJSON? = nil, hostEdits: [VortxNativeHostPreferences.Edit] = [],
                                    legacyWatchlists: [UUID: [VortxNativeWatchlist.Entry]] = [:],
                                    websiteEvents: [VortxJSON] = [], websiteAddonEvents: [VortxJSON] = [], websiteBaseline: VortxNativeProfileEditHost.Baselines = [:],
                                    sourceAuthority: (any VortxMutationAuthority)? = nil, authenticatedSourceArchive: Data? = nil) throws -> [String] {
        guard !closed else { throw VortxNativeError.closed }
        guard !persistenceFailed else { throw VortxNativeError.checkpointUncertain }
        let old = try stateJSON()
        var candidate = try VortxNativeRuntime(abi: abi, snapshot: old)
        var installed = false
        defer { if !installed { candidate.close() } }
        try scope.validateHydration(from: old, to: candidate.stateJSON())
        var results: [String] = []
        for action in actions {
            let result = try candidate.dispatch(action, now: now)
            let value = try JSONDecoder().decode(VortxJSON.self, from: Data(result.utf8))
            guard value["ok"] == .bool(true) else { throw VortxNativeError.invalidResponse }
            results.append(result)
        }
        if let legacyMaterial {
            recordLegacyReconciliation(try Self.validateLegacyReceipt(candidate, scope: scope, material: legacyMaterial, baselineMaterial: legacyBaseline, abi: abi))
        }
        var state = try scope.validateSnapshot(candidate.stateJSON())
        if legacyMaterial != nil, let shrink = try? Self.libraryShrink(from: JSONDecoder().decode(VortxJSON.self, from: Data(old.utf8)),
                                                                       to: state, ownerProfileID: scope.ownerProfileID) {
            diagnostics?("library_shrink stage=sync removed=\(shrink)")
        }
        try Self.validateAuthenticatedSources(authenticatedSourceArchive, state: state)
        var candidateHost = hostPreferences
        try candidateHost.retainAuthenticatedSourceArchive(authenticatedSourceArchive)
        try candidateHost.merge(hostRemote, scope: scope)
        for (profile, entries) in legacyWatchlists {
            // The unchanged legacy roster may still carry a profile explicitly deleted by the
            // native peer. Preserve that old array in its source document, never resurrect the
            // profile or block unrelated sync because its historical ledger cannot be seeded.
            guard let record = state["roster"]?["profiles"]?[profile.uuidString], record["deleted"] != .bool(true) else { continue }
            try VortxNativeWatchlist.seed(entries, profileID: profile, into: &candidateHost)
        }
        for edit in hostEdits {
            if let profile = edit.profileID {
                guard let record = state["roster"]?["profiles"]?[profile], record["deleted"] != .bool(true) else { throw VortxNativeError.invalidSnapshot }
            }
            try candidateHost.edit(profileID: edit.profileID, fields: edit.fields, scope: scope)
        }
        let pending = candidateHost.local.websitePending ?? []
        let hasWebsiteWork = !websiteEvents.isEmpty || !pending.isEmpty
        if hasWebsiteWork {
            try VortxNativeProfileEditHost.validateSource(websiteEvents)
            // Preserve differing payloads sharing one ID: the kernel checks immutable identity;
            // a conflict must never replace a previously retained event silently.
            var queue = pending
            for event in websiteEvents where !queue.contains(event) { queue.append(event) }
            var retained: [VortxJSON] = [], conflicts: [VortxNativeProfileEditHost.Conflict] = []
            var receipts = candidateHost.local.websiteReceipts ?? [:]
            for event in queue {
                let eventID = (try? event["eventId"]?.decode(String.self)) ?? "invalid-event"
                let previousEventState = try scope.validateSnapshot(candidate.stateJSON())
                let trial = try VortxNativeRuntime(abi: abi, snapshot: candidate.stateJSON())
                var accepted = false
                defer { if !accepted { trial.close() } }
                do {
                    var authoredEvent = event
                    var requestFields: [String: VortxJSON] = ["type": .string("apply_legacy_profile_edits"),
                        "scope": .string(scope.account), "ownerProfileId": .string(scope.ownerProfileID)]
                    if let aggregate = event["legacyAggregate"] {
                        guard aggregate == legacyProfileEdits, case .object(var fields) = aggregate,
                              let fingerprint = try scope.validateSnapshot(candidate.stateJSON())["nativeSync"]?["legacyImport"]?["fingerprint"] else { throw VortxNativeError.invalidSnapshot }
                        fields["eventId"] = .string(eventID); authoredEvent = .object(fields)
                        requestFields["legacyBootstrapFingerprint"] = fingerprint
                    }
                    requestFields["event"] = authoredEvent
                    let request = VortxJSON.object(requestFields)
                    let response = try trial.dispatch(String(decoding: JSONEncoder().encode(request), as: UTF8.self), now: now)
                    let result = try JSONDecoder().decode(VortxJSON.self, from: Data(response.utf8))
                    guard result["ok"] == .bool(true), let applied = result["events"]?.array?.first,
                          applied["event"] == .string("legacy_profile_edits_applied"), let receipt = applied["receipt"],
                          receipt["eventId"] == .string(eventID), case .string(let fingerprint) = receipt["source"]?["fingerprint"],
                          let patch = applied["hostPatch"] else { throw VortxNativeError.invalidResponse }
                    let admitted = try VortxNativeProfileEditHost.admit(event: authoredEvent, hostPatch: patch,
                        preferences: candidateHost, scope: scope, baseline: websiteBaseline,
                        committedReplay: receipts[eventID] == fingerprint,
                        remoteReplay: previousEventState["nativeSync"]?["legacyProfileEditReceipts"]?[fingerprint] == receipt)
                    if let conflict = admitted.conflict { conflicts.append(conflict); retained.append(event); continue }
                    let next = try scope.validateSnapshot(trial.stateJSON())
                    guard next["nativeSync"]?["legacyProfileEditReceipts"]?[fingerprint] == receipt else { throw VortxNativeError.invalidResponse }
                    candidate.close(); candidate = trial; accepted = true; candidateHost = admitted.preferences
                    receipts[eventID] = fingerprint; results.append(response)
                } catch {
                    retained.append(event)
                    conflicts.append(.init(eventId: eventID, code: "unsupported_or_invalid_event", paths: []))
                }
            }
            candidateHost.local.websitePending = retained
            candidateHost.local.websiteReceipts = receipts
            candidateHost.local.websiteConflicts = conflicts
            state = try scope.validateSnapshot(candidate.stateJSON())
        }
        let addonPending = candidateHost.local.websiteAddonPending ?? []
        let hasAddonWork = !websiteAddonEvents.isEmpty || !addonPending.isEmpty
        if hasAddonWork {
            try VortxNativeWebsiteAddonEdits.validateSource(websiteAddonEvents)
            try VortxNativeWebsiteAddonEdits.validateIncomingIdentities(websiteAddonEvents)
            var queue = addonPending
            for event in websiteAddonEvents where !queue.contains(event) { queue.append(event) }
            try VortxNativeWebsiteAddonEdits.validateSource(queue)
            let collidingIDs = VortxNativeWebsiteAddonEdits.collidingIdentities(queue)
            var retained: [VortxJSON] = []
            var conflicts = candidateHost.local.websiteAddonConflicts ?? []
            var receipts = candidateHost.local.websiteAddonReceipts ?? [:]
            for event in queue {
                let eventID = (try? event["eventId"]?.decode(String.self)) ?? "invalid-event"
                if collidingIDs.contains(eventID) {
                    retained.append(event)
                    let conflict = VortxNativeWebsiteAddonEdits.Conflict(eventId: eventID, code: "conflicting_addon_event_id", paths: [])
                    if !conflicts.contains(conflict) { conflicts.append(conflict) }
                    continue
                }
                let trial = try VortxNativeRuntime(abi: abi, snapshot: candidate.stateJSON())
                var accepted = false
                defer { if !accepted { trial.close() } }
                do {
                    let request = VortxNativeWebsiteAddonEdits.request(event: event, scope: scope)
                    let response = try trial.dispatch(String(decoding: JSONEncoder().encode(request), as: UTF8.self), now: now)
                    let result = try JSONDecoder().decode(VortxJSON.self, from: Data(response.utf8))
                    let next = try scope.validateSnapshot(trial.stateJSON())
                    let receipt = try VortxNativeWebsiteAddonEdits.validatedReceipt(event: event, result: result, state: next, scope: scope)
                    if let conflict = try VortxNativeWebsiteAddonEdits.conflict(event: event, receipt: receipt, state: next, runtime: trial) {
                        if !conflicts.contains(conflict) { conflicts.append(conflict) }
                    }
                    // Even a losing event needs its native receipt durably retained. Its replay
                    // cannot acquire a new clock or undo a later native uninstall.
                    candidate.close(); candidate = trial; accepted = true
                    receipts[receipt.id] = receipt.fingerprint; results.append(response)
                } catch {
                    retained.append(event)
                    let conflict = VortxNativeWebsiteAddonEdits.Conflict(eventId: eventID, code: "unsupported_or_invalid_addon_event", paths: [])
                    if !conflicts.contains(conflict) { conflicts.append(conflict) }
                }
            }
            candidateHost.local.websiteAddonPending = retained
            candidateHost.local.websiteAddonReceipts = receipts
            candidateHost.local.websiteAddonConflicts = conflicts
            state = try scope.validateSnapshot(candidate.stateJSON())
        }
        let updated = try candidate.stateJSON()
        let hasHostChanges = hostRemote != nil || !hostEdits.isEmpty || !legacyWatchlists.isEmpty || hasWebsiteWork || hasAddonWork || authenticatedSourceArchive != nil
        let encodedHost = hasHostChanges ? try candidateHost.encoded() : nil
        let uptime = ProcessInfo.processInfo.systemUptime
        let progressOnly = !hasHostChanges && legacyMaterial == nil && sourceAuthority == nil && !actions.isEmpty
            && actions.allSatisfy(Self.isProgressAction)
        let holdBack = progressOnly && uptime >= writeProgressUntil
            && lastDurableCommit.map { uptime - $0 < Self.progressCommitInterval } == true
        if holdBack {
            pendingProgressCommit = true
        } else {
            do {
                try lease.withActive {
                    func commit() throws {
                        if let encodedHost { try store.commit(updated, scope: scope, hostPreferences: encodedHost) }
                        else { try store.commit(updated, scope: scope) }
                    }
                    if let sourceAuthority { try sourceAuthority.withActive(commit) } else { try commit() }
                }
            }
            catch VortxNativeError.closed { throw VortxNativeError.closed }
            catch VortxNativeError.superseded { throw VortxNativeError.superseded }
            catch {
                // A rename may have succeeded before a readback/fsync failure. Do not overwrite that
                // uncertain checkpoint with another transaction; reopen and validate it first.
                VortxCheckpointDiagnostics.report(stage: .transactionCommit, error: error)
                persistenceFailed = true; throw VortxNativeError.checkpointUncertain
            }
            pendingProgressCommit = false
            lastDurableCommit = uptime
            if progressOnly { writeProgressUntil = 0 }
        }
        let previous = runtime; runtime = candidate; hostPreferences = candidateHost; installed = true; previous.close()
        if try scope.validateSnapshot(old)["activeProfileId"] != state["activeProfileId"] { invalidateScreens() }
        return results
    }
    /// Write a position the 30 s coalescing held back, and write the next position report at once too (it may
    /// still be on its way from the player). Failure is handled exactly like a failed commit in dispatch.
    func flushPendingProgress() throws {
        guard !closed else { throw VortxNativeError.closed }
        writeProgressUntil = ProcessInfo.processInfo.systemUptime + 10
        guard pendingProgressCommit else { return }
        guard !persistenceFailed else { throw VortxNativeError.checkpointUncertain }
        let current = try stateJSON()
        do { try lease.withActive { try store.commit(current, scope: scope) } }
        catch VortxNativeError.closed { throw VortxNativeError.closed }
        catch {
            VortxCheckpointDiagnostics.report(stage: .transactionCommit, error: error)
            persistenceFailed = true; throw VortxNativeError.checkpointUncertain
        }
        pendingProgressCommit = false
        lastDurableCommit = ProcessInfo.processInfo.systemUptime
    }
    func screen(_ name: String) -> VortxNativeScreenState? { screens[name] }
    private func begin(_ name: String, expectedProfileID: String? = nil) throws -> (VortxResourceBridge, UUID, UUID, String) {
        try Task.checkCancellation()
        guard case .string(let profile) = try scope.validateSnapshot(stateJSON())["activeProfileId"] else { throw VortxNativeError.invalidSnapshot }
        guard expectedProfileID == nil || expectedProfileID == profile else { throw VortxNativeError.superseded }
        resourceBatchBridges.removeValue(forKey: name)?.forEach { $0.invalidate() }
        let ticket = UUID(); tickets[name] = ticket; screens[name] = .loading
        let bridge = bridges[name] ?? VortxResourceBridge(transport: transport); bridges[name] = bridge
        try lease.withActive {}; return (bridge, ticket, epoch, profile)
    }
    private func current(_ name: String, _ ticket: UUID, _ capturedEpoch: UUID) -> Bool {
        !closed && epoch == capturedEpoch && tickets[name] == ticket && (try? lease.withActive { true }) == true
    }
    /// One screen operation owns the entire range. Individual bridges cannot supersede another
    /// catalog; the shared ticket/epoch/lease still retire every leg at an owner or query boundary.
    func loadCatalogs(_ screen: CatalogScreen, catalogs: [(VortxResourceAddon, VortxResourceRequest)],
                      selection: VortxJSON, range: ClosedRange<Int>, previous: VortxJSON?,
                      expectedProfileID: String,
                      onUpdate: @escaping @Sendable (VortxJSON) -> Void) async throws -> VortxJSON {
        guard catalogs.allSatisfy({ $0.1.resource == .catalog }) else { throw VortxNativeError.invalidResponse }
        let name = screen.rawValue
        let (priorBridge, ticket, capturedEpoch, profile) = try begin(name, expectedProfileID: expectedProfileID)
        priorBridge.invalidate()
        // A facade may have captured its rows just before this actor processed a profile change.
        // Reuse only this epoch's own plan, never presentation rows from a retired owner.
        let previousRows = previous == catalogPlans[name] && previous?["selected"] == selection ? previous?["catalogs"]?.array ?? [] : []
        var rows = try catalogs.enumerated().map { index, item -> VortxJSON in
            let request: VortxJSON = .object(["base": .string(item.0.transportUrl), "path": try VortxResourceProjection.path(item.1)])
            if index < previousRows.count, let first = previousRows[index].array?.first,
               first["request"] == request, [VortxJSON.string("Ready"), .string("Err")].contains(first["content"]?["type"] ?? .null) {
                return previousRows[index]
            }
            return .array([.object(["request": request, "content": range.contains(index) ? .object(["type": .string("Loading")]) : .null])])
        }
        // Preserve only pages still present in this exact plan (including already loaded skips).
        pages[name] = (pages[name] ?? []).filter { page in
            page.ownerID == profile && rows.contains { row in
                row.array?.contains { entry in
                    entry["request"]?["path"] == (try? VortxResourceProjection.path(page.request)) &&
                    page.sourceURLs.values.contains { entry["request"]?["base"] == .string($0) } &&
                    [VortxJSON.string("Ready"), .string("Err")].contains(entry["content"]?["type"] ?? .null)
                } == true
            }
        }
        var registry: [VortxResourceAddon] = []
        for (addon, _) in catalogs {
            if let existing = registry.first(where: { $0.id == addon.id }) {
                guard existing.transportUrl == addon.transportUrl else { throw VortxNativeError.invalidResponse }
            } else { registry.append(addon) }
        }
        catalogRegistries[name] = registry
        let pending = rows.indices.filter { range.contains($0) && rows[$0].array?.first?["content"]?["type"] == .string("Loading") }
        let workers = pending.map { _ in VortxResourceBridge(transport: transport) }
        resourceBatchBridges[name] = workers
        defer {
            workers.forEach { $0.invalidate() }
            if tickets[name] == ticket { resourceBatchBridges.removeValue(forKey: name) }
        }
        func publish() throws -> VortxJSON {
            try Task.checkCancellation()
            guard current(name, ticket, capturedEpoch) else { throw VortxNativeError.superseded }
            let board: VortxJSON = .object(["selected": selection, "catalogs": .array(rows)])
            catalogPlans[name] = board; screens[name] = .ready(board); onUpdate(board)
            return board
        }
        _ = try publish()
        return try await withThrowingTaskGroup(of: (Int, VortxResourceSnapshot?).self) { group in
            func schedule(_ offset: Int) {
                let item = catalogs[pending[offset]], bridge = workers[offset]
                group.addTask {
                    do { return (offset, try await bridge.load(ownerID: profile, request: item.1, addons: [item.0])) }
                    catch { return (offset, nil) }
                }
            }
            var scheduled = min(4, pending.count)
            for offset in 0..<scheduled { schedule(offset) }
            while let (offset, result) = try await group.next() {
                try Task.checkCancellation()
                guard current(name, ticket, capturedEpoch) else { throw VortxNativeError.superseded }
                let index = pending[offset], item = catalogs[index]
                let snapshot: VortxResourceSnapshot
                if let result {
                    guard workers[offset].accepts(result) else { throw VortxNativeError.superseded }
                    snapshot = result
                } else {
                    snapshot = .init(ownerID: profile, requestID: UUID().uuidString, generation: 0, request: item.1,
                        groups: [], sourceURLs: [item.0.id: item.0.transportUrl])
                }
                let failure = VortxResourceGroup(addonId: item.0.id, status: .error, content: nil, error: .init(code: "native_catalog_failed"))
                let content = snapshot.groups.first ?? failure
                let accepted = VortxResourceSnapshot(ownerID: snapshot.ownerID, requestID: snapshot.requestID,
                    generation: snapshot.generation, request: snapshot.request, groups: [content], sourceURLs: snapshot.sourceURLs)
                pages[name, default: []].append(accepted)
                rows[index] = .array([try VortxResourceProjection.entry(content, request: item.1, registry: registry)])
                _ = try publish()
                if scheduled < pending.count { schedule(scheduled); scheduled += 1 }
            }
            return .object(["selected": selection, "catalogs": .array(rows)])
        }
    }
    func loadCatalog(_ screen: CatalogScreen, request: VortxResourceRequest, addons: [VortxResourceAddon], append: Bool = false) async throws -> VortxJSON {
        guard request.resource == .catalog else { throw VortxNativeError.invalidResponse }
        let name = screen.rawValue
        let (bridge, ticket, capturedEpoch, profile) = try begin(name)
        do {
            let result: VortxResourceSnapshot
            do {
                let received = try await bridge.load(ownerID: profile, request: request, addons: addons)
                guard current(name, ticket, capturedEpoch), bridge.accepts(received) else { throw VortxNativeError.superseded }
                result = received
            } catch {
                try Task.checkCancellation()
                guard current(name, ticket, capturedEpoch) else { throw VortxNativeError.superseded }
                if case VortxNativeError.superseded = error { throw error }
                guard append else { throw error }
                // A failed page settles only its requested catalog. Keep prior pages and peers;
                // never replace the entire board with a generic operation-error envelope.
                let category = error is VortxNativeError ? VortxNativeError.diagnosticCode(error) : "transport_failed"
                NSLog("[VortXNative] resource=catalog result=failed category=%@", category)
                result = .init(ownerID: profile, requestID: UUID().uuidString, generation: 0, request: request,
                    groups: addons.map { .init(addonId: $0.id, status: .error, content: nil, error: .init(code: "native_catalog_failed")) },
                    sourceURLs: Dictionary(addons.map { ($0.id, $0.transportUrl) }, uniquingKeysWith: { first, _ in first }))
            }
            var accepted = append ? pages[name] ?? [] : []
            // A cancelled host publication can leave its already accepted page in this actor.
            // Retrying the identical source/path replaces that page instead of duplicating it.
            if let index = accepted.firstIndex(where: { $0.request == result.request && $0.sourceURLs == result.sourceURLs }) {
                accepted[index] = result
            } else { accepted.append(result) }
            var registry = append ? catalogRegistries[name] ?? [] : []
            for addon in addons {
                if let previous = registry.first(where: { $0.id == addon.id }) {
                    guard previous.transportUrl == addon.transportUrl else { throw VortxNativeError.invalidResponse }
                } else { registry.append(addon) }
            }
            var projection = try VortxResourceProjection.board(pages: accepted, registry: registry)
            if append, let plan = catalogPlans[name], var rows = plan["catalogs"]?.array {
                // Pagination retains the full planned order and the selected query, even when a
                // different catalog completed first or an unrequested row still has nil content.
                for row in projection["catalogs"]?.array ?? [] {
                    guard let request = row.array?.first?["request"], let index = rows.firstIndex(where: {
                        let existing = $0.array?.first?["request"]
                        return existing?["base"] == request["base"] && existing?["path"]?["id"] == request["path"]?["id"] && existing?["path"]?["type"] == request["path"]?["type"]
                    }) else { continue }
                    rows[index] = row
                }
                projection = .object(["selected": plan["selected"] ?? .null, "catalogs": .array(rows)])
                catalogPlans[name] = projection
            } else { catalogPlans.removeValue(forKey: name) }
            catalogRegistries[name] = registry; pages[name] = accepted; screens[name] = .ready(projection); return projection
        } catch {
            if current(name, ticket, capturedEpoch) { screens[name] = .failed }; throw error
        }
    }
    func loadMeta(request: VortxResourceRequest, stream: VortxResourceRequest?, addons: [VortxResourceAddon],
                  expectedProfileID: String? = nil,
                  onUpdate: @escaping @Sendable (VortxJSON) -> Void = { _ in }) async throws -> VortxJSON {
        guard request.resource == .meta, stream == nil || (stream?.resource == .stream && stream?.type == request.type),
              addons.count <= 128, Set(addons.map(\.id)).count == addons.count else { throw VortxNativeError.invalidResponse }
        let name = "meta_details"
        let (priorBridge, ticket, capturedEpoch, profile) = try begin(name, expectedProfileID: expectedProfileID)
        priorBridge.invalidate()
        // A native multi-addon call spends its deadline waiting for its internal slots. Schedule
        // singleton calls here instead: each admitted leg receives its own 20s network budget.
        // Separate two-meta/four-stream windows prevent slow metadata from starving streams.
        // Total screen time is multiple bounded waves, not a new 20s all-provider deadline.
        let jobs = addons.indices.flatMap { index -> [(Int, VortxResourceRequest)] in
            [(index, request)] + (stream.map { [(index, $0)] } ?? [])
        }
        let workers = jobs.map { _ in VortxResourceBridge(transport: transport) }
        resourceBatchBridges[name] = workers
        defer {
            workers.forEach { $0.invalidate() }
            if tickets[name] == ticket { resourceBatchBridges.removeValue(forKey: name) }
        }
        var metas = [VortxResourceGroup?](repeating: nil, count: addons.count)
        var streams = [VortxResourceGroup?](repeating: nil, count: stream == nil ? 0 : addons.count)
        var completed = 0
        var metaContentBudget = VortxResourceContentBudget(), streamContentBudget = VortxResourceContentBudget()
        let sourceURLs = Dictionary(uniqueKeysWithValues: addons.map { ($0.id, $0.transportUrl) })
        func snapshot(_ path: VortxResourceRequest, _ groups: [VortxResourceGroup?]) -> VortxResourceSnapshot {
            .init(ownerID: profile, requestID: ticket.uuidString, generation: 0, request: path,
                  groups: groups.compactMap { $0 }, sourceURLs: sourceURLs)
        }
        func rows(_ path: VortxResourceRequest, _ groups: [VortxResourceGroup?]) throws -> [VortxJSON] {
            try groups.enumerated().map { index, group in
                if let group { return try VortxResourceProjection.entry(group, request: path, registry: addons) }
                return .object(["request": .object(["base": .string(addons[index].transportUrl), "path": try VortxResourceProjection.path(path)]),
                                "content": .object(["type": .string("Loading")])])
            }
        }
        func publish(logStreams: Bool = false, settled: Bool = false) throws -> VortxJSON {
            try Task.checkCancellation()
            guard current(name, ticket, capturedEpoch) else { throw VortxNativeError.superseded }
            let projection = try VortxResourceProjection.metaDetails(meta: snapshot(request, metas),
                streams: stream.map { snapshot($0, streams) }, expectedStream: stream, registry: addons)
            let value: VortxJSON = .object(["selected": projection["selected"] ?? .null,
                "metaItems": .array(try rows(request, metas)), "streams": .array(try stream.map { try rows($0, streams) } ?? []),
                "metaStreams": projection["metaStreams"] ?? .array([])])
            screens[name] = completed == 0 && !jobs.isEmpty ? .loading : .ready(value)
            onUpdate(value)
            if logStreams, stream != nil {
                var ready = 0, empty = 0, failed = 0
                for case let group? in streams {
                    if group.status != .ready { failed += 1 }
                    else if (try? group.items(for: .stream).isEmpty) == true { empty += 1 }
                    else { ready += 1 }
                }
                NSLog("[VortXNative] resource=stream result=%@ ready=%d empty=%d error=%d loading=%d", settled ? "settled" : "partial", ready, empty, failed, streams.count - ready - empty - failed)
            }
            return value
        }
        _ = try publish(logStreams: true)
        return try await withThrowingTaskGroup(of: (Int, Result<VortxResourceSnapshot, VortxResourceGroup.Failure>).self) { group in
            func schedule(_ offset: Int) {
                let job = jobs[offset], bridge = workers[offset], addon = addons[job.0]
                group.addTask {
                    do { return (offset, .success(try await bridge.load(ownerID: profile, request: job.1, addons: [addon], budgetMs: 20_000,
                        maxResponseBytes: 8_388_608, maxTotalResponseBytes: 33_554_432))) }
                    catch { return (offset, .failure(.from(error))) }
                }
            }
            let metaStride = stream == nil ? 1 : 2
            var scheduledMeta = min(2, addons.count), scheduledStreams = stream == nil ? 0 : min(4, addons.count)
            for index in 0..<scheduledMeta { schedule(index * metaStride) }
            for index in 0..<scheduledStreams { schedule(index * 2 + 1) }
            while let (offset, result) = try await group.next() {
                try Task.checkCancellation()
                guard current(name, ticket, capturedEpoch) else { throw VortxNativeError.superseded }
                let job = jobs[offset], addon = addons[job.0]
                var value: VortxResourceGroup
                switch result {
                case .success(let result):
                    guard workers[offset].accepts(result) else { throw VortxNativeError.superseded }
                    // Unsupported/empty singleton responses are terminal, not still-loading rows.
                    value = result.groups.first ?? .init(addonId: addon.id, status: .ready,
                        content: .object(job.1.resource == .meta ? ["meta": .null] : ["streams": .array([])]), error: nil)
                case .failure(let failure):
                    value = .failed(addonId: addon.id, failure: failure)
                }
                if value.status == .ready {
                    do {
                        // Validate embedded streams before they enter the combined projection;
                        // malformed metadata from one provider must not discard valid peers.
                        if job.1.resource == .meta {
                            _ = try VortxResourceProjection.metaDetails(meta: snapshot(request, [value]), streams: nil,
                                expectedStream: stream, registry: addons)
                        }
                        // Retain the incumbent32MiB normalized-content cap separately for meta
                        // and stream. This is not a consumed-wire-byte measure: the ABI does not
                        // report raw bytes for failed or whitespace-heavy responses.
                        let fits = try job.1.resource == .meta ? metaContentBudget.claim(value.content ?? .null) : streamContentBudget.claim(value.content ?? .null)
                        if !fits {
                            value = .init(addonId: addon.id, status: .error, content: nil, error: .init(code: "body_too_large"))
                        }
                    } catch {
                        value = .init(addonId: addon.id, status: .error, content: nil, error: .init(code: "invalid_response"))
                    }
                }
                if job.1.resource == .meta { metas[job.0] = value } else { streams[job.0] = value }
                completed += 1
                _ = try publish(logStreams: job.1.resource == .stream)
                // Publish/log the accepted terminal leg once, not every snapshot of prior legs.
                // No await separates these ownership checks from submitting the redacted receipt.
                try Task.checkCancellation()
                guard current(name, ticket, capturedEpoch) else { throw VortxNativeError.superseded }
                VXProbe.log("native-addon", value.terminalDiagnostic(requestID: ticket,
                    request: job.1, sourceOrdinal: job.0 + 1))
                if job.1.resource == .meta, scheduledMeta < addons.count {
                    schedule(scheduledMeta * metaStride); scheduledMeta += 1
                } else if job.1.resource == .stream, scheduledStreams < addons.count {
                    schedule(scheduledStreams * 2 + 1); scheduledStreams += 1
                }
            }
            return try publish(logStreams: true, settled: true)
        }
    }
    /// Independent, non-UI metadata lookup for a deliberate library add. It cannot replace the
    /// detail screen or cancel another title's add; account/profile/registry invalidation still wins.
    func libraryMetadata(id: String, type: String, profileID: String, addons: [VortxResourceAddon]) async throws -> VortxJSON {
        let name = "library_lookup:" + UUID().uuidString
        let (bridge, ticket, capturedEpoch, profile) = try begin(name)
        defer { bridge.invalidate(); bridges.removeValue(forKey: name); tickets.removeValue(forKey: name); screens.removeValue(forKey: name) }
        guard profile == profileID else { throw VortxNativeError.superseded }
        let result = try await bridge.load(ownerID: profile, request: .init(resource: .meta, type: type, id: id), addons: addons)
        guard current(name, ticket, capturedEpoch), bridge.accepts(result) else { throw VortxNativeError.superseded }
        for addon in addons {
            guard let group = result.groups.first(where: { $0.addonId == addon.id }), group.status == .ready,
                  let meta = group.content?["meta"], meta["id"] == .string(id), meta["type"] == .string(type),
                  case .string(let title) = meta["name"], !title.isEmpty else { continue }
            return meta
        }
        throw VortxNativeError.invalidResponse
    }
    func loadSubtitles(request: VortxResourceRequest, addons: [VortxResourceAddon]) async throws -> VortxJSON {
        guard request.resource == .subtitles else { throw VortxNativeError.invalidResponse }
        let name = "subtitles"
        let (bridge, ticket, capturedEpoch, profile) = try begin(name)
        do {
            let result = try await bridge.load(ownerID: profile, request: request, addons: addons)
            guard current(name, ticket, capturedEpoch), bridge.accepts(result) else { throw VortxNativeError.superseded }
            let projection = try VortxResourceProjection.subtitles(result, registry: addons)
            screens[name] = .ready(projection); return projection
        } catch {
            if current(name, ticket, capturedEpoch) { screens[name] = .failed }; throw error
        }
    }
}
