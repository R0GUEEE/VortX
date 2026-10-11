import Foundation

/// Foreground catch-up may merge a newer account document without replacing a certified
/// resident session. A failed pull is not permission to retire its resource selections.
enum NativeForegroundSyncPolicy {
    /// Failed checkpoint preparation is not repaired by repeating the same expensive
    /// import every few seconds. This controls attempts, never pending edits or authority.
    struct RecoveryBackoff: Equatable {
        private(set) var failures = 0
        private(set) var retryAt = Date.distantPast

        mutating func recordFailure(at now: Date) {
            failures = min(failures + 1, 4)
            retryAt = now.addingTimeInterval(min(12 * pow(2, Double(failures - 1)), 60))
        }

        func permitsAttempt(at now: Date, userInitiated: Bool) -> Bool {
            userInitiated || now >= retryAt
        }
    }
    static func shouldPullBroadcast(version: Int, lastAcknowledgedVersion: Int, ownerIsCurrent: Bool) -> Bool {
        ownerIsCurrent && version > lastAcknowledgedVersion
    }

    /// Compare only authenticated, successfully joined exported carriers. NativeSync contains
    /// causal provenance, not device-local selectors/rolling counters; do not strip its fields.
    /// Structural values avoid JSON key-order/encoding noise. An equal join must not echo-upload.
    static func requiresCausalRepublish(pulledNative: VortxJSON?, pulledHost: VortxJSON?,
                                        joinedNative: VortxJSON, joinedHost: VortxJSON) -> Bool {
        joinedNative != pulledNative || joinedHost != pulledHost
    }
    /// A transport ACK covers only the edit generation captured before that upload. A failure
    /// or an edit accepted while the request suspends must remain queued for another export.
    ///
    /// Playback reports progress every few seconds, and every upload rebuilds and re-encrypts the
    /// whole account document. Progress-only work therefore waits until playback goes quiet
    /// (pause/exit) or `progressMaxWait` passes. Any other edit keeps the immediate cadence.
    struct PushQueue: Equatable {
        static let urgentDelay: TimeInterval = 2.5
        static let maxFailureBackoff: TimeInterval = 30
        static let progressQuiet: TimeInterval = 15
        static let progressMaxWait: TimeInterval = 60

        private(set) var generation: UInt64 = 0
        private(set) var hasPendingPush = false
        /// Latest generation carrying an edit other than playback progress.
        private var urgentGeneration: UInt64?
        /// First and latest progress reports not yet covered by an accepted upload.
        private var progressSince: Date?
        private var progressLatest: Date?

        var hasUrgentPush: Bool { urgentGeneration != nil }

        mutating func request(progressOnly: Bool = false, at now: Date = Date()) {
            generation &+= 1
            hasPendingPush = true
            if progressOnly {
                if progressSince == nil { progressSince = now }
                progressLatest = now
            } else {
                urgentGeneration = generation
            }
        }

        mutating func acknowledge(_ uploadedGeneration: UInt64, accepted: Bool) {
            guard accepted else { return }
            if let urgent = urgentGeneration, urgent <= uploadedGeneration { urgentGeneration = nil }
            guard uploadedGeneration == generation else {
                // Only progress reported during that upload remains; its window starts there.
                progressSince = progressLatest
                return
            }
            hasPendingPush = false
            progressSince = nil
            progressLatest = nil
        }

        /// Seconds until the next upload attempt, or nil when nothing is pending. A progress
        /// window never shortens the retry delay that follows a failed upload.
        func nextUploadDelay(now: Date, failureBackoff: TimeInterval?) -> TimeInterval? {
            guard hasPendingPush else { return nil }
            guard !hasUrgentPush, let since = progressSince, let latest = progressLatest else {
                return failureBackoff ?? Self.urgentDelay
            }
            let due = min(latest.addingTimeInterval(Self.progressQuiet), since.addingTimeInterval(Self.progressMaxWait))
            return max(due.timeIntervalSince(now), failureBackoff ?? 0)
        }

        static func nextFailureBackoff(after previous: TimeInterval?) -> TimeInterval {
            min((previous ?? urgentDelay) * 2, maxFailureBackoff)
        }
    }

    @MainActor
    static func ensureSession(isCurrent: () -> Bool, hasCertifiedSession: () -> Bool,
                              settleResident: () async -> Void,
                              restore: () async -> Bool) async -> Bool {
        guard isCurrent() else { return false }
        if hasCertifiedSession() { return true }
        // A resident session with an admitted profile transaction temporarily cannot certify
        // a publication binding. Give its FIFO ownership back before deciding it needs replacement.
        await settleResident()
        guard isCurrent() else { return false }
        if hasCertifiedSession() { return true }
        let restored = await restore()
        return restored && isCurrent() && hasCertifiedSession()
    }

    /// Cold host/credential projection can run before a native session is attributable. Mounting
    /// it afterward is not an ACK: commit this exact pulled document and its host preferences under
    /// the still-current capture before acknowledging the cloud version.
    @MainActor
    static func mayAcknowledgeDocument(nativeDocumentCommitted: Bool, isCurrent: () -> Bool,
                                       ensureSession: () async -> Bool,
                                       commitDocument: () async -> Bool) async -> Bool {
        guard isCurrent() else { return false }
        guard nativeDocumentCommitted else {
            guard await ensureSession(), isCurrent() else { return false }
            return await commitDocument() && isCurrent()
        }
        return true
    }

    struct ResourceIntent: Sendable {
        let searchQuery: String?
        let metadataAction: Data?
        let metadataGeneration: UUID

        func searchToReplay(currentQuery: String?) -> String? {
            guard let searchQuery, searchQuery == currentQuery, searchQuery.count >= 2 else { return nil }
            return searchQuery
        }

        func metadataToReplay(currentGeneration: UUID) -> Data? {
            currentGeneration == metadataGeneration ? metadataAction : nil
        }

        func searchToReplay(currentQuery: String?, hasPendingSearch: Bool, pendingScopeIsCurrent: Bool) -> String? {
            if hasPendingSearch, pendingScopeIsCurrent { return currentQuery }
            return searchToReplay(currentQuery: currentQuery)
        }

        func metadataToReplay(currentAction: Data?, currentGeneration: UUID, pendingScopeIsCurrent: Bool) -> Data? {
            if let currentAction, pendingScopeIsCurrent { return currentAction }
            return metadataToReplay(currentGeneration: currentGeneration)
        }
    }

    enum ProgressIdentity: Equatable {
        case movie
        case episode(String)
        case reject
    }

    /// Episode-like media must carry an exact video identity. The title fallback used by
    /// PlaybackMeta cannot become an episode progress record in the kernel.
    static func progressIdentity(isEpisodic: Bool, libraryID: String, videoID: String) -> ProgressIdentity {
        guard isEpisodic else { return .movie }
        guard !videoID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              videoID != libraryID else { return .reject }
        return .episode(videoID)
    }
}
