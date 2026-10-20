import Combine
import Foundation

/// The active profile's For You interests and smart collections.
///
/// Signed in, the source is the account's native state: `forYou.` host profile fields, written through
/// the same profile/account fence as the watchlist and synced to every device. Signed out, a local
/// per-profile copy stands in (there is no account to sync to). `ready` is the gate for asking: the
/// interests picker is only offered once this profile's saved answer is known, so a device that has
/// not yet received another device's answer never asks a second time.
@MainActor
final class ForYouStore: ObservableObject {
    static let shared = ForYouStore()
    static let changedNote = Notification.Name("vortx.forYouChanged")

    @Published private(set) var profileID: UUID?
    @Published private(set) var interests: ForYouInterests?
    @Published private(set) var collections: [SmartCollection] = []
    @Published private(set) var ready = false
    @Published private(set) var richCollections: [CurationCollection] = []
    private var richEditSession: CurationEditSession?
    private var legacyEditSession: CurationLegacyEditSession?
    private var richCredentialCapture = CredentialScopeRegistry.shared.capture()
    #if VORTX_NATIVE_DATA_ENGINE
    private var richPresentationTarget: CoreBridge.NativeXperienceTarget?
    #endif

    private var subscriptions: Set<AnyCancellable> = []

    private init() {
        let center = NotificationCenter.default
        // Identity changes retire the original gesture synchronously, including A -> B -> A.
        ProfileStore.shared.$activeID.dropFirst()
            .sink { [weak self] _ in self?.invalidateCurationContext() }.store(in: &subscriptions)
        VortXSyncManager.shared.$isSignedIn.dropFirst()
            .sink { [weak self] _ in self?.invalidateCurationContext() }.store(in: &subscriptions)
        var contextNotes = [VortXSyncManager.credentialScopeDidChangeNote]
        #if VORTX_NATIVE_DATA_ENGINE
        contextNotes.append(CoreBridge.xperienceContextChangedNote)
        #endif
        for name in contextNotes {
            // Native notifications may originate off-main after releasing their source locks.
            // Do not receive(on:) and leave the previous owner's data visible for another turn.
            center.publisher(for: name).sink { @Sendable [weak self] _ in
                if Thread.isMainThread {
                    MainActor.assumeIsolated { self?.checkCurationContext() }
                } else {
                    DispatchQueue.main.sync { self?.checkCurationContext() }
                }
            }.store(in: &subscriptions)
        }
        // Remote host changes arrive through the native profile refresh, which posts the watchlist note.
        for name in [Self.changedNote, LibraryAutoAdd.watchlistChangedNote] {
            center.publisher(for: name).receive(on: RunLoop.main)
                .sink { [weak self] _ in self?.reload() }.store(in: &subscriptions)
        }
        ProfileStore.shared.$activeID.removeDuplicates().receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.reload() }.store(in: &subscriptions)
        VortXSyncManager.shared.$isSignedIn.removeDuplicates().receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.reload() }.store(in: &subscriptions)
    }

    var homeCollections: [SmartCollection] { collections.filter(\.home) }

    func reload() {
        let active = ProfileStore.shared.activeID
        guard VortXSyncManager.shared.isSignedIn else {
            let local = ForYouLocalStore.read(profileID: active)
            publish(profileID: active, interests: local.interests, collections: local.collections, ready: active != nil)
            return
        }
        #if VORTX_NATIVE_DATA_ENGINE
        if let native = try? CoreBridge.shared.nativeForYou(), native.profileID == active {
            publish(profileID: native.profileID, interests: native.interests, collections: native.collections, ready: true)
        } else {
            publish(profileID: active, interests: nil, collections: [], ready: false)
        }
        #else
        let local = ForYouLocalStore.read(profileID: active)
        publish(profileID: active, interests: local.interests, collections: local.collections, ready: active != nil)
        #endif
    }

    func save(_ interests: ForYouInterests) async throws {
        try await write(ForYouInterests.field, value: ForYouSync.value(interests))
    }

    func save(_ collection: SmartCollection) async throws {
        try await write(collection.field, value: ForYouSync.value(collection))
    }

    func delete(_ collection: SmartCollection) async throws {
        try await write(collection.field, value: .null)
    }

    private func write(_ field: String, value: VortxJSON) async throws {
        guard let profile = ProfileStore.shared.activeID else { throw VortxNativeError.closed }
        try ForYouSync.validate(field, value: value)
        #if VORTX_NATIVE_DATA_ENGINE
        if VortXSyncManager.shared.isSignedIn {
            let core = CoreBridge.shared
            let admission = core.captureNativeWatchlistActionAdmission()
            guard let target = await core.prepareNativeWatchlistActionTarget(admission),
                  ProfileStore.shared.activeID == profile else { throw VortxNativeError.superseded }
            try await core.setNativeForYouField(field, value: value, target: target)
            reload()
            return
        }
        #endif
        ForYouLocalStore.write(field: field, value: value, profileID: profile)
        NotificationCenter.default.post(name: Self.changedNote, object: nil)
    }

    func captureRichEdit(_ collection: SmartCollection) throws -> CurationEditSession {
        invalidateRichEdit(clearPresentation: false)
        guard let profile = ProfileStore.shared.activeID, ready, profile == profileID,
              collections.contains(where: { $0.id == collection.id }) else { throw CurationFailure.unavailable }
        let registers: [String: VortxJSON]
        #if VORTX_NATIVE_DATA_ENGINE
        let target: CoreBridge.NativeXperienceTarget?
        if VortXSyncManager.shared.isSignedIn {
            target = try CoreBridge.shared.captureNativeXperienceTarget()
            do { registers = try CoreBridge.shared.nativeCurationRegisters(target: target!) }
            catch { CoreBridge.shared.retireNativeXperienceTarget(target!); throw error }
        } else {
            target = nil; registers = ForYouLocalStore.registers(profileID: profile)
        }
        #else
        guard !VortXSyncManager.shared.isSignedIn else { throw CurationFailure.unavailable }
        registers = ForYouLocalStore.registers(profileID: profile)
        #endif
        guard let legacyRegister = registers[collection.field], let legacy = legacyRegister["value"],
              let current = try? legacy.decode(SmartCollection.self), current == collection else {
            #if VORTX_NATIVE_DATA_ENGINE
            if let target { CoreBridge.shared.retireNativeXperienceTarget(target) }
            #endif
            throw CurationFailure.staleCollection
        }
        let companion = registers[CurationDocument.prefix + collection.id]
        let document: CurationDocument
        do {
            if let value = companion?["value"], value != .null {
                document = try CurationDocument(raw: value)
                guard document.matches(collection),
                      VortxNativeHostFieldEvidence.json(value["legacyCreatedAt"], legacy["createdAt"]) else {
                    throw CurationFailure.staleCollection
                }
            } else { document = try CurationDocument.make(for: collection, legacyValue: legacy) }
        } catch {
            #if VORTX_NATIVE_DATA_ENGINE
            if let target { CoreBridge.shared.retireNativeXperienceTarget(target) }
            #endif
            throw error
        }
        #if VORTX_NATIVE_DATA_ENGINE
        let session = CurationEditSession(profileID: profile, collection: collection, document: document,
            legacyRegister: legacyRegister, companionRegister: companion, target: target)
        #else
        let session = CurationEditSession(profileID: profile, collection: collection, document: document,
            legacyRegister: legacyRegister, companionRegister: companion)
        #endif
        richEditSession = session
        return session
    }
    func retireRichEdit(_ session: CurationEditSession) {
        if richEditSession?.id == session.id { invalidateRichEdit(clearPresentation: false) }
    }
    func isCurrentRichEdit(_ session: CurationEditSession) -> Bool {
        guard richEditSession?.id == session.id, ProfileStore.shared.activeID == session.profileID else { return false }
        #if VORTX_NATIVE_DATA_ENGINE
        if let target = session.target { return CoreBridge.shared.nativeXperienceTargetIsCurrent(target) }
        #endif
        return !VortXSyncManager.shared.isSignedIn
    }
    private func invalidateRichEdit(clearPresentation: Bool = true) {
        #if VORTX_NATIVE_DATA_ENGINE
        if let target = richEditSession?.target { CoreBridge.shared.retireNativeXperienceTarget(target) }
        #endif
        richEditSession = nil
        // Immediate privacy boundary; the next ordinary reload republishes the active profile.
        if clearPresentation { richCollections = [] }
    }
    func saveRich(_ document: CurationDocument, session: CurationEditSession) async throws {
        try Task.checkCancellation()
        guard richEditSession?.id == session.id, ProfileStore.shared.activeID == session.profileID,
              document.id == session.collection.id, document.matches(session.collection) else {
            throw CurationFailure.staleContext
        }
        let edit = CurationNativeEdit(document: document, expectedRegister: session.companionRegister,
            legacyRegister: session.legacyRegister)
        #if VORTX_NATIVE_DATA_ENGINE
        if let target = session.target {
            let core = CoreBridge.shared
            let validator = try await core.prepareNativeXperienceExportBudget(target: target)
            guard richEditSession?.id == session.id else { throw CurationFailure.staleContext }
            let saved = try await core.saveNativeCuration(edit, target: target, validator: validator)
            guard richEditSession?.id == session.id,
                  VortxNativeHostFieldEvidence.json(saved["value"], document.raw) else {
                throw CurationFailure.staleContext
            }
        } else {
            guard !VortXSyncManager.shared.isSignedIn else { throw CurationFailure.staleContext }
            try ForYouLocalStore.writeRich(edit, profileID: session.profileID)
        }
        #else
        guard !VortXSyncManager.shared.isSignedIn else { throw CurationFailure.staleContext }
        try ForYouLocalStore.writeRich(edit, profileID: session.profileID)
        #endif
        retireRichEdit(session)
        reload()
        NotificationCenter.default.post(name: Self.changedNote, object: nil)
    }

    private func invalidateCurationContext() {
        invalidateRichEdit()
        if let session = legacyEditSession { retireLegacyEdit(session) }
        #if VORTX_NATIVE_DATA_ENGINE
        if let target = richPresentationTarget { CoreBridge.shared.retireNativeXperienceTarget(target) }
        richPresentationTarget = nil
        #endif
        richCollections = []
    }

    private func checkCurationContext() {
        let credentialChanged = !CredentialScopeRegistry.shared.isCurrent(richCredentialCapture)
        var changed = credentialChanged
        #if VORTX_NATIVE_DATA_ENGINE
        let core = CoreBridge.shared
        if let target = richPresentationTarget, !core.nativeXperienceTargetIsCurrent(target) { changed = true }
        if let target = richEditSession?.target, !core.nativeXperienceTargetIsCurrent(target) { changed = true }
        if let target = legacyEditSession?.target, !core.nativeXperienceTargetIsCurrent(target) { changed = true }
        #endif
        if changed {
            invalidateCurationContext()
            // Do not recapture an old facade while install/revoke is in progress.
            // Normal acknowledged profile/watchlist publication will call reload.
            return
        }
        // A successful own host publication keeps every original gesture alive. Refresh display
        // only; do not compare it against the pre-save register and report a false stale failure.
        reload()
    }

    func captureLegacyEdit(_ collection: SmartCollection, isNew: Bool = false) throws -> CurationLegacyEditSession {
        if let previous = legacyEditSession { retireLegacyEdit(previous) }
        guard let profile = ProfileStore.shared.activeID, ready, profile == profileID else {
            throw CurationFailure.unavailable
        }
        let registers: [String: VortxJSON]
        #if VORTX_NATIVE_DATA_ENGINE
        let target: CoreBridge.NativeXperienceTarget?
        if VortXSyncManager.shared.isSignedIn {
            target = try CoreBridge.shared.captureNativeXperienceTarget()
            do { registers = try CoreBridge.shared.nativeCurationRegisters(target: target!) }
            catch { CoreBridge.shared.retireNativeXperienceTarget(target!); throw error }
        } else { target = nil; registers = ForYouLocalStore.registers(profileID: profile) }
        #else
        guard !VortXSyncManager.shared.isSignedIn else { throw CurationFailure.unavailable }
        registers = ForYouLocalStore.registers(profileID: profile)
        #endif
        let expected = registers[collection.field]
        let valid: Bool
        if isNew { valid = expected == nil }
        else { valid = (try? expected?["value"]?.decode(SmartCollection.self)) == collection }
        guard valid else {
            #if VORTX_NATIVE_DATA_ENGINE
            if let target { CoreBridge.shared.retireNativeXperienceTarget(target) }
            #endif
            throw CurationFailure.staleCollection
        }
        #if VORTX_NATIVE_DATA_ENGINE
        let session = CurationLegacyEditSession(profileID: profile, collection: collection, isNew: isNew,
            expectedRegister: expected, target: target)
        #else
        let session = CurationLegacyEditSession(profileID: profile, collection: collection, isNew: isNew,
            expectedRegister: expected)
        #endif
        legacyEditSession = session
        return session
    }
    func isCurrentLegacyEdit(_ session: CurationLegacyEditSession) -> Bool {
        guard legacyEditSession?.id == session.id, ProfileStore.shared.activeID == session.profileID else { return false }
        #if VORTX_NATIVE_DATA_ENGINE
        if let target = session.target { return CoreBridge.shared.nativeXperienceTargetIsCurrent(target) }
        #endif
        return !VortXSyncManager.shared.isSignedIn
    }
    func retireLegacyEdit(_ session: CurationLegacyEditSession) {
        guard legacyEditSession?.id == session.id else { return }
        #if VORTX_NATIVE_DATA_ENGINE
        if let target = session.target { CoreBridge.shared.retireNativeXperienceTarget(target) }
        #endif
        legacyEditSession = nil
    }
    func saveLegacy(_ collection: SmartCollection, session: CurationLegacyEditSession) async throws {
        let edit = try CurationLegacyEdit.update(collection, original: session.collection,
            expectedRegister: session.expectedRegister)
        try await commitLegacy(edit, session: session)
    }
    func deleteLegacy(_ session: CurationLegacyEditSession) async throws {
        guard !session.isNew else { throw CurationFailure.invalidEdit }
        let edit = try CurationLegacyEdit.deletion(session.collection, expectedRegister: session.expectedRegister)
        try await commitLegacy(edit, session: session)
    }
    private func commitLegacy(_ edit: VortxNativeHostFieldEdit, session: CurationLegacyEditSession) async throws {
        try Task.checkCancellation()
        guard isCurrentLegacyEdit(session), edit.field == session.collection.field else { throw CurationFailure.staleContext }
        #if VORTX_NATIVE_DATA_ENGINE
        if let target = session.target {
            let core = CoreBridge.shared
            let budget = try await core.prepareNativeXperienceExportBudget(target: target)
            guard isCurrentLegacyEdit(session) else { throw CurationFailure.staleContext }
            let saved = try await core.saveNativeLegacyCuration(edit, target: target, validator: budget)
            guard isCurrentLegacyEdit(session),
                  VortxNativeHostFieldEvidence.json(saved["value"], edit.value) else { throw CurationFailure.staleContext }
        } else {
            guard !VortXSyncManager.shared.isSignedIn else { throw CurationFailure.staleContext }
            try ForYouLocalStore.writeLegacy(edit, profileID: session.profileID)
        }
        #else
        guard !VortXSyncManager.shared.isSignedIn else { throw CurationFailure.staleContext }
        try ForYouLocalStore.writeLegacy(edit, profileID: session.profileID)
        #endif
        retireLegacyEdit(session)
        reload()
        NotificationCenter.default.post(name: Self.changedNote, object: nil)
    }

    private func publish(profileID: UUID?, interests: ForYouInterests?, collections: [SmartCollection], ready: Bool) {
        if self.profileID != profileID { self.profileID = profileID }
        if self.interests != interests { self.interests = interests }
        if self.collections != collections { self.collections = collections }
        if self.ready != ready { self.ready = ready }
        richCredentialCapture = CredentialScopeRegistry.shared.capture()
        let registers: [String: VortxJSON]
        #if VORTX_NATIVE_DATA_ENGINE
        let core = CoreBridge.shared
        if let old = richPresentationTarget { core.retireNativeXperienceTarget(old) }
        richPresentationTarget = nil
        if VortXSyncManager.shared.isSignedIn {
            guard ready, let target = try? core.captureNativeXperienceTarget() else {
                richCollections = []; return
            }
            do {
                registers = try core.nativeCurationRegisters(target: target)
                richPresentationTarget = target
            } catch {
                core.retireNativeXperienceTarget(target); richCollections = []; return
            }
        } else { registers = ForYouLocalStore.registers(profileID: profileID) }
        #else
        registers = ForYouLocalStore.registers(profileID: profileID)
        #endif
        let rich = collections.map { CurationCollection.legacy($0,
            companion: CurationDocument.read(registers: registers, legacy: $0)) }
        if richCollections != rich { richCollections = rich }
    }
}

