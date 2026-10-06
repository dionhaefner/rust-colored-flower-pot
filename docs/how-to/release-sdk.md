# Test and release the JavaScript SDK

The SDK uses independent `sdk-vX.Y.Z` tags. Python `vX.Y.Z` releases continue to
use the PyPI workflow. The npm package and embedded SDK share the same source,
but their release schedules can differ. An npm SDK release qualifies against the
server and viewer in its tagged commit. It does not qualify every historical
server or publish Python artifacts.

## Test gates

The reusable `.github/workflows/sdk.yml` runs through correctness qualification
on pull requests, main, scheduled/manual runs and Python releases. The npm release
workflow calls it for every SDK tag. All its jobs must pass before publishing.

| Gate                                       | Purpose                                                                                                                                                   |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Node 20, 22 and 24                         | Contract tests for transport, encoding, profiles, discovery, cancellation, time and revisioned state, plus release-tag validation                         |
| TypeScript 5.9.3                           | Strict public declaration checks, including invalid requests that must fail compilation                                                                   |
| Installed npm tarball on each Node version | Exact package contents, byte equality with source, both public exports, typed adapters and runtime state round trips                                      |
| Chromium with a freshly built server       | Both profiles, offline viewer and three independent maps, metadata/legends, inspection/time, share/restore, stale responses, aborts, errors and real CORS |

The Node matrix verifies SDK logic and packaging. Chromium qualifies browser
behavior. Firefox/Safari and formal accessibility audits remain separate support
work. External providers are excluded from the offline gate. Time labels reuse
pinned real pixels, and inspection checks display RGBA. See the
[SDK support policy](../reference/javascript.md#versions-support-and-accessibility).

For a local package check with Node 20 or newer and npm installed:

```sh
npm ci --prefix packaging/sdk --ignore-scripts --no-audit --no-fund
node tests/sdk_contracts.mjs
node tests/sdk_release.mjs
node packaging/sdk/node_modules/typescript/bin/tsc --strict --noEmit --module nodenext --target es2022 tests/sdk_types.mts
node tests/sdk_package.mjs build/sdk-package-check
```

Use a fresh output directory for each package check. Browser reproduction is
documented in the [test guide](https://github.com/dionhaefner/rust-colored-flower-pot/blob/main/tests/README.md). Keep failed/skipped checks
separate from successful qualification. CI retains the tested tarballs and their
SHA-256 checksums for 14 days, alongside browser reports and screenshots.

## Version and release policy

Before 1.0, incompatible SDK, type or state changes increase the minor version.
Compatible fixes increase the patch version. From 1.0, incompatible changes
increase the major version and compatible additions increase the minor version.
Removing a supported runtime or server contract is an incompatible change.
State schema changes also need a new explicit state version.

Python's Hatch VCS discovery selects only `v` release tags. An SDK tag does not
change the Python version. A temporary-repository regression test checks stable
and prerelease SDK tags between Python releases.

Update `version` in `js/sdk/package.json`, `VERSION` in `index.js` and its
literal declaration in `index.d.ts` together. Add user-facing changes to the SDK's
`CHANGELOG.md` and update affected examples/docs. The release gate rejects a tag
that differs from the package/exported version. Contract tests check declarations
against those versions.

Stable `sdk-vX.Y.Z` tags publish to npm's `latest` tag. Prereleases use
`sdk-vX.Y.Z-alpha.N`, `-beta.N` or `-rc.N` and publish to `next`. Test prereleases
with an explicit version or `npm install @rcfp/sdk@next`. A stable release gets
its own version and full qualification.

After merging the version and changelog changes into main:

```sh
git fetch origin main
git tag sdk-v0.1.1 origin/main
git push origin sdk-v0.1.1
```

The release checks main ancestry and versions, then qualifies the tagged commit.
The publishing job downloads the Node 24 job's tested tarball, verifies its
checksum and publishes that file without rebuilding or executing package scripts.
Only that job receives OIDC permission. Node 24 and npm 11.6.2 support trusted
publishing, which adds provenance for this public repository/package.
[npm trusted publishing](https://docs.npmjs.com/trusted-publishers/)

npm versions are immutable. Retry a failed publishing job after fixing account
configuration, without changing the tag or artifact. If publication succeeded,
do not publish that version again. Fix a defective release with a new version.
[npm publication rules](https://docs.npmjs.com/cli/v11/commands/npm-publish/)

## Configure npm once

An npm account with write access to the package's scope must perform account-side
setup. For a new package, first publish a tested tarball from a successful main
correctness run using that account, then configure trusted publishing on the
existing package. This first release uses npm login/2FA. Subsequent versions use
the tag workflow. npm's documented setup starts from an existing package's
settings. [npm setup](https://docs.npmjs.com/trusted-publishers/)

Download `sdk-package-24` from the successful main run into a fresh directory:

```sh
gh run download RUN_ID --name sdk-package-24 --dir build/sdk-bootstrap
cd build/sdk-bootstrap
sha256sum --check SHA256SUMS
npm login
npm publish ./*.tgz --access public --tag latest --ignore-scripts --registry https://registry.npmjs.org/
```

For an initial prerelease, use `--tag next` instead. This local bootstrap does not
produce GitHub OIDC provenance. Configure the package's GitHub trusted publisher
with these exact values:

| Setting              | Value                                 |
| -------------------- | ------------------------------------- |
| Organization or user | `dionhaefner`                         |
| Repository           | `rust-colored-flower-pot`             |
| Workflow filename    | `sdk-release.yml`                     |
| Environment          | `npm`                                 |
| Allowed action       | Direct publication with `npm publish` |

Create the GitHub `npm` environment and restrict deployments to `sdk-v*` tags.
Configure matching tag rules to limit who can create SDK release tags. Optional
environment reviewers add a manual approval before publication. Keep account
tokens out of repository secrets, source files and shared state. Confirm the
publisher settings immediately before the first automated release, then verify
the published version, npm tag and provenance on npm.

Account-side setup and an actual registry publish are separate from local dry-run
verification. A successful `npm publish --dry-run` does not test OIDC or prove
that the account owns the scope.
