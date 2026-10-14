import Foundation

/// The network shares saved on this device. The non-secret list (name, kind, address, username) lives in
/// UserDefaults under `vortx.networkShares.list`; each password lives only in the Keychain under
/// `vortx.networkshare.<uuid>.password`. Device-local by design: a share is an address on this network,
/// so it is neither synced to the account nor carried into a settings backup.
@MainActor
final class NetworkShareStore: ObservableObject {
    static let shared = NetworkShareStore()
    static let listKey = "vortx.networkShares.list"

    @Published private(set) var shares: [NetworkShare] = []

    private init() {
        guard let data = UserDefaults.standard.data(forKey: Self.listKey),
              let saved = try? JSONDecoder().decode([NetworkShare].self, from: data) else { return }
        shares = saved
    }

    static func passwordAccount(_ id: UUID) -> String { "vortx.networkshare.\(id.uuidString.lowercased()).password" }

    /// Checks the address, works out whether it is WebDAV or a web folder, and saves the share once its root
    /// actually lists. Nothing is stored for an address that fails.
    func add(name: String, address: String, username: String, password: String) async throws -> NetworkShare {
        guard let root = NetworkSharePolicy.rootURL(address) else { throw NetworkShareError.invalidAddress }
        let user = username.trimmingCharacters(in: .whitespacesAndNewlines)
        let login = NetworkShareClient.Login(username: user, password: user.isEmpty ? "" : password)
        let kind = try await NetworkShareClient.detect(root: root, login: login)
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let share = NetworkShare(id: UUID(), name: trimmedName.isEmpty ? (root.host ?? root.absoluteString) : trimmedName,
                                 kind: kind, url: root.absoluteString, username: user, addedAt: Date())
        if !user.isEmpty {
            guard Keychain.set(password, for: Self.passwordAccount(share.id)) == .success else {
                throw NetworkShareError.credentialStorage
            }
        }
        shares.insert(share, at: 0)
        save()
        return share
    }

    func remove(_ share: NetworkShare) {
        Keychain.set(nil, for: Self.passwordAccount(share.id))
        shares.removeAll { $0.id == share.id }
        save()
    }

    /// The visible contents of `folder` (the share root when nil).
    func list(_ share: NetworkShare, folder: URL? = nil) async throws -> [NetworkShareEntry] {
        guard let root = share.rootURL else { throw NetworkShareError.invalidAddress }
        return try await NetworkShareClient.list(folder ?? root, kind: share.kind, root: root, login: login(for: share))
    }

    /// What the player opens for a file from this share: the final address after the server's own redirects
    /// (followed only within the share), and the login only when that address is inside it.
    func playbackTarget(_ share: NetworkShare, for url: URL) async throws -> (url: URL, headers: [String: String]?) {
        guard let root = share.rootURL else { throw NetworkShareError.invalidAddress }
        let login = login(for: share)
        let final = try await NetworkShareClient.resolveForPlayback(url, root: root, login: login)
        let headers = NetworkSharePolicy.headers(for: final, root: root, username: login.username, password: login.password)
        return (final, headers.isEmpty ? nil : headers)
    }

    private func login(for share: NetworkShare) -> NetworkShareClient.Login {
        guard !share.username.isEmpty else { return .init(username: "", password: "") }
        return .init(username: share.username, password: Keychain.string(Self.passwordAccount(share.id)) ?? "")
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(shares) else { return }
        UserDefaults.standard.set(data, forKey: Self.listKey)
    }
}
