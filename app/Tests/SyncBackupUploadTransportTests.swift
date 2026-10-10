import Foundation
import CoreFoundation

private enum CredentialScopeRegistry {
    struct Capture: Equatable { let generation: Int }
}
@MainActor private final class Authority {
    var generation = 1
    func capture() -> CredentialScopeRegistry.Capture { .init(generation: generation) }
}
@MainActor private enum DiagnosticsLog {
    static var messages: [String] = []
    static func log(_ category: String, _ message: String) { messages.append(message) }
}
private enum VortXSyncCrypto {
    static func sealDocument(dataKey: Data, plaintext: Data, accountId: String, version: Int, writeV2: Bool) -> String? {
        // Encryption is a fixture aperture. The production upload/verdict/ACK method is extracted unchanged.
        plaintext.base64EncodedString()
    }
}
@MainActor private final class UploadHarness {
    struct Account { let id = "synthetic-account" }
    let credentialAuthority = Authority()
    var account: Account? = Account()
    var dataKey: Data? = Data([1])
    static let writeSyncDocV2 = false
    var pendingApply = false
    var backupUploadFailure: SyncBackupUploadFailure?
    var lastSyncedVersion = 9
    var backupCount = 0
    var providerReceiptCount = 0
    var successCount = 0
    var persistenceCount = 0
    var receiptFails = false
    var backupFails = false
    var reply: (Int, [String: Any]?) = (400, ["error": "bad_request"])
    var whileSending: (() -> Void)?
    func isCurrent(_ capture: CredentialScopeRegistry.Capture) -> Bool { capture == credentialAuthority.capture() }
    func hasPendingAccountDocApply(for capture: CredentialScopeRegistry.Capture) -> Bool { pendingApply }
    func request(_ method: String, _ path: String, body: [String: Any], auth: Bool,
                 credentialCapture: CredentialScopeRegistry.Capture) async -> (Int, [String: Any]?) {
        precondition(method == "PUT" && path == "/v1/backup" && auth)
        precondition(body["document"] is String && body["version"] as? Int == 10)
        whileSending?()
        return reply
    }
    func rememberNativeBackup(capture: CredentialScopeRegistry.Capture) -> Bool { backupCount += 1; return !backupFails }
    func acknowledgeNativeProviders(_ document: [String: Any], capture: CredentialScopeRegistry.Capture) throws {
        providerReceiptCount += 1
        if receiptFails { throw NSError(domain: "fixture", code: 1) }
    }
    func persistLastSyncedVersion() { persistenceCount += 1 }
    func stampSyncSuccess() { successCount += 1 }
    static func markSawDocV2(_ accountID: String) { preconditionFailure("v2 writes remain disabled") }
    func send() async -> String {
        switch await pushSyncDocAt(["synthetic": String(repeating: "x", count: 1_267_400)], version: 10) {
        case .accepted: return "accepted"
        case .rejected: return "rejected"
        case .error: return "error"
        }
    }
    /* PRODUCTION_UPLOAD */
}

@main @MainActor private enum UploadTests {
    static var checks = 0
    static func expect(_ condition: @autoclosure () -> Bool, _ label: String) {
        precondition(condition(), label); checks += 1
    }
    static func main() async {
        for (status, code, expected) in [(413, nil, SyncBackupUploadFailure.tooLarge),
                                       (400, "document_too_large", .tooLarge),
                                       (400, "request_too_large", .tooLarge),
                                       (400, "bad_version", .invalidRevision),
                                       (400, "bad_request", .serverRejected),
                                       (400, "secret-token@example.com", .serverRejected),
                                       (401, nil, .authorization), (403, nil, .authorization),
                                       (0, nil, .unavailable), (500, nil, .unavailable)] {
            let harness = UploadHarness()
            harness.reply = (status, code.map { ["error": $0] })
            let outcome = await harness.send()
            expect(outcome == "error" && harness.backupUploadFailure == expected, "classified transport failure")
            expect(harness.lastSyncedVersion == 9 && harness.successCount == 0 && harness.persistenceCount == 0,
                   "failed upload cannot advance revision or last-sync time")
            expect(harness.backupCount == 0 && harness.providerReceiptCount == 0, "failed upload cannot ACK native evidence")
            expect(!DiagnosticsLog.messages.last!.contains("secret-token"), "server text cannot leak through logs")
            expect(DiagnosticsLog.messages.last!.contains("ciphertextBytes="), "transport diagnostic includes only safe size/category")
        }
        let accepted = UploadHarness()
        accepted.backupUploadFailure = .tooLarge
        accepted.reply = (200, ["accepted": true])
        let acceptedOutcome = await accepted.send()
        expect(acceptedOutcome == "accepted" && accepted.backupUploadFailure == nil, "durably accepted retry clears error")
        expect(accepted.lastSyncedVersion == 10 && accepted.successCount == 1 && accepted.persistenceCount == 1,
               "only durable acceptance records sync success")
        expect(accepted.backupCount == 1 && accepted.providerReceiptCount == 1, "accepted payload persists both receipts")

        let conflict = UploadHarness(); conflict.backupUploadFailure = .tooLarge
        conflict.reply = (200, ["accepted": false, "version": 11])
        let rejected = await conflict.send()
        expect(rejected == "rejected" && conflict.lastSyncedVersion == 9 && conflict.backupCount == 0,
               "CAS rejection is not an acknowledgement")
        expect(conflict.backupUploadFailure == .tooLarge, "rejection cannot claim failed backup saved")

        for stage in ["verdict", "backup", "receipt", "retired-owner", "pending-restore"] {
            let h = UploadHarness(); h.backupUploadFailure = .tooLarge
            h.reply = (200, stage == "verdict" ? ["accepted": 1] : ["accepted": true])
            h.backupFails = stage == "backup"; h.receiptFails = stage == "receipt"
            if stage == "retired-owner" { h.whileSending = { h.credentialAuthority.generation += 1 } }
            if stage == "pending-restore" { h.whileSending = { h.pendingApply = true } }
            let outcome = await h.send()
            expect(outcome == "error" && h.lastSyncedVersion == 9 && h.successCount == 0,
                   "unproven local or remote completion retains revision")
            expect(h.backupUploadFailure == .tooLarge, "incomplete acceptance retains failed-save status")
        }
        expect(!DiagnosticsLog.messages.joined().contains("synthetic-account"), "diagnostics contain no account identifiers")
        print("Sync backup production upload checks passed: \(checks)")
    }
}
