import Foundation

/// Network shares: folders on a NAS or computer that VortX browses and plays directly, without Plex or
/// Jellyfin. Two protocols, both plain HTTP so they need no extra library and play in either engine:
///   - WebDAV (Synology, QNAP, TrueNAS, Nextcloud, Windows and most NAS file services)
///   - a web server's folder listing (nginx autoindex, Apache, Caddy file_server, `python3 -m http.server`)
///
/// Everything in this file is pure (no network, no storage) so the parsing and the credential scoping are
/// tested directly. Android implements the same rules in `NetworkShares.kt`.
enum NetworkShareKind: String, Codable, CaseIterable, Sendable {
    case webdav
    case http

    var label: String {
        switch self {
        case .webdav: return "WebDAV"
        case .http: return "Web folder"
        }
    }
}

/// One saved share. Non-secret, kept on this device only; the password lives in the Keychain.
struct NetworkShare: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    var name: String
    var kind: NetworkShareKind
    /// The share's root folder, normalized to end in "/".
    var url: String
    /// Empty when the share needs no login.
    var username: String
    var addedAt: Date

    var rootURL: URL? { URL(string: url) }
}

/// A folder or video inside a share.
struct NetworkShareEntry: Identifiable, Equatable, Sendable {
    let name: String
    let url: URL
    let isFolder: Bool
    let size: Int64?

    var id: String { url.absoluteString }
    var isVideo: Bool { !isFolder && NetworkSharePolicy.isVideo(name) }
}

enum NetworkSharePolicy {
    static let videoExtensions: Set<String> = [
        "mkv", "mp4", "m4v", "mov", "avi", "ts", "m2ts", "mts", "webm", "wmv", "flv", "mpg", "mpeg", "ogv",
        "3gp", "vob", "divx", "iso",
    ]

    static func isVideo(_ name: String) -> Bool {
        guard let dot = name.lastIndex(of: ".") else { return false }
        return videoExtensions.contains(name[name.index(after: dot)...].lowercased())
    }

    /// The share root for what the viewer typed: http or https with a host, no login in the address (that
    /// goes in the username and password fields, never a URL), no query or fragment, ending in "/".
    /// A bare "nas.local:5005/video" gets http:// added.
    static func rootURL(_ raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.lowercased().hasPrefix("http://") && !text.lowercased().hasPrefix("https://") {
            guard !text.contains("://") else { return nil }
            text = "http://" + text
        }
        guard var components = URLComponents(string: text),
              let scheme = components.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil else { return nil }
        components.scheme = scheme
        if !components.path.hasSuffix("/") { components.path += "/" }
        return components.url
    }

    /// True when `url` is the share's own server and inside its root folder. Only such URLs ever receive
    /// the share's login, and only such entries are listed, so a hostile listing cannot point VortX, or the
    /// viewer's password, somewhere else.
    static func isInside(_ url: URL, root: URL) -> Bool {
        guard url.scheme?.lowercased() == root.scheme?.lowercased(),
              url.host?.lowercased() == root.host?.lowercased(),
              effectivePort(url) == effectivePort(root) else { return false }
        let path = decodedPath(url), rootPath = decodedPath(root)
        guard !path.split(separator: "/").contains("..") else { return false }
        return path.hasPrefix(rootPath)
    }

    /// "Basic ..." for a share with a login, nil without one.
    static func authorization(username: String, password: String) -> String? {
        guard !username.isEmpty else { return nil }
        return "Basic " + Data("\(username):\(password)".utf8).base64EncodedString()
    }

    /// The headers a request to `url` carries: the login only inside the share's root.
    static func headers(for url: URL, root: URL, username: String, password: String) -> [String: String] {
        guard isInside(url, root: root), let value = authorization(username: username, password: password) else { return [:] }
        return ["Authorization": value]
    }

    /// WebDAV PROPFIND body asking only for what the browser shows.
    static let propfindBody = Data("""
        <?xml version="1.0" encoding="utf-8"?>
        <d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:resourcetype/><d:getcontentlength/></d:prop></d:propfind>
        """.utf8)

    /// The direct children of `directory` in a WebDAV multistatus response.
    static func parseWebDAV(_ data: Data, directory: URL, root: URL) -> [NetworkShareEntry]? {
        let reader = WebDAVReader()
        let parser = XMLParser(data: data)
        parser.shouldProcessNamespaces = true
        parser.delegate = reader
        guard parser.parse(), reader.sawMultistatus else { return nil }
        return reader.responses.compactMap { response in
            guard let url = child(href: response.href, directory: directory, root: root, isFolder: response.isFolder) else { return nil }
            let name = response.displayName.flatMap { $0.isEmpty ? nil : $0 } ?? lastComponent(url)
            return NetworkShareEntry(name: name, url: url, isFolder: response.isFolder, size: response.length)
        }
    }

