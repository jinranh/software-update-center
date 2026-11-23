#!/bin/bash
set -euo pipefail
umask 022

version="${1:-}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'Usage: %s VERSION\n' "$0" >&2
    exit 2
fi

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
stage="$(mktemp -d)"
trap 'python3 -c "import shutil, sys; shutil.rmtree(sys.argv[1])" "$stage"' EXIT
chmod 755 "$stage"
install -d "$stage/DEBIAN" "$stage/usr/share/keyrings" \
    "$stage/etc/apt/sources.list.d" "$stage/etc/apt/preferences.d" "$root/dist"
install -m 644 "$root/packaging/archive-keyring.asc" \
    "$stage/usr/share/keyrings/software-update-center-archive-keyring.asc"

cat > "$stage/DEBIAN/control" <<CONTROL
Package: software-update-center-apt-source
Version: $version
Section: admin
Priority: optional
Architecture: all
Maintainer: jinranh <92495065+jinranh@users.noreply.github.com>
Depends: apt (>= 2.4)
Homepage: https://github.com/jinranh/software-update-center
Description: Signed APT source for Software Update Center
 Opt in to ongoing APT updates for the software-update-center package.
CONTROL

source_file='etc/apt/sources.list.d/software-update-center.sources'
pin_file='etc/apt/preferences.d/software-update-center'
cat > "$stage/$source_file" <<'SOURCES'
Types: deb
URIs: https://jinranh.github.io/software-update-center/apt
Suites: stable
Components: main
Architectures: amd64 arm64
Signed-By: /usr/share/keyrings/software-update-center-archive-keyring.asc
SOURCES
cat > "$stage/$pin_file" <<'PREFERENCES'
Package: software-update-center
Pin: release o=SoftwareUpdateCenter
Pin-Priority: 500

Package: *
Pin: release o=SoftwareUpdateCenter
Pin-Priority: -1
PREFERENCES

printf '/%s\n/%s\n' "$source_file" "$pin_file" > "$stage/DEBIAN/conffiles"
dpkg-deb --root-owner-group --build "$stage" "$root/dist/software-update-center-apt-source.deb"
