#!/bin/bash
set -euo pipefail

if [[ $# != 3 ]]; then
    printf 'Usage: %s APP_DEB PAGES_CHECKOUT SIGNING_KEY_DIRECTORY\n' "$0" >&2
    exit 2
fi

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
deb="$(realpath -- "$1")"
pages="$(realpath -- "$2")"
keydir="$(realpath -- "$3")"
repo="$pages/apt"
version="$(dpkg-deb -f "$deb" Version)"
[[ "$(dpkg-deb -f "$deb" Package)" == software-update-center ]]
[[ "$(dpkg-deb -f "$deb" Architecture)" == all ]]
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
[[ -d "$pages" && -d "$keydir" ]]

export LC_ALL=C
fingerprint="$(gpg --homedir "$keydir" --batch --with-colons --show-keys "$root/packaging/archive-keyring.asc" \
    | awk -F: '$1=="fpr" && !seen {print $10; seen=1}')"
[[ "$fingerprint" =~ ^[A-F0-9]{40}$ ]]
gpg --homedir "$keydir" --batch --list-secret-keys "$fingerprint" >/dev/null

workspace="$(mktemp -d)"
trap 'python3 -c "import shutil, sys; shutil.rmtree(sys.argv[1])" "$workspace"' EXIT
pool="$repo/pool/main/s/software-update-center"
install -d "$pool"
dest="$pool/software-update-center_${version}_all.deb"
if [[ -f "$dest" ]]; then
    if ! cmp -s "$deb" "$dest"; then
        printf 'An existing package has version %s and different contents. Bump the version.\n' "$version" >&2
        exit 1
    fi
else
    install -m 644 "$deb" "$dest"
fi

touch "$pages/.nojekyll"
cd "$repo"
apt-ftparchive packages pool > "$workspace/Packages"
for arch in amd64 arm64; do
    index="dists/stable/main/binary-$arch"
    install -d "$index"
    install -m 644 "$workspace/Packages" "$index/Packages"
    gzip -n -c "$workspace/Packages" > "$index/Packages.gz"
done

apt-ftparchive -o APT::FTPArchive::Release::Origin=SoftwareUpdateCenter \
    -o APT::FTPArchive::Release::Label='Software Update Center' \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Codename=stable \
    -o 'APT::FTPArchive::Release::Architectures=amd64 arm64' \
    -o APT::FTPArchive::Release::Components=main release dists/stable > "$workspace/Release"
gpg --homedir "$keydir" --batch --yes --local-user "$fingerprint" \
    --output "$workspace/InRelease" --clearsign "$workspace/Release"
install -m 644 "$workspace/Release" "$workspace/InRelease" dists/stable/
