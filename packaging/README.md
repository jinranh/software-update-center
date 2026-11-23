# APT archive maintenance

The public APT archive is hosted at https://jinranh.github.io/software-update-center/apt from the root of the repository's `gh-pages` branch.

The application DEB is also suitable for manual installation. The separate `software-update-center-apt-source.deb` opts in to the source and restricts its package priority to `software-update-center`.

## Publish a new version

Build the two artifacts from the same clean source commit you intend to tag. Use a new version for every changed application package.

```bash
./packaging/build-deb.sh 0.3.0
./packaging/build-apt-source-deb.sh 0.3.0
```

Fetch and check out the existing `gh-pages` branch in a separate worktree. With `apt-ftparchive` (from `apt-utils`) and GnuPG available, run the following from the source checkout. Replace the first path with the path to that worktree.

```bash
./packaging/build-apt-repo.sh dist/software-update-center.deb \
    /path/to/gh-pages-checkout "$HOME/.local/share/software-update-center-signing"
```

Check the changes in the Pages worktree, then commit and push that branch. Wait for Pages to serve the signed index and confirm APT can discover the new version. Publish the two DEB files to GitHub Releases under the tag for the tested source commit.

The signing private key stays in the maintainer's local GnuPG directory. Keep a secure backup of that key and its revocation certificate. The tracked `archive-keyring.asc` is the public key distributed to users. The archive script signs with the key matching that file.
