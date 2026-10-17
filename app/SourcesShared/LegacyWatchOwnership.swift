import Foundation

/// In-memory authority only. No token, history or cursor is persisted by this fence.
/// A process restart has no surviving debounce task; within a process, a fresh epoch prevents
/// same-token sign-out/rejoin and profile A -> B -> A from reviving an old queued write.
enum LegacyWatchOwnership {
    private final class Epoch: @unchecked Sendable {
        let lock = NSLock()
        var value = UUID()
    }
    private static let epoch = Epoch()
    static let sessionDidChange = Notification.Name("vortx.legacyWatchSessionDidChange")
    static func session() -> UUID { epoch.lock.withLock { epoch.value } }
    static func invalidateSession() {
        epoch.lock.withLock { epoch.value = UUID() }
        // Real lifecycle callers run on the main thread; never notify while holding the lock.
        NotificationCenter.default.post(name: sessionDidChange, object: nil)
    }

    struct ProfileBinding: Equatable, Sendable {
        let id: UUID
        let isOwner: Bool
        let usesOwnAccount: Bool
    }
    /// Status attribution, not a credential or history store. Keep exact bytes in memory only.
    struct NoticeOwner<Owner: Equatable & Sendable>: Equatable, Sendable {
        let owner: Owner
        let session: UUID
        let profile: ProfileBinding
        private let slot: Data
        private let token: Data
        init(owner: Owner, session: UUID, profile: ProfileBinding, slot: String, token: String) {
            self.owner = owner; self.session = session; self.profile = profile
            self.slot = Data(slot.utf8); self.token = Data(token.utf8)
        }
        func matchesCredential(slot: String, token: String) -> Bool {
            self.slot == Data(slot.utf8) && self.token == Data(token.utf8)
        }
        /// Display projection only, never transport or publication admission. Real credential
        /// mutation/auth/profile transitions retire the in-process epoch before mutation.
        func matchesPresentation(owner: Owner, session: UUID, profile: ProfileBinding) -> Bool {
            self.owner == owner && self.session == session && self.profile == profile
        }
    }

    struct Capture<Owner: Equatable & Sendable>: Sendable {
        let owner: Owner
        let session: UUID
        let profileID: UUID
        private let slot: Data
        private let token: Data
        init(owner: Owner, session: UUID, profileID: UUID, slot: String, token: String) {
            self.owner = owner; self.session = session; self.profileID = profileID
            self.slot = Data(slot.utf8); self.token = Data(token.utf8)
        }
        func matches(owner: Owner, session: UUID, profileID: UUID?, usesEngineHistory: Bool,
                     slot: String, token: String?) -> Bool {
            guard self.owner == owner, self.session == session, self.profileID == profileID,
                  !usesEngineHistory, !self.token.isEmpty, self.slot == Data(slot.utf8), let token else { return false }
            return self.token == Data(token.utf8)
        }
    }

    /// Main-actor auth/profile transitions cannot interleave between admission and invoking
    /// the captured transport. An already issued remote request cannot be recalled; a late
    /// result is never authority to publish a notice or acknowledge another session's work.
    @MainActor static func perform<Result: Sendable>(isCurrent: @MainActor () -> Bool,
        operation: @MainActor () async throws -> Result) async throws -> Result {
        try Task.checkCancellation()
        guard isCurrent() else { throw CancellationError() }
        let result = try await operation()
        try Task.checkCancellation()
        guard isCurrent() else { throw CancellationError() }
        return result
    }
}
