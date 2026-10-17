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
