# Help and diagnostics

For ordinary bugs, use [GitHub Issues](https://github.com/VortXTV/VortX/issues). For ideas, use [Discussions](https://github.com/VortXTV/VortX/discussions). For security vulnerabilities, use [private vulnerability reporting](https://github.com/VortXTV/VortX/security/advisories/new).

## Profiles will not open, or sync fails

Please keep your saved data intact while collecting evidence:

1. Note the installed **version and build**, device model, OS version and approximate failure time.
2. Capture the exact message and whether it appears during sign-in, profile migration, profile selection or **Sync now**.
3. Export a fresh diagnostic log soon after the failure. A later successful open does not prove that upload/sync completed, so include that change in your report too.
4. If another device still opens, say which one and when its last successful sync occurred. Do not reset it to make the devices match.

**Do not delete the app, reset the account, clear its storage or remove Keychain/encryption items just to get past an error.** A recovery flow cannot restore a key that has already been lost. Never send your password, authentication token, encryption/recovery key or complete backup file in a public issue.

VortX sign-in and the optional Stremio connection are separate. For an import failure, mention whether VortX itself opens, whether the failure is connecting or importing, and which categories fail, such as add-ons or library. No Stremio password or token is needed in the report.

## Export a diagnostic log

The exact labels can differ by platform and version. Current Apple builds expose diagnostic export before normal browsing is available, through sign-in/profile recovery entry points, as well as in Settings.

- **iPhone / iPad:** in Settings, find the advanced player diagnostic/logging controls and use **Save or share log** or the offered export action.
- **Mac:** use **Export diagnostic log** in the diagnostic controls; the app saves/reveals a file in Finder.
- **Apple TV:** open the diagnostic controls in Settings and use QR log export. Scan the displayed code from a device on the same local network while the export is available.
- **Android:** use the diagnostics/log export option in Settings. If the app cannot reach it, report the startup screen and build rather than clearing storage.

If the profile is PIN-protected, its diagnostic access can require that PIN. Do not post the PIN.

Before sharing, inspect the export for private source URLs, account identifiers and credentials. Keep an original privately; share only the minimum redacted evidence needed. A local QR export link is not a public support-upload endpoint. Do not publish the QR code or raw network export.

## Playback, slow controls or repeated recovery

Include:

- Device model, OS, VortX version/build and selected player, if shown.
- Whether the delay is revealing controls, moving focus, selecting a source, seeking or resuming.
- Approximate position/duration and whether it follows Skip Intro, Skip Credits, an episode change or a source switch.
- A fresh diagnostic export and a short reproduction sequence. For Apple TV input delay, a short video can help show the time between the remote action and the controls appearing.
- The source type and relevant format labels, without a private playback URL or provider credentials.

A repeated EOF/recovery failure is not evidence that the episode is finished. A source may end early even when the UI reports a longer duration. Please report whether the resume position was retained and whether the episode was incorrectly marked watched or advanced.

## What a fix status means

- **Source addressed:** a relevant code change exists.
- **Tested:** named checks passed against that source; synthetic checks are not a physical-device result.
- **Released:** verified packages containing it are available on a named release.
- **Confirmed on device:** the reported behavior was checked on the affected device/setup.

These are separate states. A closed source review or a successful build does not establish universal sync convergence, playback compatibility or smooth controls.
