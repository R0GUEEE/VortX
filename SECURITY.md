# Security policy

## Supported versions

Use the latest applicable official release and identify its exact version/build when reporting a problem. A newer beta on GitHub is not necessarily on every update feed or download redirect. Sideloading/signing renewal and in-app update notices depend on the platform and channel; do not assume an installed copy updated automatically.

## Reporting a vulnerability

Report privately through [GitHub's private vulnerability reporting](https://github.com/VortXTV/VortX/security/advisories/new), rather than opening a public issue, especially for anything touching:

- Native streaming transport, network shares or localhost interfaces.
- Account authentication, encryption keys, backup ownership, profile isolation or Keychain access.
- Add-on/provider URL handling, private source links or diagnostic exports.
- Release workflows, signer validation, provenance, update feeds or download routing.

Include the affected version/build, a minimal reproduction and the impact without posting live credentials or other people's data. Do not upload an account backup or unredacted network/log archive. Ordinary playback and UI problems belong in [Issues](https://github.com/VortXTV/VortX/issues), with the precautions in [Help and diagnostics](docs/SUPPORT.md).

Verified reports can be credited in release notes unless you prefer otherwise. Please allow time for investigation and a coordinated fix before public disclosure.

## Verifying downloads

Use the official release page, its checksum file and any attached provenance record. Check that the package name, version/build, source tag and build evidence agree. A checksum confirms bytes against a manifest; it does not by itself establish safety, reproducibility or an independent audit.

Current Apple IPAs require sideload signing. The Mac app is ad-hoc signed, not notarized. Re-signing changes the package bytes, so the official download's checksum applies before that transformation. Current Android releases require the project's production signer; a debug certificate is not a release signer.

The public app code and private engine dependencies are separate access/licensing boundaries. Do not infer that a complete shipping binary can be rebuilt from the public repository alone. See [Release artifacts](docs/RELEASE-ARTIFACTS.md) and [Build and source boundaries](docs/BUILDING.md).

## Preserve user data during investigation

Do not suggest clearing storage, deleting Keychain entries, resetting accounts or removing encryption keys as a generic workaround. An unreadable backup is not proof that it is empty or safe to replace. Security fixes must preserve pending edits, histories and supported unknown/legacy fields unless a specifically reviewed change requires otherwise.
