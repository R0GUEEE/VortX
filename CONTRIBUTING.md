# Contributing to VortX

Contributions are welcome, and the bar to clear is low. The project has merged
community pull requests within hours of them opening, and contributors are
credited by name in the release notes for the version their work ships in. This
guide covers the few things that keep that fast.

## Licensing and the CLA

VortX source code is licensed under **GPL-3.0-or-later** (see [LICENSE](LICENSE)).
The VortX name and logo are separate from the code licence and are covered by the
brand-use policy in [TRADEMARK.md](TRADEMARK.md).

Before your first contribution can be merged, we ask you to sign the project's
**Contributor License Agreement** ([CLA.md](CLA.md)). It is short and it is not
hostile:

- You keep the copyright in everything you write. The CLA does not assign or
  transfer your copyright.
- You grant the project a broad, lasting licence to use your contribution, which
  is what lets the project keep its licensing options open over time.

### How to sign

Open a pull request that adds your signed record to the bottom of [CLA.md](CLA.md),
filling in the signature block (full name, GitHub username, email, and date). One
signature covers all of your present and future contributions, so you only do this
once. If you prefer a different submission path, ask in your pull request or an
issue and the maintainer will confirm the process. An automated CLA check may be
added later; until then this manual path is the way to sign.

## Pull request mechanics

- Keep each pull request focused on one thing.
- Include before and after screenshots for anything visual. The existing pull
  requests set the pattern.
- Use the SDK/toolchain selected by the relevant workflow and retain the declared
  minimum OS versions. Newer APIs need availability handling for supported systems.
- State what you actually checked. A fixture test is not a full app build or a
  physical-device playback/sync result. Never include credentials or raw private logs.
- Keep public updates in the project's voice, without tool-brand signatures or
  generated co-author trailers. Preserve real contributor and third-party credits.

### Commit messages

Use conventional commit titles, scoped by platform, for example:

```
feat(tvos): add a resume button to the series hero
fix(ios): stop the player straddling the previous title
chore: bump the fetch script pins
```

### Engine source never reaches this repo

The engine layer is proprietary and lives in a private repository. A `pre-push`
hook blocks any push to a public remote whose commits carry engine source, and a
nightly CI sweep re-checks every branch and tag here for the same thing. Both use
one detector, `scripts/scan-proprietary-engine.sh`, which reads the trees of the
commits being pushed rather than the branch or tag name.

`./scripts/fetch-server-deps.sh` installs the hook, so a fresh clone is covered by
the normal setup. Confirm it any time with `scripts/install-git-hooks.sh --check`.

### No em dashes

Do not use em dashes in any prose, including code comments, commit messages,
pull request descriptions, changelog entries, and documentation. Use commas,
colons, periods, or parentheses instead.

## The Xcode project is generated

`app/project.yml` is the retained project specification. The current native shipping
path derives `app/.native-project.yml` through `scripts/generate-native-apple-project.rb`
with explicit engine/player inputs. Generated `.xcodeproj` files are not the place to
make source changes.

Commit the relevant specification/generator change, not a generated project. Read
[Build and source boundaries](docs/BUILDING.md) before attempting a full build: the
shipping engine requires private dependencies, and the old Node/server setup is not
the current native release recipe.
