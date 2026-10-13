import Foundation

// Compiled by scripts/test-for-you-model.sh as main.swift beside the real ForYouRowsModel.swift,
// ForYouInterests.swift and ForYouDiscover.swift, with a recording TMDB fake in place of TMDBClient.

enum TMDBClient {
    @MainActor static var calls: [String] = []
    @MainActor static var empty = false
    @MainActor static func ruleTitles(movieQuery: String?, tvQuery: String?, limit: Int = 20) async -> [MetaPreview] {
        calls.append("rule:\(movieQuery ?? "-")|\(tvQuery ?? "-")")
        await Task.yield()
        return empty ? [] : (1...5).map { MetaPreview(id: "tt\(calls.count)\($0)", type: "movie", name: "T\($0)", poster: nil, posterShape: nil, popularity: nil) }
    }
    @MainActor static func personTopTitles(id: Int, limit: Int = 20) async -> [MetaPreview] {
        calls.append("person:\(id)")
        await Task.yield()
        return empty ? [] : (1...3).map { MetaPreview(id: "tp\(id)\($0)", type: "movie", name: "P\($0)", poster: nil, posterShape: nil, popularity: nil) }
    }
}

var failures = 0
var checks = 0
func expect(_ condition: @autoclosure () -> Bool, _ message: String, line: Int = #line) {
    checks += 1
    if !condition() { failures += 1; print("FAIL line \(line): \(message)") }
}

@MainActor func settle(_ model: ForYouRowsModel) async {
    for _ in 0..<200 { await Task.yield() }
    try? await Task.sleep(nanoseconds: 20_000_000)
    for _ in 0..<200 { await Task.yield() }
}

@MainActor func run() async {
    let profile = UUID()
    let interests = ForYouInterests(genres: ["scifi"], people: [ForYouPerson(id: 7, name: "Someone")], decided: true)
    var pinnedRules = SmartCollectionRules()
    pinnedRules.genres = ["drama"]
    pinnedRules.hideWatched = true
    let pinned = SmartCollection.make(name: "Weekend dramas", rules: pinnedRules, home: true)
    let libraryOnly = SmartCollection.make(name: "Library only", rules: SmartCollectionRules(), home: false)
    let model = ForYouRowsModel()

    model.refresh(profileID: profile, interests: interests, collections: [pinned, libraryOnly], owned: [], watched: [], enabled: true)
    await settle(model)
    expect(model.interestRows.map(\.id) == ["forYou.genre.scifi", "forYou.person.7"], "interest rows follow the plan order")
    expect(model.interestRows.first?.eyebrow == "From your interests", "the reason is shown")
    expect(model.collectionRows.map(\.title) == ["Weekend dramas"], "only collections pinned to Home become rows")
    let fetched = TMDBClient.calls.count
    expect(fetched == 3, "two interest rows and one pinned collection are fetched")

    let firstScifi = model.interestRows[0].items[0].id
    let firstDrama = model.collectionRows[0].items[0].id
    model.refresh(profileID: profile, interests: interests, collections: [pinned, libraryOnly],
                  owned: [firstScifi], watched: [firstDrama], enabled: true)
    await settle(model)
    expect(TMDBClient.calls.count == fetched, "ownership and watched changes re-filter without refetching")
    expect(!model.interestRows[0].items.contains { $0.id == firstScifi }, "owned titles leave interest rows")
    expect(!model.collectionRows[0].items.contains { $0.id == firstDrama }, "hide watched applies to the pinned row")

    var renamed = pinned
    renamed.name = "Renamed"
    model.refresh(profileID: profile, interests: interests, collections: [renamed], owned: [], watched: [], enabled: true)
    await settle(model)
    expect(TMDBClient.calls.count == fetched, "renaming a collection reuses its results")
    expect(model.collectionRows.map(\.title) == ["Renamed"], "the new name shows")

    model.refresh(profileID: profile, interests: interests, collections: [], owned: [], watched: [], enabled: false)
    expect(model.interestRows.isEmpty && model.collectionRows.isEmpty, "disabled clears every row at once")

    TMDBClient.empty = true
    let other = UUID()
    model.refresh(profileID: other, interests: interests, collections: [], owned: [], watched: [], enabled: true)
    await settle(model)
    let afterEmpty = TMDBClient.calls.count
    expect(model.interestRows.isEmpty, "an empty fetch shows no rows")
    TMDBClient.empty = false
    model.refresh(profileID: other, interests: interests, collections: [], owned: [], watched: [], enabled: true)
    await settle(model)
    expect(TMDBClient.calls.count == afterEmpty + 2, "an all-empty fetch is retried on the next refresh")
    expect(model.interestRows.count == 2, "the retry fills the rows")

    print("for-you rows model: \(checks) checks, \(failures) failed")
    exit(failures == 0 ? 0 : 1)
}

Task { @MainActor in await run() }
dispatchMain()