/// Signed-out copy: one JSON object of `forYou.` fields per profile, in the same shape as the synced
/// host fields so both paths share `ForYouSync` decoding.
enum ForYouLocalStore {
    static let key = "vortx.forYou.local"

    static func read(profileID: UUID?) -> (interests: ForYouInterests?, collections: [SmartCollection]) {
        guard let profileID, let fields = all()[profileID.uuidString] else { return (nil, []) }
        let host = VortxJSON.object(["profiles": .object([profileID.uuidString: .object([
            "fields": .object(fields.mapValues { .object(["value": $0]) }),
        ])])])
        return ForYouSync.read(host: host, profileID: profileID)
    }

    static func write(field: String, value: VortxJSON, profileID: UUID) {
        var stored = all()
        var fields = stored[profileID.uuidString] ?? [:]
        if value == .null { fields.removeValue(forKey: field) } else { fields[field] = value }
        stored[profileID.uuidString] = fields
        if let data = try? JSONEncoder().encode(stored) { UserDefaults.standard.set(data, forKey: key) }
    }

    static func registers(profileID: UUID?) -> [String: VortxJSON] {
        guard let profileID else { return [:] }
        return (all()[profileID.uuidString] ?? [:]).mapValues { .object(["value": $0]) }
    }
    @MainActor static func writeRich(_ edit: CurationNativeEdit, profileID: UUID) throws {
        let current = registers(profileID: profileID)
        guard edit.nativeEdit.baselineMatches(current[edit.document.field]) else {
            throw VortxNativeHostFieldMutationError.staleRegister
        }
        try edit.validateAdmission(current)
        // Run the same public-host credential exclusion policy before plaintext local storage.
        let bytes = try JSONEncoder().encode(VortxJSON.object([edit.document.field: edit.document.raw]))
        let archived = try VortxNativeBootstrapArchive.encode(document: bytes)
        let parsed = try JSONDecoder().decode(VortxJSON.self, from: archived)
        guard parsed["excludedCredentialPaths"] == .array([]) else { throw CurationFailure.invalidEdit }
        var stored = all()
        var fields = stored[profileID.uuidString] ?? [:]
        fields[edit.document.field] = edit.document.raw
        stored[profileID.uuidString] = fields
        let encoded = try JSONEncoder().encode(stored)
        UserDefaults.standard.set(encoded, forKey: key)
        guard UserDefaults.standard.data(forKey: key) == encoded else { throw CurationFailure.unavailable }
    }

