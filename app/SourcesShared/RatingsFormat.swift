import Foundation

/// ONE consistent formatting for cross-provider ratings across every VortX surface: the landscape card
/// badge (`CardRatingBadge`), the detail hero's primary rating position (`iOSDetailView` / tvOS
/// `DetailView`), and the server-side baked poster (mirrored in the `cloudflare-poster` worker's
/// `buildSvg`). Change the labels/order/number format here and every Apple surface moves together, so the
/// card, the detail row, and the baked poster never disagree about how a score reads.
///
/// The numbers cannot drift because every surface reaches them the same way: `VortXRatings.swift` and
/// `MDBListClient.swift` both land the aggregators with `Int($0.rounded())`, which is exactly what the
/// worker's `Math.round` does, and both read the same service keys (imdb / rt / metacritic / tmdb). The
/// worker's own unit test asserts the printed runs in this order, so a change on that side is caught; a
/// change on THIS side has no test behind it, so anything edited here must be mirrored by hand.
///
/// What DOES differ, deliberately, is the arrangement. The card draws these tokens as the JEWEL capsule
/// (a glass plate, lettermark beside its value) because it floats in a corner over unpredictable artwork;
/// the baked portrait poster draws them as the MARQUEE block (type straight on the art, lettermark stacked
/// above its value under a rule) because it owns a tall canvas with room for a real composition. That is a
/// design decision, not drift: do not "reunify" the two compositions. Only the tokens are shared.
///
/// The model is `MDBListRatings` (the shared decode target for both the keyless VortX ratings service and a
/// user's own MDBList key): IMDb on its native 0-10 scale, Rotten Tomatoes / TMDB as 0-100 percentages, and
/// Metacritic as a 0-100 metascore (no percent sign, matching how the score is conventionally printed).
/// The audience and community scores (Popcorn, Letterboxd, Trakt, MyAnimeList) are app-only: the baked
/// poster keeps the four headline scores, while cards and the detail row show every score present.
enum RatingsFormat {
    /// A single provider score as a label + value, e.g. ("RT", "84%"). `isIMDb` marks the primary score that
    /// leads with a star glyph on the badges (its label is dropped there in favor of the star), while the
    /// detail row prints the label verbatim.
    struct Token: Equatable {
        let label: String
        let value: String
        let isIMDb: Bool
        /// The short lettermark a small badge prints ("POP", "LBXD"); the detail row prints `label`.
        var mark: String

        init(label: String, value: String, isIMDb: Bool, mark: String? = nil) {
            self.label = label
            self.value = value
            self.isIMDb = isIMDb
            self.mark = mark ?? label
        }
    }

    /// IMDb on its native 0-10 scale, one decimal ("8.3"). Matches the baked poster's `imdb.toFixed(1)`, so
    /// the card, the detail row, and the poster all print the IMDb number identically.
    static func imdb(_ v: Double) -> String { String(format: "%.1f", v) }

    /// A score on a small native scale (Letterboxd 0-5, MyAnimeList 0-10), one decimal. A value above the
    /// scale is read as the 0-100 form of the same score, so an upstream format change cannot print 88 stars.
    static func scaled(_ v: Double, max scale: Double) -> Double? {
        guard v.isFinite, v > 0 else { return nil }
        let native = v > scale ? v * scale / 100 : v
        return (native * 10).rounded() / 10
    }

    /// Ordered tokens for every score present: the four headline scores first (IMDb -> RT -> MC -> TMDB),
    /// then the audience and community scores (Popcorn -> Letterboxd -> Trakt -> MAL). Empty when nothing is
    /// present. A card badge drops tokens from the END when its card is too narrow, so the order is also
    /// the priority. `limit` caps the count outright for a surface that wants fewer. Per-score fail-soft by
    /// construction: a title carrying only IMDb yields exactly one token, so it renders only IMDb.
    static func tokens(_ r: MDBListRatings, limit: Int? = nil) -> [Token] {
        var out: [Token] = []
        if let v = r.imdb { out.append(Token(label: "IMDb", value: imdb(v), isIMDb: true)) }
        if let v = r.rottenTomatoes { out.append(Token(label: "RT", value: "\(v)%", isIMDb: false)) }
        if let v = r.metacritic { out.append(Token(label: "MC", value: "\(v)", isIMDb: false)) }
        if let v = r.tmdb { out.append(Token(label: "TMDB", value: "\(v)%", isIMDb: false)) }
        if let v = r.rtAudience { out.append(Token(label: "Popcorn", value: "\(v)%", isIMDb: false, mark: "POP")) }
        if let v = r.letterboxd {
            out.append(Token(label: "Letterboxd", value: String(format: "%.1f", v), isIMDb: false, mark: "LBXD"))
        }
        if let v = r.trakt { out.append(Token(label: "Trakt", value: "\(v)%", isIMDb: false)) }
        if let v = r.mal { out.append(Token(label: "MAL", value: String(format: "%.1f", v), isIMDb: false)) }
        if let limit, limit >= 0, out.count > limit { return Array(out.prefix(limit)) }
        return out
    }

    /// The joined detail-row string ("IMDb 8.3  ·  RT 84%  ·  MC 69  ·  TMDB 81%  ·  Popcorn 90%"), or nil
    /// when no score is present. The one string every detail surface prints, so tvOS and iOS/Mac never diverge.
    static func joined(_ r: MDBListRatings) -> String? {
        let parts = tokens(r).map { "\($0.label) \($0.value)" }
        return parts.isEmpty ? nil : parts.joined(separator: "  \u{00B7}  ")
    }
}
