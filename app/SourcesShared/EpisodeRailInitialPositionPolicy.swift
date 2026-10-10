import Foundation

/// An initial position is a one-shot per title/profile and selected season. Metadata/history may
/// arrive late, but a user's drag or paging action consumes the shot before that late update lands.
struct EpisodeRailInitialPositionPolicy {
    private var scope: String?
    private var consumedSeasons: Set<Int> = []

    static func firstUnwatchedIndex(ids: [String], watched: Set<String>) -> Int {
        ids.firstIndex { !watched.contains($0) } ?? 0
    }

    mutating func claim(scope: String, season: Int, ids: [String], watched: Set<String>, ready: Bool) -> Int? {
        selectScope(scope)
        guard ready, !ids.isEmpty, consumedSeasons.insert(season).inserted else { return nil }
        return Self.firstUnwatchedIndex(ids: ids, watched: watched)
    }

    mutating func userInteracted(scope: String, season: Int) {
        selectScope(scope)
        consumedSeasons.insert(season)
    }

    private mutating func selectScope(_ next: String) {
        guard scope != next else { return }
        scope = next
        consumedSeasons = []
    }
}
