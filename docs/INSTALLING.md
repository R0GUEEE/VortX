# Install and update VortX

Download only from the [official VortX releases](https://github.com/VortXTV/VortX/releases). For a particular beta, open its named release page and use the assets on that page. A website link, update feed or download redirect can follow a different channel.

## Pick the right package

| Device | Download | Requirements |
| --- | --- | --- |
| iPhone / iPad | `VortX-iOS-<tag>-ci.ipa` | iOS / iPadOS 16+, compatible sideload signing |
| Apple TV | `VortX-tvOS-<tag>-ci.ipa` | tvOS 18+, compatible sideload signing |
| Apple TV Lite | `VortX-tvOS-lite-<tag>-ci.ipa` | tvOS 18+, direct/resolved sources only, no Top Shelf |
| Mac | `VortX-macOS-<tag>-ci.dmg` | Apple silicon, macOS 14+ |
| Android | `VortX-<version>-full-mpv-universal.apk` | Android 8.0+, MPV variant |
| Android Media3 | `VortX-<version>-play-media3-universal.apk` | Android 8.0+, Media3-only variant |

Names describe the current package layout; older releases can use different names. Both Android APKs include phone/tablet and Android TV interfaces. An `.aab` is for a distribution workflow, not direct installation. See [Release artifacts](RELEASE-ARTIFACTS.md).

## iPhone, iPad and Apple TV

The release IPAs are unsigned and require compatible sideloading/signing. Choose a tool that supports your device and follow its current instructions. Common routes include [Sideloadly](https://sideloadly.io/), [AltStore](https://altstore.io/), [SideStore](https://sidestore.io/) and signing services such as [Signulous](https://www.signulous.com/). They are independent services, not VortX support channels; device support, renewal requirements and terms vary.

1. Download the iOS IPA for an iPhone/iPad, or the appropriate tvOS IPA for Apple TV.
2. Sign and install it using your chosen compatible tool. Follow that tool's device-pairing and trust instructions.
3. Open VortX and sign into your VortX account, or use the available local setup.

Full TV includes embedded native streaming transport and Top Shelf. Lite enforces Direct Links Only and has neither. They use different app identities; moving between them is not an in-place upgrade.

Re-signing can change access to Keychain/app-group data and extensions. Keep the same app variant and signing identity for updates where possible. No signing method guarantees that every credential or device-local setting will survive an identity change. Export a backup first if the current app can open, and do not uninstall simply to update.

## Mac

1. Open the official `.dmg` and drag **VortX** into **Applications**.
2. Run the copy in Applications, not the app inside the mounted disk image.
3. If macOS blocks this official download, use **System Settings > Privacy & Security > Open Anyway** when offered, or remove its quarantine flag:

```sh
xattr -dr com.apple.quarantine /Applications/VortX.app
```

Run that command only for the official VortX app you intentionally downloaded. It removes a download quarantine flag; it does not notarize the app, repair its signature or prove it safe. Current Mac packages are ad-hoc signed, not Developer ID notarized. Do not disable system-wide security protections.

Replace the app in Applications when updating. A changed ad-hoc signature can cause macOS to ask permission to access saved VortX Keychain items. Approve only a prompt you expect that names VortX. Repeated prompts or denied access should be reported with the version/build; **do not delete Keychain items as a troubleshooting shortcut**.

## Android phone, tablet and TV

1. Download the APK variant you intend to use. Full MPV includes the native MPV player; Media3 omits it.
2. Open the APK and allow installation from that browser/file manager if Android asks.
3. Open VortX. Both APK variants contain the phone and TV interfaces.

To update, install the newer APK over the existing app, keeping the same variant and release signer. If Android rejects the update, record the exact message rather than uninstalling and losing local data. `dl.vortx.tv` is a convenience route, not a guarantee that it points to a particular beta.

## After installation

- Check the version/build in Settings and compare it with the release page.
- A local save is not the same as completed account sync. Check **VortX account & sync** if data does not appear on another device.
- Optional **Integrations > Stremio** is separate from the VortX sign-in.
- If profiles cannot open, export diagnostics from the sign-in/profile recovery screen where available. See [Help and diagnostics](SUPPORT.md) before resetting or reinstalling anything.
