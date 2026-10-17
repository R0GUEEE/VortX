# Watched migration evidence repair — 17 October 2026

Source-only repair. No app/SDK build, installation, device launch, playback,
real-account operation, checkpoint rewrite or release was performed.

## Changed behavior

Migration receipts now share an exact digest-addressed source pool instead of
repeating the entire source in each stored receipt. Existing inline receipts
remain readable. Historical retry expands one pending tuple and matching
evidence at a time; current preparation uses an indexed lazy lookup. Ambiguous
evidence rejects rather than selecting an arbitrary receipt. Unknown host and
legacy wrapper fields remain preserved across the three audited appenders.

Every original pending snapshot remains recoverable. Newly fetched evidence is
checked against the remaining processing budget before accumulation. Cancellation,
account ownership and exact source/row binding remain enforced.

## Evidence and limits

The actual-source standalone archive test and existing migration/bootstrap and
producer boundary tests pass, with independent review of the final composition.
A 24-row fixture with a 1,050,720-byte source previously repeated approximately
33.65 MB of evidence; the compact archive is 1,444,315 bytes and retains the exact
source and offline replay evidence. Adversarial small-wire journals are rejected
before replay/provider work, and an oversized new result stops before a second
distinct fetch.

The policy is deliberately bounded:

- New writes and pooled/mixed reads have a 32 MiB encoded combined journal cap.
- Legacy inline-only reads retain compatibility under the separate 96 MiB
  expanded processing budget. This is not a universal 32 MiB read cap.
- The 96 MiB processing budget is not a measured process-memory ceiling. Large
  legitimate histories may still require a later recovery design; the error
  preserves the original data and asks for support/update, not reset or deletion.
- Fresh preparation can still accumulate output receipts before retention.
  This change does not close that separate transient-memory issue.
- Older clients are not certified to process the new compact migration format.
  Full device-memory/performance and all host-state persistence sinks remain
  outside the verified scope.

Standalone regression: `bash scripts/test-native-watched-archive.sh`.

## 18 October cooperative producer successor

The next source milestone replaces fresh receipt-array accumulation with a bounded
stream and durable draft checkpoints. Construction is cheap; an explicit recovery
worker owns journal loading, strict validation, indexing, compaction and cooperative
replay. Main-actor callers do not obtain that isolation merely by writing `await`.
The encrypted draft reader releases file locks before cooperative validation. The
session protocol exposes the asynchronous read as a real requirement/witness, with
a validating worker fallback for generic stores.

Draft installation retains exact expected-byte CAS, account/source attribution and
current-authority/cancellation checks. A crash after a durable checkpoint resumes the
remaining tuples; original unresolved snapshots and unknown envelope fields remain
recoverable. Progress checkpoints are not import, cloud or authentication ACKs.
Existing limits remain: 32 MiB encoded journal, 96 MiB eager compatibility/batch work,
64 receipts per uncheckpointed batch, and 48 MiB retained draft-archive admission.
They are finite format/work limits, not a process-memory or device-latency guarantee.

The actual-source watched/session fixtures cover checkpoint/reopen, exact source
retention, cancelled/revoked work, stale CAS, generic/existential dispatch and closure.
Final composed-stack gates are retained by the serial phase-A runner. The session's
final leased replay and repeated whole-history work remain a separate Phase B repair;
one indivisible decode/hash/encode/file operation is not made preemptible by these
cooperative item boundaries. Whole-app/UI typechecking and physical performance remain
unverified. No SDK/application package or real account is exercised by these fixtures.

## 18 October final-session replay slice

The concrete encrypted-store path now completes strict journal replay on the recovery
worker before acquiring the session's final write lease. An in-process immutable
validated-draft witness binds the original envelope bytes to exact authenticated
account/owner bytes. It is not current write authority or a durable acknowledgement.
After suspension the session rechecks closure, checkpoint certainty and its original
lease; the unchanged storage sink still checks live source authority, exact expected
plaintext bytes, encryption, durable installation and readback. No lock spans the await.

The new writer is a protocol requirement with a concrete witness, not an extension-only
optimization. Older generic stores still delegate to their original strict writer and
may repeat validation under the lease. The existing synchronous/cooperative readers and
writers remain available; no saved-data schema or admission limit changes.

Actual-source session fixtures pass for existential and generic dispatch, exact-byte
and Unicode-distinct scope binding, caller Data-copy conservation, cancellation, source
retirement, session close/reopen, checkpoint uncertainty, stale expected-byte conflict
and lost acknowledgement. Their main-actor/session-read sentinel is source-bound to a
particular cooperative replay boundary, not a wall-clock latency or future control-flow
guarantee. Tests use a synthetic ABI/key and disposable encrypted files, not a real
account or actual disk-failure injection.

This closes only the concrete final leased full-replay slice. Every offered draft still
receives a complete cooperative validation; other overlapping history passes remain.
The final sink still synchronously reads, decrypts, compares, encrypts, writes, fsyncs
and reads back. Indivisible parsing/hashing/serialization, CPU/RSS/UI-latency measurement,
generic fallback cost, whole-app/SDK compilation and device performance remain open.
