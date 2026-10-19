# VortX roadmap

This page separates what is available from what still needs work. It is a direction, not a promise of dates or version numbers. Released changes belong in the [changelog](CHANGELOG.md); an implemented source change is not automatically a published or device-verified fix.

## Available now

- Native Apple TV, iPhone, iPad and Apple-silicon Mac packages.
- Android phone, tablet and TV packages, still a technical preview.
- VortX accounts with encrypted sync, profiles, optional Stremio import and Trakt/SIMKL integrations.
- Cinematic browsing, Continue Watching, episode progress, catalog management, ranked sources and quality choices.
- Per-profile interests, For You rows and smart collections. Basic Home/catalog hiding and reordering already exist.
- Debrid, supported torrent and Usenet paths, personal media servers, network shares and bring-your-own live channels.
- Backup/restore, library import/export and diagnostic export, including recovery entry points before normal browsing is available.

These features do not imply identical behavior on every platform, provider or playback route. See [package differences](docs/RELEASE-ARTIFACTS.md).

## Next priorities

### 1. Account recovery and saved-data safety

Continue closing the reported profile/import failures, including [#262](https://github.com/VortXTV/VortX/issues/262) and [#265](https://github.com/VortXTV/VortX/issues/265), without discarding histories, pending edits, unknown legacy fields or encryption material. Finish the remaining household key-delivery and backup-ownership work with coordinated client/server validation.

Recovery must distinguish missing data from data that cannot be read. A loading or migration state should explain what is happening, retry safely and open the chosen profile when ready. It must not turn a failed read into an empty replacement account.

### 2. Player responsiveness and reliable recovery

Prioritize Apple TV control reveal and remote response, repeated native track work, visible-row updates and playback-queue contention. Continue the Mac seek/skip-credits EOF investigation without falsely marking unfinished episodes watched or advancing past them.

The 0.5.6 beta 2 recovery cap and reduced Up Next work are scoped improvements, not the end of the performance effort. Measure response time and smoothness on real hardware alongside synthetic tests.

### 3. Apple integration and Swift 6

Complete the first-party Swift 6 migration across iOS, Mac, Full TV, Lite TV, helpers and their callers. Some isolated units and targets have migrated; the main apps are not all on Swift 6 yet.

Finish actor ownership, storage and queue integration against the actual SDK and app composition. Compiler-mode checks on a standalone unit do not establish that the entire app has migrated.

### 4. Shared UI and maintainable code

Consolidate repeated controls, appearance rules and presentation logic so a shared change reaches all intended surfaces. Keep platform-specific focus, accessibility and player lifecycle behavior where it belongs.

Continue checking tint coverage, glass eligibility, Effects Off behavior, navigation backgrounds and disabled states. Avoid a cleanup that merely moves duplication or suppresses concurrency warnings.

### 5. Large libraries and engine efficiency

Reduce repeated history evidence, unnecessary full-state serialization and oversized deltas without trimming users' saved histories. Use bounded work, explicit ownership and measured allocation/latency checks. Complete the remaining database/service audit coverage; sampled or unread areas stay open.

### 6. Release reliability and queued reports

Correct the release dispatch-receipt producer and complete future release preflight before starting another packaging run. Preserve protected tags and original evidence rather than relabeling old builds.

[#267](https://github.com/VortXTV/VortX/issues/267) is queued for the next repair pass. It is not marked diagnosed or fixed. The 0.5.6 beta 2 publication does not close the wider audit, Swift 6 or performance work.

## After the reliability work

- Deeper rule-based collections and a more flexible dashboard, extending the existing interests and For You features.
- Better source and metadata controls, accessible navigation and clearer provider failures.
- Broader platform coverage, desktop packaging and physical-device QA for the webOS/Tizen projects.
- Further live-TV/IPTV expansion after the higher-priority reliability and playback work. Existing playlist/guide support is not a promise of recording, timeshift or multiview.

## Suggest or report

Use [Discussions](https://github.com/VortXTV/VortX/discussions) for ideas and [Issues](https://github.com/VortXTV/VortX/issues) for bugs. For a failure, include the installed version/build, device, reproduction steps and a carefully reviewed diagnostic export. See [Help and diagnostics](docs/SUPPORT.md).
