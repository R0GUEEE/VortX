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

    private var subscriptions: Set<AnyCancellable> = []

    private init() {
        let center = NotificationCenter.default
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

    private func publish(profileID: UUID?, interests: ForYouInterests?, collections: [SmartCollection], ready: Bool) {
        if self.profileID != profileID { self.profileID = profileID }
        if self.interests != interests { self.interests = interests }
        if self.collections != collections { self.collections = collections }
        if self.ready != ready { self.ready = ready }
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

    private static func all() -> [String: [String: VortxJSON]] {
        guard let data = UserDefaults.standard.data(forKey: key),
              let value = try? JSONDecoder().decode([String: [String: VortxJSON]].self, from: data) else { return [:] }
        return value
    }
}