    /// The direct children of `directory` linked from a web server's folder listing.
    static func parseHTMLIndex(_ html: String, directory: URL, root: URL) -> [NetworkShareEntry] {
        guard let regex = try? NSRegularExpression(pattern: #"<a\s[^>]*href\s*=\s*["']([^"']+)["']"#, options: [.caseInsensitive]) else { return [] }
        var seen = Set<String>()
        var entries: [NetworkShareEntry] = []
        let range = NSRange(html.startIndex..., in: html)
        for match in regex.matches(in: html, range: range) {
            guard let hrefRange = Range(match.range(at: 1), in: html) else { continue }
            let href = String(html[hrefRange]).replacingOccurrences(of: "&amp;", with: "&")
            if href.hasPrefix("?") || href.hasPrefix("#") || href.lowercased().hasPrefix("mailto:") { continue }
            let isFolder = href.hasSuffix("/")
            guard let url = child(href: href, directory: directory, root: root, isFolder: isFolder),
                  seen.insert(url.absoluteString).inserted else { continue }
            entries.append(NetworkShareEntry(name: lastComponent(url), url: url, isFolder: isFolder, size: nil))
        }
        return entries
    }

    /// What the browser shows: folders and videos, no hidden files, folders first, then natural name order.
    static func visible(_ entries: [NetworkShareEntry]) -> [NetworkShareEntry] {
        entries
            .filter { !$0.name.hasPrefix(".") && ($0.isFolder || $0.isVideo) }
            .sorted { lhs, rhs in
                if lhs.isFolder != rhs.isFolder { return lhs.isFolder }
                return lhs.name.localizedStandardCompare(rhs.name) == .orderedAscending
            }
    }

    // MARK: - Helpers

    /// `href` resolved against `directory`, kept only when it is a direct child inside the share root.
    private static func child(href: String, directory: URL, root: URL, isFolder: Bool) -> URL? {
        // Some servers send hrefs with raw spaces or brackets; escape those (but not existing %XX escapes).
        let escaped = URL(string: href) == nil ? href.addingPercentEncoding(withAllowedCharacters: hrefAllowed) : href
        guard let escaped, var url = URL(string: escaped, relativeTo: directory)?.absoluteURL else { return nil }
        // `URL.path` drops a trailing slash, so check the encoded path itself.
        if isFolder, var components = URLComponents(url: url, resolvingAgainstBaseURL: false),
           !components.percentEncodedPath.hasSuffix("/") {
            components.percentEncodedPath += "/"
            url = components.url ?? url
        }
        guard isInside(url, root: root) else { return nil }
        let childParts = decodedPath(url).split(separator: "/")
        let directoryParts = decodedPath(directory).split(separator: "/")
        guard childParts.count == directoryParts.count + 1, Array(childParts.prefix(directoryParts.count)) == directoryParts else { return nil }
        return url
    }

    private static let hrefAllowed: CharacterSet = {
        var allowed = CharacterSet.urlPathAllowed
        allowed.insert(charactersIn: "%:")
        return allowed
    }()

    private static func decodedPath(_ url: URL) -> String {
        let raw = URLComponents(url: url, resolvingAgainstBaseURL: true)?.percentEncodedPath ?? url.path
        let decoded = raw.removingPercentEncoding ?? raw
        return decoded.hasSuffix("/") ? decoded : decoded + "/"
    }

    private static func effectivePort(_ url: URL) -> Int {
        url.port ?? (url.scheme?.lowercased() == "https" ? 443 : 80)
    }

    private static func lastComponent(_ url: URL) -> String {
        let part = url.path.split(separator: "/").last.map(String.init) ?? url.host ?? ""
        return part.removingPercentEncoding ?? part
    }
}

/// Collects `<response>` elements from a WebDAV multistatus body, by local name so any namespace prefix works.
private final class WebDAVReader: NSObject, XMLParserDelegate {
    struct Response {
        var href = ""
        var displayName: String?
        var isFolder = false
        var length: Int64?
    }

    private(set) var responses: [Response] = []
    private(set) var sawMultistatus = false
    private var current: Response?
    private var text = ""

    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?,
                qualifiedName qName: String?, attributes attributeDict: [String: String] = [:]) {
        switch elementName.lowercased() {
        case "multistatus": sawMultistatus = true
        case "response": current = Response()
        case "collection": current?.isFolder = true
        default: break
        }
        text = ""
    }

    func parser(_ parser: XMLParser, foundCharacters string: String) { text += string }

    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName qName: String?) {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        switch elementName.lowercased() {
        case "href": if current?.href.isEmpty == true { current?.href = value }
        case "displayname": current?.displayName = value
        case "getcontentlength": current?.length = Int64(value)
        case "response":
            if let response = current, !response.href.isEmpty { responses.append(response) }
            current = nil
        default: break
        }
        text = ""
    }
}
