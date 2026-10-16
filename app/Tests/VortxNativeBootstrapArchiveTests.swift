import Foundation

@main enum VortxNativeBootstrapArchiveTests {
    static func check(_ condition: Bool, line: Int = #line) { precondition(condition, "bootstrap archive at \(line)") }
    static func data(_ object: Any) throws -> Data { try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) }
    static func main() throws {
        let configured = "https://addon.example/CaseSensitive?secret=fixture-only"
        let preferences: [String: Any] = [
            "kcfallback.legacy": "fixture-secret", "future.pref": ["color": "blue", "key": "non-secret-record-id"],
            "stremiox.profiles": try data([["id": "historical-owner", "pin": "sha256:fixture", "future": ["custom": true]]]),
            "nested": try data(["password": "nested-fixture-secret", "retained": 4,
                                 "jsonString": #"{"token":"string-hidden-fixture","unknown":true}"#]),
            "date": Date(timeIntervalSince1970: 42)
        ]
        let plist = try PropertyListSerialization.data(fromPropertyList: preferences, format: .binary, options: 0)
        let backup = try data(["format": "vortx-backup", "schema": 1, "payloadBase64": plist.base64EncodedString(), "futureEnvelope": ["keep": true]])
        let source: [String: Any] = ["settings": backup.base64EncodedString(), "apiKeys": ["provider": "fixture-key"],
                                     "addons": [["transportUrl": configured]], "unknown": ["key": "movie:tt1", "fractionalClock": 1000.125],
                                     "vortx": ["deletedAddonsTs": [configured: ["addedAt": 1000.125]]],
                                     "authKey": "fixture-auth", "nested": ["accessToken": "fixture-access", "safe": ["name": "keep"]]]
        let material = try data(["schemaVersion": 1, "roster": [], "watches": ["owner": [["lastPlayedAtMs": 1000.125]]]])
        let archive = try VortxNativeBootstrapArchive.encode(document: data(source), material: material)
        try VortxNativeBootstrapArchive.validate(archive)
        let object = try JSONSerialization.jsonObject(with: archive) as! [String: Any]
        let host = object["hostDocument"] as! [String: Any]
        check(host["authKey"] == nil && host["apiKeys"] == nil)
        check((host["addons"] as! [[String: Any]])[0]["transportUrl"] as? String == configured)
        check(((host["vortx"] as! [String: Any])["deletedAddonsTs"] as! [String: Any])[configured] != nil)
        check((host["unknown"] as! [String: Any])["key"] as? String == "movie:tt1")
        check((host["unknown"] as! [String: Any])["fractionalClock"] as? Double == 1000.125)
        let safeBackup = try JSONSerialization.jsonObject(with: Data(base64Encoded: host["settings"] as! String)!) as! [String: Any]
        let safeDomain = try PropertyListSerialization.propertyList(from: Data(base64Encoded: safeBackup["payloadBase64"] as! String)!, options: [], format: nil) as! [String: Any]
        check(safeDomain["kcfallback.legacy"] == nil && safeDomain["date"] as? Date == Date(timeIntervalSince1970: 42))
        check((safeBackup["futureEnvelope"] as! [String: Bool])["keep"] == true)
        let nested = try JSONSerialization.jsonObject(with: safeDomain["nested"] as! Data) as! [String: Any]
        check(nested["password"] == nil && nested["retained"] as? Int == 4)
        let nestedString = try JSONSerialization.jsonObject(with: Data((nested["jsonString"] as! String).utf8)) as! [String: Any]
        check(nestedString["token"] == nil && nestedString["unknown"] as? Bool == true)
        check(try data(object["legacyImportMaterial"]!) == material)
        let exclusions = object["excludedCredentialPaths"] as! [String]
        check(exclusions.contains("/settings/payloadBase64/kcfallback.legacy") && exclusions.contains("/nested/accessToken"))
        let genericJSON = try data(["token": "must-be-excluded", "keep": "value", "url": configured]).base64EncodedString()
        let genericPlist = try PropertyListSerialization.data(fromPropertyList: ["password": "must-be-excluded", "date": Date(timeIntervalSince1970: 5)], format: .xml, options: 0).base64EncodedString()
        let nestedBackup = try VortxNativeBootstrapArchive.encode(document: data(["future": ["backup": backup.base64EncodedString(),
                                                                                              "json": genericJSON, "plist": genericPlist]]))
        try VortxNativeBootstrapArchive.validate(nestedBackup)
        let nestedObject = try JSONSerialization.jsonObject(with: nestedBackup) as! [String: Any]
        let future = (nestedObject["hostDocument"] as! [String: Any])["future"] as! [String: String]
        let futureBackup = try JSONSerialization.jsonObject(with: Data(base64Encoded: future["backup"]!)!) as! [String: Any]
        let futureDomain = try PropertyListSerialization.propertyList(from: Data(base64Encoded: futureBackup["payloadBase64"] as! String)!, options: [], format: nil) as! [String: Any]
        check(futureDomain["kcfallback.legacy"] == nil && futureDomain["date"] as? Date == Date(timeIntervalSince1970: 42))
        let futureJSON = try JSONSerialization.jsonObject(with: Data(base64Encoded: future["json"]!)!) as! [String: String]
        check(futureJSON["token"] == nil && futureJSON["keep"] == "value" && futureJSON["url"] == configured)
        let futurePlist = try PropertyListSerialization.propertyList(from: Data(base64Encoded: future["plist"]!)!, options: [], format: nil) as! [String: Any]
        check(futurePlist["password"] == nil && futurePlist["date"] as? Date == Date(timeIntervalSince1970: 5))
        check((nestedObject["excludedCredentialPaths"] as! [String]).contains("/future/backup/payloadBase64/kcfallback.legacy"))
        let innerJSON = #"{"token":"quoted-hidden-fixture","unknown":true}"#
        let quotedJSON = String(decoding: try JSONSerialization.data(withJSONObject: innerJSON, options: [.fragmentsAllowed]), as: UTF8.self)
        let quotedArchive = try JSONSerialization.jsonObject(with: VortxNativeBootstrapArchive.encode(document: data(["future": quotedJSON]))) as! [String: Any]
        let sanitizedQuoted = (quotedArchive["hostDocument"] as! [String: String])["future"]!
        let sanitizedInner = try JSONSerialization.jsonObject(with: Data(sanitizedQuoted.utf8), options: [.fragmentsAllowed]) as! String
        let sanitizedObject = try JSONSerialization.jsonObject(with: Data(sanitizedInner.utf8)) as! [String: Any]
        check(sanitizedObject["token"] == nil && sanitizedObject["unknown"] as? Bool == true)
        check((quotedArchive["excludedCredentialPaths"] as! [String]).contains("/future/token"))
        let unchanged = try data(["keep": true]).base64EncodedString()
        let hashThatLooksStructured = "e9" + String(repeating: "0", count: 62)
        let evidence = try data(["sourceDocumentSha256": hashThatLooksStructured, "typedCarrierFingerprint": hashThatLooksStructured,
            "nativeSync": ["legacyImport": ["acceptedFingerprints": [hashThatLooksStructured], "ownAccountSourceHistory": [
                "00000000-0000-0000-0000-00000000A11C": [hashThatLooksStructured: hashThatLooksStructured]]]]])
        let hashArchive = try VortxNativeBootstrapArchive.encode(document: evidence)
        try VortxNativeBootstrapArchive.validate(hashArchive)
        try ownAccountSlotSourceHistory()
        try archiveInspectionDiagnostics()
        let unchangedArchive = try JSONSerialization.jsonObject(with: VortxNativeBootstrapArchive.encode(document: data(["future": unchanged]))) as! [String: Any]
        check((unchangedArchive["hostDocument"] as! [String: String])["future"] == unchanged)
        let malformedBackup = try data(["format": "vortx-backup", "schema": 1, "payloadBase64": "opaque"]).base64EncodedString()
        do { _ = try VortxNativeBootstrapArchive.encode(document: data(["future": malformedBackup])); fatalError("nested malformed backup was archived") } catch {}
        for malformed in [Data(#"{"token":"unfinished"# .utf8), Data("bplist00broken".utf8)] {
            do { _ = try VortxNativeBootstrapArchive.encode(document: data(["future": malformed.base64EncodedString()])); fatalError("recognizable malformed container was archived") } catch {}
        }
        var deep: [String: Any] = ["keep": true]
        for _ in 0..<70 { deep = ["nested": deep] }
        do { _ = try VortxNativeBootstrapArchive.encode(document: data(deep)); fatalError("unbounded archive recursion") } catch {}
        for source: [String: Any] in [["futureSecretBox": "opaque"], ["settings": "not-a-backup"], ["custom_token": "unknown"]] {
            do { _ = try VortxNativeBootstrapArchive.encode(document: data(source)); fatalError("ambiguous source was archived") } catch {}
        }
        let opaque = try PropertyListSerialization.data(fromPropertyList: ["unknown": Data([0, 255, 0])], format: .binary, options: 0)
        let opaqueBackup = try data(["format": "vortx-backup", "schema": 1, "payloadBase64": opaque.base64EncodedString()])
        do { _ = try VortxNativeBootstrapArchive.encode(document: data(["settings": opaqueBackup.base64EncodedString()])); fatalError("opaque preference was archived") } catch {}
        do { _ = try VortxNativeBootstrapArchive.encode(document: data([:]), material: data(["schemaVersion": 1, "password": "not-allowed"])); fatalError("typed input was silently sanitized") } catch {}
        var injected = object; injected["password"] = "not-allowed-at-archive-root"
        do { try VortxNativeBootstrapArchive.validate(data(injected)); fatalError("unknown archive root persisted") } catch {}
        try membershipReceiptRetention()
        print("Native bootstrap archive: exact fractional material, full noncredential settings/unknown fields, nested credential exclusions and ambiguity fences passed")
    }

    static func ownAccountSlotSourceHistory() throws {
        // Exact schema-4 slot identifiers and source fingerprint from the website carrier fixture.
        // A typed SHA-256 fingerprint can decode to a leading brace under base64 probing.
        let profile = "00000000-0000-0000-0000-00000000B22C"
        let slot = "own:86d5e2a3d69627f41d23a87c36f06a5351a97392d06e62b9fc1e429889ea5a47"
        let sourceDigest = "7b04ac2853c664a15255369c5424b2099df38715a43d56b80feb9edd1824d7e5"
        let fingerprints = ["e563698769b687e82770b9150d5b655c0c4b7e3cbee814d75f58b512157336d7",
                            "e9" + String(repeating: "0", count: 62)]
        for fingerprint in fingerprints {
            let document: [String: Any] = ["nativeSync": ["schemaVersion": 4, "accountSlots": [profile: ["slots": [slot: [
                "sourceHistory": [sourceDigest: fingerprint], "future": ["token": "fixture-hidden", "keep": true]
            ]]]]]]
            let clean: Data
            do { clean = try VortxNativeBootstrapArchive.credentialFreeDocument(document) }
            catch { preconditionFailure("Valid native own-account slot sourceHistory fingerprint rejected: \(error)") }
            func checkHistory(_ host: [String: Any]) {
                let native = host["nativeSync"] as! [String: Any]
                let accounts = native["accountSlots"] as! [String: Any]
                let slots = (accounts[profile] as! [String: Any])["slots"] as! [String: Any]
                let account = slots[slot] as! [String: Any]
                check((account["sourceHistory"] as! [String: String])[sourceDigest] == fingerprint)
                let future = account["future"] as! [String: Any]
                check(future["token"] == nil && future["keep"] as? Bool == true)
            }
            checkHistory(try JSONSerialization.jsonObject(with: clean) as! [String: Any])
            let archive = try VortxNativeBootstrapArchive.encode(document: data(document))
            try VortxNativeBootstrapArchive.validate(archive)
            checkHistory((try JSONSerialization.jsonObject(with: archive) as! [String: Any])["hostDocument"] as! [String: Any])
            let retained = try VortxNativeBootstrapArchive.encode(document: data([:]), authenticatedSourceArchive: archive)
            try VortxNativeBootstrapArchive.validate(retained)
            checkHistory((try JSONSerialization.jsonObject(with: retained) as! [String: Any])["authenticatedSourceArchive"] as! [String: Any])
            // The protocol exemption must not make an arbitrary digest-shaped carrier opaque.
            do {
                _ = try VortxNativeBootstrapArchive.credentialFreeDocument(["future": ["sourceHistory": [sourceDigest: fingerprint]]])
                preconditionFailure("Nonprotocol sourceHistory fingerprint skipped credential inspection")
            } catch VortxNativeBootstrapArchive.Failure.opaquePreference {}
        }
    }

    static func archiveInspectionDiagnostics() throws {
        var messages: [String] = []
        _ = try VortxNativeBootstrapArchive.credentialFreeDocument(["library": [["name": "Fixture title"]]],
                                                                 diagnostics: { messages.append($0) })
        check(messages.isEmpty)

        func rejected(_ source: [String: Any], field: String) throws {
            messages = []
            do {
                _ = try VortxNativeBootstrapArchive.credentialFreeDocument(source, diagnostics: { messages.append($0) })
                preconditionFailure("Opaque source unexpectedly accepted")
            } catch VortxNativeBootstrapArchive.Failure.opaquePreference {}
            check(messages == ["archive_inspection result=failed reason=opaque_preference field=" + field])
        }
        try rejected(["settings": "fixture-private-malformed-settings"], field: "/settings")

        let privateKey = "https://private-user.example/Personal/manifest.json?key=fixture-private-value"
        let preferences = try PropertyListSerialization.data(fromPropertyList: [privateKey: Data([0, 255, 0])], format: .binary, options: 0)
        let backup = try data(["format": "vortx-backup", "schema": 1, "payloadBase64": preferences.base64EncodedString()])
        try rejected(["settings": backup.base64EncodedString()], field: "/settings/payloadBase64/*")

        let profile = "00000000-0000-0000-0000-00000000B22C"
        let sourceDigest = String(repeating: "a", count: 64)
        try rejected(["nativeSync": ["accountSlots": [profile: ["slots": [privateKey: [
            "sourceHistory": [sourceDigest: Data([0, 255, 0])]
        ]]]]]], field: "/nativeSync/accountSlots/*/slots/*/sourceHistory/*")
        try rejected(["private-personal-field": ["sourceHistory": [sourceDigest: "e9" + String(repeating: "0", count: 62)]]],
                     field: "/*/sourceHistory/*")
    }

    static func membershipReceiptRetention() throws {
        let scope = "account.fixture"
        let owner = "00000000-0000-0000-0000-00000000A11C"
        let digest = String(repeating: "a", count: 64)
        let receipt: [String: Any] = ["profileId": owner, "kind": "addon_install",
            "identity": "https://addon.example/retired/manifest.json", "sourceField": "/vortx/deletedAddonsTs",
            "receipt": ["deletedAddonsTs": ["https://addon.example/retired/manifest.json": ["addedAt": 1000.125, "removedAt": 0]],
                        "deletedAddons": [], "webAddonRemovals": []], "sourceDocumentSha256": digest]
        let source = try VortxNativeBootstrapArchive.encode(document: data(["ownAccountSources": [:]]))
        let archive = try VortxLegacyMembershipReceiptArchive.appending(data([receipt]), to: source,
            scope: scope, ownerProfileID: owner, sourceDocumentSHA256: digest)
        try VortxNativeBootstrapArchive.validate(archive)
        let host = (try JSONSerialization.jsonObject(with: archive) as! [String: Any])["hostDocument"] as! [String: Any]
        let pending = host[VortxLegacyMembershipReceiptArchive.key] as! [String: Any]
        let initial = try data(pending)
        var next = receipt; next["sourceDocumentSha256"] = String(repeating: "b", count: 64)
        let refreshed: [String: Any] = ["schemaVersion": 1, "scope": scope, "ownerProfileId": owner, "receipts": [next]]
        let merged = try VortxLegacyMembershipReceiptArchive.merging(initial, with: data(refreshed), scope: scope, ownerProfileID: owner)!
        let retained = (try JSONSerialization.jsonObject(with: merged) as! [String: Any])["receipts"] as! [[String: Any]]
        check(retained.count == 1 && retained[0]["sourceDocumentSha256"] as? String == digest)
        check((((retained[0]["receipt"] as! [String: Any])["deletedAddonsTs"] as! [String: Any])[receipt["identity"] as! String] as! [String: Any])["addedAt"] as? Double == 1000.125)
        next["receipt"] = ["deletedAddonsTs": [receipt["identity"] as! String: ["addedAt": 1000.875, "removedAt": 0]],
                           "deletedAddons": [], "webAddonRemovals": []]
        let changed: [String: Any] = ["schemaVersion": 1, "scope": scope, "ownerProfileId": owner, "receipts": [next]]
        let history = try VortxLegacyMembershipReceiptArchive.merging(merged, with: data(changed), scope: scope, ownerProfileID: owner)!
        check(((try JSONSerialization.jsonObject(with: history) as! [String: Any])["receipts"] as! [Any]).count == 2)
        check(try VortxLegacyMembershipReceiptArchive.merging(history, with: nil, scope: scope, ownerProfileID: owner) == history)
        do { _ = try VortxLegacyMembershipReceiptArchive.merging(history, with: nil, scope: "account.other", ownerProfileID: owner); fatalError("foreign scope accepted") } catch {}
        do { _ = try VortxLegacyMembershipReceiptArchive.appending(data([receipt]), to: source, scope: scope, ownerProfileID: owner,
                sourceDocumentSHA256: String(repeating: "c", count: 64)); fatalError("wrong source accepted") } catch {}
        var credential = receipt; credential["receipt"] = ["password": "fixture-only"]
        do { _ = try VortxLegacyMembershipReceiptArchive.appending(data([credential]), to: source, scope: scope, ownerProfileID: owner,
                sourceDocumentSHA256: digest); fatalError("credential carrier accepted") } catch {}
        var unknown = receipt; unknown["kind"] = "ignore_everything"
        let invalid: [String: Any] = ["schemaVersion": 1, "scope": scope, "ownerProfileId": owner, "receipts": [unknown]]
        do { _ = try VortxLegacyMembershipReceiptArchive.merging(nil, with: data(invalid), scope: scope, ownerProfileID: owner); fatalError("unknown pending kind accepted") } catch {}
        var malformedKind = receipt; malformedKind["sourceField"] = "/unrelated/path"
        var noSources = receipt; noSources["kind"] = "watch_identity_conflict"
        noSources["sourceField"] = "/vortx/byProfile/\(owner)/watch_identity_conflicts"; noSources["receipt"] = [:] as [String: Any]
        var wrongProfile = receipt; wrongProfile["kind"] = "profile_saved_overlay"
        wrongProfile["identity"] = "title"; wrongProfile["sourceField"] = "/vortx/byProfile/00000000-0000-0000-0000-00000000ABCD/library/0"
        wrongProfile["receipt"] = ["id": "title", "type": "series"]
        var wrongIdentity = wrongProfile; wrongIdentity["sourceField"] = "/vortx/byProfile/\(owner)/library/0"
        wrongIdentity["receipt"] = ["id": "different", "type": "series"]
        for malformed in [malformedKind, noSources, wrongProfile, wrongIdentity] {
            let candidate: [String: Any] = ["schemaVersion": 1, "scope": scope, "ownerProfileId": owner, "receipts": [malformed]]
            do { _ = try VortxLegacyMembershipReceiptArchive.merging(nil, with: data(candidate), scope: scope, ownerProfileID: owner); fatalError("malformed known kind accepted") } catch {}
        }
        let excessive: [String: Any] = ["schemaVersion": 1, "scope": scope, "ownerProfileId": owner, "receipts": Array(repeating: receipt, count: 10_001)]
        do { _ = try VortxLegacyMembershipReceiptArchive.merging(nil, with: data(excessive), scope: scope, ownerProfileID: owner); fatalError("unbounded journal accepted") } catch {}
        print("Pending membership receipts: exact values, account binding, no resurrection, bounded idempotent retention and credential fences passed")
    }
}
