import SwiftUI
import CoreGraphics
import QuartzCore

/// The shell only borrows an immutable image already accepted by the visible hero's loader.
/// A title/route change publishes nil immediately; no shell request, decode or scroll observer exists.
struct CinemaNavigationArtwork: Equatable {
    let owner: String
    let identity: String
    let image: CGImage
    let height: CGFloat
    let contentMode: ContentMode
    let panEpoch: CFTimeInterval?
    let reduceMotion: Bool

    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.owner == rhs.owner && lhs.identity == rhs.identity && lhs.image === rhs.image
            && lhs.height == rhs.height && lhs.contentMode == rhs.contentMode
            && lhs.panEpoch == rhs.panEpoch && lhs.reduceMotion == rhs.reduceMotion
    }
}

struct CinemaNavigationArtworkKey: PreferenceKey {
    static var defaultValue: CinemaNavigationArtwork? { nil }
    static func reduce(value: inout CinemaNavigationArtwork?, nextValue: () -> CinemaNavigationArtwork?) {
        if let next = nextValue() { value = next }
    }
}

struct CinemaNavigationHeightKey: PreferenceKey {
    static var defaultValue: CGFloat { 0 }
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

private struct CinemaNavigationArtworkInsetKey: EnvironmentKey { static let defaultValue: CGFloat = 0 }
private struct CinemaNavigationArtworkOwnerKey: EnvironmentKey { static let defaultValue = "" }
extension EnvironmentValues {
    var cinemaNavigationArtworkInset: CGFloat {
        get { self[CinemaNavigationArtworkInsetKey.self] }
        set { self[CinemaNavigationArtworkInsetKey.self] = newValue }
    }
    var cinemaNavigationArtworkOwner: String {
        get { self[CinemaNavigationArtworkOwnerKey.self] }
        set { self[CinemaNavigationArtworkOwnerKey.self] = newValue }
    }
}

/// Both slices use the same expanded image rectangle. The menu retains real layout space;
/// only the decorative image continues upward across that space.
enum CinemaArtworkGeometry {
    static func expandedHeight(band: CGFloat, navigation: CGFloat) -> CGFloat {
        max(0, band) + max(0, navigation)
    }
}

/// A hero is a self-contained rounded media card. Its artwork does not share the navigation strip's
/// crop: the reserved header may borrow an ambient treatment, while this viewport remains confined.
enum CinemaHeroCardGeometry {
    static let inset: CGFloat = 16
    static let radius: CGFloat = 28
    static func innerWidth(viewport: CGFloat) -> CGFloat { max(1, viewport - inset * 2) }
}

private struct CinemaHeroCardTreatment: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: CinemaHeroCardGeometry.radius, style: .continuous)
        content
            .background(shape.fill(Theme.Palette.accent.opacity(reduceTransparency ? 0.022 : 0.045)))
            .overlay {
                LinearGradient(colors: [Theme.Palette.accent.opacity(0.075), .clear,
                                         Theme.Palette.accent.opacity(0.018)],
                               startPoint: .topLeading, endPoint: .bottomTrailing)
                    .clipShape(shape).allowsHitTesting(false)
            }
            .clipShape(shape)
            .overlay(shape.strokeBorder(Theme.Palette.accent.opacity(reduceTransparency ? 0.24 : 0.16), lineWidth: 0.8)
                .allowsHitTesting(false))
            .shadow(color: .black.opacity(0.32), radius: 20, y: 12)
    }
}

extension View {
    func cinemaHeroCard() -> some View { modifier(CinemaHeroCardTreatment()) }
}

/// Floating top navigation. The bar is a top safe-area inset, so screens still start their controls
/// beneath it, while scroll views run underneath and stay visible through it at every scroll position.
struct CinemaNavigationShell<Navigation: View, Content: View>: View {
    let navigation: Navigation
    let content: Content

    init(@ViewBuilder navigation: () -> Navigation, @ViewBuilder content: () -> Content) {
        self.navigation = navigation()
        self.content = content()
    }

    var body: some View {
        content.frame(maxWidth: .infinity, maxHeight: .infinity)
            .modifier(CinemaFloatingNavigationBar(navigation: navigation))
    }
}

/// OS 26 hands the bar to the system, which softly blurs whatever scrolls beneath it (the iOS bar
/// treatment). Older systems and Reduce Transparency draw their own backdrop instead.
private struct CinemaFloatingNavigationBar<Navigation: View>: ViewModifier {
    let navigation: Navigation
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    func body(content: Content) -> some View {
        if #available(iOS 26.0, macOS 26.0, *), !reduceTransparency {
            content
                .safeAreaBar(edge: .top, spacing: 0) { navigation }
                .scrollEdgeEffectStyle(.soft, for: .top)
        } else {
            content.safeAreaInset(edge: .top, spacing: 0) {
                navigation.background { CinemaNavigationBackdrop(reduceTransparency: reduceTransparency) }
            }
        }
    }
}

/// Pre-26 backdrop: material that fades out at its lower edge, so content dissolves under the bar
/// rather than meeting a hard line. Reduce Transparency keeps a near-opaque canvas for legibility.
private struct CinemaNavigationBackdrop: View {
    let reduceTransparency: Bool

    var body: some View {
        Group {
            if reduceTransparency {
                Theme.Palette.canvas.opacity(0.94)
            } else {
                Rectangle().fill(.ultraThinMaterial)
                    .overlay(Theme.Palette.canvas.opacity(0.35))
                    .mask(LinearGradient(stops: [.init(color: .black, location: 0), .init(color: .black, location: 0.7),
                                                 .init(color: .clear, location: 1)],
                                         startPoint: .top, endPoint: .bottom))
            }
        }
        .ignoresSafeArea(edges: .top)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

struct CinemaHeroDissolve: View {
    var body: some View {
        LinearGradient(stops: [
            .init(color: .white, location: 0),
            .init(color: .white, location: 0.68),
            .init(color: .white.opacity(0.72), location: 0.86),
            .init(color: .clear, location: 1),
        ], startPoint: .top, endPoint: .bottom)
    }
}

struct CinemaNavigationArtworkStrip: View {
    let artwork: CinemaNavigationArtwork?
    let reduceTransparency: Bool
    let highContrast: Bool

    var body: some View {
        ZStack {
            Theme.Palette.canvas
            if let artwork {
                CinemaNavigationArtworkLayerHost(artwork: artwork)
                    .id("\(artwork.owner):\(artwork.identity)")
                // Keep the full wordmark, capsule, profile and search chrome legible, while
                // retaining the same leading scrim as the adjoining hero image.
                LinearGradient(colors: [Theme.Palette.canvas.opacity(0.5), .clear],
                               startPoint: .leading, endPoint: .center)
                Theme.Palette.canvas.opacity(reduceTransparency || highContrast ? 0.60 : 0.45)
            } else {
                Theme.Palette.accent.opacity(HomeAtmospherePolicy.alpha(
                    reduceTransparency: reduceTransparency, highContrast: highContrast))
            }
        }
        .clipped()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
