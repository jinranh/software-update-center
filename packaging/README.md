# APT archive maintenance

The public APT archive is hosted at https://jinranh.github.io/software-update-center/apt from the root of the repository's `gh-pages` branch.

The application DEB is also suitable for manual installation. The separate `software-update-center-apt-source.deb` opts in to the source and restricts its package priority to `software-update-center`.

## Publish a new version

Follow the repository [release rules](../AGENTS.md). Verify the test suite, a clean install, and an upgrade from the current public DEB on Ubuntu 22.04 before publishing.

Build the two artifacts from the same clean source commit you intend to tag. Use a new version for every changed application package.

```bash
release_version=0.4.0
./packaging/build-deb.sh "$release_version"
./packaging/build-apt-source-deb.sh "$release_version"
sha256sum dist/software-update-center{,-apt-source}.deb
```

Fetch and check out the existing `gh-pages` branch in a separate worktree. With `apt-ftparchive` (from `apt-utils`) and GnuPG available, run the following from the source checkout. Replace the first path with the path to that worktree.

```bash
./packaging/build-apt-repo.sh dist/software-update-center.deb \
    /path/to/gh-pages-checkout "$HOME/.local/share/software-update-center-signing"
```

Use the tested application DEB for both GitHub Releases and the signed APT archive. Tag the verified source commit on `main` and include both DEB files in the release. After publishing the Pages branch, verify the live `InRelease` signature, its package-index hash, and the package hash against the Release asset. Confirm the new version is an APT candidate from an isolated environment with the project source enabled. Keep the previous package in the archive.

The signing private key stays in the maintainer's local GnuPG directory. Keep a secure backup of that key and its revocation certificate. The tracked `archive-keyring.asc` is the public key distributed to users. The archive script signs with the key matching that file.
