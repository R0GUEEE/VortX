import Foundation

enum NetworkShareError: Error, Equatable, Sendable {
    case invalidAddress
    case unauthorized
    case notAShare
    case unreachable
    case credentialStorage

    var message: String {
        switch self {
        case .invalidAddress: return String(localized: "Enter an http:// or https:// address, like 192.168.1.20:5005/video.")
        case .unauthorized: return String(localized: "The share refused that username or password.")
        case .notAShare: return String(localized: "That address answered, but not with a WebDAV share or a folder listing.")
        case .unreachable: return String(localized: "Couldn't reach the share. Check the address and that this device is on the same network.")
        case .credentialStorage: return String(localized: "Couldn't save the password securely on this device.")
        }
    }
}

/// Talks to a share over HTTP. Foundation only, so it is tested against a live local server.
///
/// The login travels only in an Authorization header and only to URLs inside the share's root
/// (`NetworkSharePolicy.headers`). A redirect anywhere else is refused, so a misconfigured or hostile server
/// cannot bounce the request, or the password, to another host.
enum NetworkShareClient {
    struct Login: Sendable {
        let username: String
        let password: String
    }

    /// WebDAV when the root answers PROPFIND with a multistatus body, else a web folder when it serves a
    /// listing. A refused login stops the check at once.
    static func detect(root: URL, login: Login) async throws -> NetworkShareKind {
        switch await propfind(root, root: root, login: login) {
        case .listing: return .webdav
        case .unauthorized: throw NetworkShareError.unauthorized
        case .unreachable: throw NetworkShareError.unreachable
        case .notAListing: break
        }
        switch await getIndex(root, root: root, login: login) {
        case .listing: return .http
        case .unauthorized: throw NetworkShareError.unauthorized
        case .unreachable: throw NetworkShareError.unreachable
        case .notAListing: throw NetworkShareError.notAShare
        }
    }

    /// The visible contents of `folder` (folders first, then videos).
    static func list(_ folder: URL, kind: NetworkShareKind, root: URL, login: Login) async throws -> [NetworkShareEntry] {
        let outcome = kind == .webdav
            ? await propfind(folder, root: root, login: login)
            : await getIndex(folder, root: root, login: login)
        switch outcome {
        case .listing(let entries): return NetworkSharePolicy.visible(entries)
        case .unauthorized: throw NetworkShareError.unauthorized
        case .unreachable: throw NetworkShareError.unreachable
        case .notAListing: throw NetworkShareError.notAShare
        }
    }

    // MARK: - Requests

    private enum Outcome {
        case listing([NetworkShareEntry])
        case unauthorized
        case notAListing
        case unreachable
    }

    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.httpShouldSetCookies = false
        configuration.httpCookieAcceptPolicy = .never
        configuration.urlCache = nil
        return URLSession(configuration: configuration)
    }()

    private static func propfind(_ folder: URL, root: URL, login: Login) async -> Outcome {
        var request = request(folder, root: root, login: login)
        request.httpMethod = "PROPFIND"
        request.setValue("1", forHTTPHeaderField: "Depth")
        request.setValue("application/xml; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.httpBody = NetworkSharePolicy.propfindBody
        return await perform(request, root: root, login: login) { data, status in
            guard status == 207 else { return nil }
            return NetworkSharePolicy.parseWebDAV(data, directory: folder, root: root)
        }
    }

    private static func getIndex(_ folder: URL, root: URL, login: Login) async -> Outcome {
        var request = request(folder, root: root, login: login)
        request.httpMethod = "GET"
        request.setValue("text/html", forHTTPHeaderField: "Accept")
        return await perform(request, root: root, login: login) { data, status in
            guard status == 200, let html = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1),
                  html.range(of: "<a ", options: .caseInsensitive) != nil else { return nil }
            return NetworkSharePolicy.parseHTMLIndex(html, directory: folder, root: root)
        }
    }

    private static func request(_ url: URL, root: URL, login: Login) -> URLRequest {
        var request = URLRequest(url: url)
        for (field, value) in NetworkSharePolicy.headers(for: url, root: root, username: login.username, password: login.password) {
            request.setValue(value, forHTTPHeaderField: field)
        }
        return request
    }

    private static func perform(_ request: URLRequest, root: URL, login: Login,
                                parse: (Data, Int) -> [NetworkShareEntry]?) async -> Outcome {
        guard let (data, response) = try? await session.data(for: request, delegate: RedirectGuard(root: root, login: login)),
              let http = response as? HTTPURLResponse else { return .unreachable }
        if http.statusCode == 401 || http.statusCode == 403 { return .unauthorized }
        guard let entries = parse(data, http.statusCode) else { return .notAListing }
        return .listing(entries)
    }

    /// Follows a redirect only within the share's root, re-attaching the login itself rather than trusting
    /// whatever headers the redirect carried over.
    private final class RedirectGuard: NSObject, URLSessionTaskDelegate {
        let root: URL
        let login: Login

        init(root: URL, login: Login) {
            self.root = root
            self.login = login
        }

        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                        newRequest request: URLRequest) async -> URLRequest? {
            guard let url = request.url, NetworkSharePolicy.isInside(url, root: root) else { return nil }
            var next = request
            next.setValue(nil, forHTTPHeaderField: "Authorization")
            for (field, value) in NetworkSharePolicy.headers(for: url, root: root, username: login.username, password: login.password) {
                next.setValue(value, forHTTPHeaderField: field)
            }
            return next
        }
    }
}
