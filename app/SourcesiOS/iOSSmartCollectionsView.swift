import SwiftUI

/// Which collection the builder opens on: nil is a new one.
struct iOSSmartCollectionEditing: Identifiable {
    let collection: SmartCollection?
    var id: String { collection?.id ?? "new" }
}

/// Library > Smart collections on iPhone, iPad and Mac: every collection as a Home-style rail of its
/// titles (pinned to Home or not), with Edit, plus New collection. Tapping a card opens the normal detail
/// page through the Library's navigation stack.
struct iOSSmartCollectionsView: View {
    let onOpen: (RailItem) -> Void

    @EnvironmentObject private var theme: ThemeManager
    @ObservedObject private var store = ForYouStore.shared
    @ObservedObject private var watchedIndex = WatchedIndex.shared
    @StateObject private var titles = SmartCollectionTitlesModel()
    @State private var editing: iOSSmartCollectionEditing?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Space.lg) {
                Text("Rows built from rules: genres, people, years and ratings. Pin one to Home or keep it here.")
                    .font(Theme.Typography.body).foregroundStyle(Theme.Palette.textSecondary)
                    .padding(.horizontal, Theme.Space.md)
                Button { editing = iOSSmartCollectionEditing(collection: nil) } label: { Label("New collection", systemImage: "plus") }
                    .buttonStyle(ChipButtonStyle(selected: false))
                    .padding(.horizontal, Theme.Space.md)
                if !store.ready {
                    note("Your collections appear once your account has loaded.")
                } else if store.collections.isEmpty {
                    note("No smart collections yet.")
                }
                ForEach(store.collections) { collection in
                    collectionRail(collection)
                }
            }
            .padding(.vertical, Theme.Space.md)
        }
        .background(Theme.Palette.canvas.ignoresSafeArea())
        .navigationTitle(Text("Smart collections"))
        .onAppear { titles.load(store.collections) }
        .onChange(of: store.collections) { collections in titles.load(collections) }
        .sheet(item: $editing) { editing in
            SmartCollectionEditor(existing: editing.collection, watched: watchedIndex.ids, onFinish: { self.editing = nil }) { items, loading in
                if items.isEmpty {
                    if loading { ProgressView().padding(Theme.Space.md) }
                } else {
                    PosterRail(title: String(localized: "Preview"), items: items.map(railItem), onTap: { _ in })
                }
            }
            .environmentObject(theme)
        }
    }

    @ViewBuilder private func collectionRail(_ collection: SmartCollection) -> some View {
        let raw = titles.titles[collection.id] ?? []
        let items = collection.rules.hideWatched ? raw.filter { !watchedIndex.ids.contains($0.id) } : raw
        VStack(alignment: .leading, spacing: Theme.Space.xs) {
            HStack(spacing: Theme.Space.sm) {
                Button { editing = iOSSmartCollectionEditing(collection: collection) } label: { Label("Edit", systemImage: "slider.horizontal.3") }
                    .buttonStyle(ChipButtonStyle(selected: false))
                if collection.home { Text("On Home").eyebrowStyle(Theme.Palette.accent) }
            }
            .padding(.horizontal, Theme.Space.md)
            if items.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(collection.rules.summary).eyebrowStyle()
                    Text(collection.name).sectionTitleStyle()
                    Text(titles.loading.contains(collection.id) ? LocalizedStringKey("Finding titles") : LocalizedStringKey("No titles match these rules yet."))
                        .font(Theme.Typography.label).foregroundStyle(Theme.Palette.textTertiary)
                }
                .padding(.horizontal, Theme.Space.md)
            } else {
                PosterRail(title: collection.name, eyebrow: collection.rules.summary, items: items.map(railItem),
                           onTap: onOpen, showWatchedBadges: true)
            }
        }
    }

    private func note(_ text: LocalizedStringKey) -> some View {
        Text(text).font(Theme.Typography.label).foregroundStyle(Theme.Palette.textTertiary)
            .padding(.horizontal, Theme.Space.md)
    }

    private func railItem(_ preview: MetaPreview) -> RailItem {
        RailItem(id: preview.id, type: preview.type, name: preview.name, poster: preview.poster, progress: 0)
    }
}
