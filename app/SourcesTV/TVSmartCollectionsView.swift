import SwiftUI

/// Which collection the builder opens on: nil is a new one.
struct SmartCollectionEditing: Identifiable {
    let collection: SmartCollection?
    var id: String { collection?.id ?? "new" }
}

/// Library > Smart collections on tvOS: every collection as a row of its titles (pinned to Home or not),
/// with Edit, plus New collection. Rows are the same `ExternalWatchlistRow` Home uses, so cards follow
/// the user's landscape or poster setting and open the normal detail page.
struct TVSmartCollectionsView: View {
    @EnvironmentObject private var theme: ThemeManager
    @ObservedObject private var store = ForYouStore.shared
    @ObservedObject private var watchedIndex = WatchedIndex.shared
    @StateObject private var titles = SmartCollectionTitlesModel()
    @StateObject private var focusOwner = FocusedItemOwner()   // owned, not observed: focus re-renders only the hero
    private var focusModel: FocusedItemModel { focusOwner.model }
    @State private var editing: SmartCollectionEditing?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Space.lg) {
                VStack(alignment: .leading, spacing: Theme.Space.xs) {
                    Text("Smart collections").screenTitleStyle()
                    Text("Rows built from rules: genres, people, years and ratings. Pin one to Home or keep it here.")
                        .font(Theme.Typography.body).foregroundStyle(Theme.Palette.textSecondary)
                }
                .padding(.horizontal, Theme.Space.screenEdge)
                Button { editing = SmartCollectionEditing(collection: nil) } label: { Label("New collection", systemImage: "plus") }
                    .buttonStyle(ChipButtonStyle(selected: false))
                    .padding(.horizontal, Theme.Space.screenEdge)
                if !store.ready {
                    Text("Your collections appear once your account has loaded.")
                        .font(Theme.Typography.label).foregroundStyle(Theme.Palette.textTertiary)
                        .padding(.horizontal, Theme.Space.screenEdge)
                } else if store.collections.isEmpty {
                    Text("No smart collections yet.")
                        .font(Theme.Typography.label).foregroundStyle(Theme.Palette.textTertiary)
                        .padding(.horizontal, Theme.Space.screenEdge)
                }
                ForEach(store.collections) { collection in
                    collectionRow(collection)
                }
            }
            .padding(.vertical, Theme.Space.xl)
        }
        .background(Theme.Palette.canvas.ignoresSafeArea())
        .onAppear { titles.load(store.collections) }
        .onChange(of: store.collections) { collections in titles.load(collections) }
        .fullScreenCover(item: $editing) { editing in
            SmartCollectionEditor(existing: editing.collection, watched: watchedIndex.ids, onFinish: { self.editing = nil }) { items, loading in
                TVSmartCollectionPreview(items: items, loading: loading)
            }
            .environmentObject(theme)
        }
    }

    @ViewBuilder private func collectionRow(_ collection: SmartCollection) -> some View {
        let raw = titles.titles[collection.id] ?? []
        let items = collection.rules.hideWatched ? raw.filter { !watchedIndex.ids.contains($0.id) } : raw
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            HStack(spacing: Theme.Space.sm) {
                Button { editing = SmartCollectionEditing(collection: collection) } label: { Label("Edit", systemImage: "slider.horizontal.3") }
                    .buttonStyle(ChipButtonStyle(selected: false))
                if collection.home {
                    Text("On Home").eyebrowStyle(Theme.Palette.accent)
                }
            }
            .padding(.horizontal, Theme.Space.screenEdge)
            if items.isEmpty {
                RailHeader(eyebrow: collection.rules.summary, title: collection.name)
                Text(titles.loading.contains(collection.id) ? LocalizedStringKey("Finding titles") : LocalizedStringKey("No titles match these rules yet."))
                    .font(Theme.Typography.label).foregroundStyle(Theme.Palette.textTertiary)
                    .padding(.horizontal, Theme.Space.screenEdge)
            } else {
                ExternalWatchlistRow(eyebrow: collection.rules.summary, title: collection.name, items: items, focusModel: focusModel)
            }
        }
    }
}

/// The builder's live preview: the same cards, focusable for scrolling but inert (the builder is modal).
struct TVSmartCollectionPreview: View {
    let items: [MetaPreview]
    let loading: Bool

    var body: some View {
        if items.isEmpty {
            if loading { ProgressView() }
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: Theme.Space.lg) {
                    ForEach(items) { item in
                        PosterCard(title: item.name, poster: item.poster, type: item.type, id: item.id, onDetails: {})
                    }
                }
                .padding(.vertical, Theme.Space.lg)
            }
        }
    }
}