    @MainActor static func writeLegacy(_ edit: VortxNativeHostFieldEdit, profileID: UUID) throws {
        guard ForYouSync.isCollectionField(edit.field),
              edit.baselineMatches(registers(profileID: profileID)[edit.field]) else {
            throw VortxNativeHostFieldMutationError.staleRegister
        }
        try ForYouSync.validate(edit.field, value: edit.value)
        let bytes = try JSONEncoder().encode(VortxJSON.object([edit.field: edit.value]))
        let archive = try VortxNativeBootstrapArchive.encode(document: bytes)
        let parsed = try JSONDecoder().decode(VortxJSON.self, from: archive)
        guard parsed["excludedCredentialPaths"] == .array([]) else { throw CurationFailure.invalidEdit }
        var stored = all()
        var fields = stored[profileID.uuidString] ?? [:]
        // Retain the deletion locally too, so a stale create cannot mistake it for absence.
        fields[edit.field] = edit.value
        stored[profileID.uuidString] = fields
        let encoded = try JSONEncoder().encode(stored)
        UserDefaults.standard.set(encoded, forKey: key)
        guard UserDefaults.standard.data(forKey: key) == encoded else { throw CurationFailure.unavailable }
    }

    private static func all() -> [String: [String: VortxJSON]] {
        guard let data = UserDefaults.standard.data(forKey: key),
              let value = try? JSONDecoder().decode([String: [String: VortxJSON]].self, from: data) else { return [:] }
        return value
    }
}
