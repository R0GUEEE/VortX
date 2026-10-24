import Foundation

/// Compile with the generated ACTUAL-source fixture, not a separately modeled coordinator.
/// Maintained regression for actual subtitle mount owner coordination.
@main
struct SubtitleMountOwnerTests {
    @MainActor
    static func main() async {
        var checks = 0
        func check(_ value: Bool, _ name: String) {
            precondition(value, name); checks += 1; print("PASS \(name)")
        }
        let (host, target, facade) = CoreBridge.fixture()
        guard let original = host.captureNativeSubtitleOwner(target),
              let effect = CredentialPublicationOutbox.tryBeginSubtitleEffect(original) else {
            fatalError("original accepted owner must capture and enter")
        }
        check(CredentialPublicationOutbox.beginMutation() == nil, "effect excludes credential mutation")
        check(CredentialPublicationOutbox.acquireBoundary() != .acquired, "effect joins actual bind boundary drain")
        check(CredentialPublicationOutbox.tryBeginSubtitleEffect(original) == nil, "effect is exclusive")
        var reads = 0, writes = 0
        let dispatch = CredentialPublicationOutbox.beginDispatch(sessionID: "synthetic", account: "synthetic",
            read: { _ in reads += 1; return .value("pending:synthetic") },
            write: { _, _ in writes += 1; return .success })
        check(dispatch == nil && reads == 0 && writes == 0, "effect rejects auth dispatch before external storage callbacks")

        let profileRetirement = CredentialPublicationOutbox.beginSubtitleRetirement(domain: facade.sourceID)
        check(CredentialPublicationOutbox.tryBeginSubtitleEffect(original) == nil, "profile-first denies old receipt")
        check(!CredentialPublicationOutbox.finishSubtitleRetirement(profileRetirement), "cannot end retirement before effect")
        let waiter = Task {
            await CredentialPublicationOutbox.waitForSubtitleRetirement(profileRetirement)
        }
        for _ in 0..<512 {
            if CredentialPublicationOutbox.fixtureDrainWaiterCount() == 1 { break }
            await Task.yield()
        }
        check(CredentialPublicationOutbox.fixtureDrainWaiterCount() == 1, "retirement suspended without blocking actor")
        check(host.captureNativeSubtitleOwner(target) == nil, "pending profile retirement prevents fresh capture")
        CredentialPublicationOutbox.endSubtitleEffect(effect)
        await waiter.value
        check(CredentialPublicationOutbox.finishSubtitleRetirement(profileRetirement), "profile writer finishes after drain")
        check(CredentialPublicationOutbox.tryBeginSubtitleEffect(original) == nil, "no old job revival after drain")
        guard let next = host.captureNativeSubtitleOwner(target),
              let nextEffect = CredentialPublicationOutbox.tryBeginSubtitleEffect(next) else { fatalError("new initiating owner capture") }
        check(next != original, "fresh writer registration is not original receipt")
        CredentialPublicationOutbox.endSubtitleEffect(effect)
        check(CredentialPublicationOutbox.beginMutation() == nil, "stale end cannot release newer effect")
        CredentialPublicationOutbox.endSubtitleEffect(nextEffect)

        guard let mutation1 = CredentialPublicationOutbox.beginMutation(),
              let mutation2 = CredentialPublicationOutbox.beginMutation() else { fatalError("ordinary concurrent mutations") }
        check(mutation1 != mutation2, "ordinary mutation-vs-mutation concurrency retained")
        check(CredentialPublicationOutbox.tryBeginSubtitleEffect(next) == nil, "mutation-first excludes subtitle effect")
        CredentialPublicationOutbox.endMutation(mutation1)
        check(CredentialPublicationOutbox.tryBeginSubtitleEffect(next) == nil, "second ordinary mutation still owns drain")
        CredentialPublicationOutbox.endMutation(mutation2)
        guard let lease = CredentialPublicationOutbox.tryBeginSubtitleEffect(next) else { fatalError("settled same owner") }
        host.retireActualBridgeLifetime()
        check(CredentialPublicationOutbox.tryBeginSubtitleEffect(next) == nil, "actual Bridge lifetime retire denies original")
        CredentialPublicationOutbox.endSubtitleEffect(lease)
        for _ in 0..<512 {
            if host.captureNativeSubtitleOwner(target) != nil { break }
            await Task.yield()
        }
        check(CredentialPublicationOutbox.tryBeginSubtitleEffect(next) == nil, "Bridge retirement cannot restore old ID")

        guard let cancellationOwner = host.captureNativeSubtitleOwner(target),
              let cancellationEffect = CredentialPublicationOutbox.tryBeginSubtitleEffect(cancellationOwner) else {
            fatalError("fresh initiating scope after original Bridge retire")
        }
        let cancelledRetirement = CredentialPublicationOutbox.beginSubtitleRetirement(domain: facade.sourceID)
        let secondRetirement = CredentialPublicationOutbox.beginSubtitleRetirement(domain: facade.sourceID)
        let cancelledWaiter = Task { await CredentialPublicationOutbox.waitForSubtitleRetirement(cancelledRetirement) }
        let otherWaiter = Task { await CredentialPublicationOutbox.waitForSubtitleRetirement(secondRetirement) }
        cancelledWaiter.cancel()
        for _ in 0..<512 {
            if CredentialPublicationOutbox.fixtureDrainWaiterCount() == 2 { break }
            await Task.yield()
        }
        check(CredentialPublicationOutbox.fixtureDrainWaiterCount() == 2, "cancelled waiter still drains its admitted predecessor")
        check(CredentialPublicationOutbox.beginMutation() == nil, "waiter cancellation cannot release active native effect")
        check(!CredentialPublicationOutbox.finishSubtitleRetirement(cancelledRetirement), "cancelled writer cannot finish before drain")
        CredentialPublicationOutbox.endSubtitleEffect(cancellationEffect)
        await cancelledWaiter.value; await otherWaiter.value
        check(CredentialPublicationOutbox.finishSubtitleRetirement(cancelledRetirement), "cancelled writer retires after predecessor finishes")
        check(host.captureNativeSubtitleOwner(target) == nil, "second pending retirement still excludes new owner")
        check(CredentialPublicationOutbox.finishSubtitleRetirement(secondRetirement), "independent retirement ticket finishes exactly itself")
        check(!CredentialPublicationOutbox.finishSubtitleRetirement(secondRetirement), "retirement double-finish is rejected")

        let (secondHost, secondTarget, secondFacade) = CoreBridge.fixture()
        check(secondHost.captureNativeSubtitleOwner(secondTarget) != nil, "independent accepted installation enrolls")
        secondFacade.lock.withLock { secondFacade.pendingProfileTransitions = 1 }
        check(secondHost.captureNativeSubtitleOwner(secondTarget) == nil, "actual Facade capture rejects pending profile")
        secondFacade.lock.withLock { secondFacade.pendingProfileTransitions = 0; secondFacade.closed = true }
        check(secondHost.captureNativeSubtitleOwner(secondTarget) == nil, "actual Facade capture rejects closed source")

        guard let accountBefore = CredentialScopeRegistry.shared.tryBind(.account(UUID())) else { fatalError("actual account boundary") }
        check(CredentialPublicationOutbox.tryBeginSubtitleEffect(next) == nil, "actual registry boundary retires previous account")
        check(CredentialScopeRegistry.shared.isCurrent(accountBefore), "actual registry accepted new account")
        guard let rebound = CredentialScopeRegistry.shared.tryBind(accountBefore.scope) else { fatalError("same-account rebind") }
        check(rebound != accountBefore && !CredentialScopeRegistry.shared.isCurrent(accountBefore), "same-account ABA has a fresh actual registry generation")
        print("ALL PASS \(checks)")
    }
}
