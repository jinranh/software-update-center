#!/bin/bash
set -euo pipefail

version="${1:-}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'Usage: %s VERSION (for example, 0.2.0)\n' "$0" >&2
    exit 2
fi

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'python3 -c "import shutil, sys; shutil.rmtree(sys.argv[1])" "$work"' EXIT
stage="$work/package"
app="$stage/usr/lib/software-update-center"
desktop="$stage/usr/share/applications"
units="$stage/usr/lib/systemd/user"
docs="$stage/usr/share/doc/software-update-center"

install -d "$stage/DEBIAN" "$app" "$desktop" "$units" "$docs" "$root/dist"
install -m 644 "$root"/{app.py,backend.py,catalog.py,catalog_view.py,check.conf,official_apt.py,install.py,notify.py} "$app/"
install -m 644 "$root"/{README.md,README.en.md} "$docs/"
install -m 644 "$root/desktop/software-update-check.timer" "$units/"
sed 's@^Exec=.*@Exec=/usr/bin/python3 /usr/lib/software-update-center/app.py@' \
    "$root/desktop/software-update-center.desktop" > "$desktop/software-update-center.desktop"
sed 's@^ExecStart=.*@ExecStart=/usr/bin/python3 /usr/lib/software-update-center/notify.py@' \
    "$root/desktop/software-update-check.service" > "$units/software-update-check.service"

cat > "$stage/DEBIAN/control" <<CONTROL
Package: software-update-center
Version: $version
Section: admin
Priority: optional
Architecture: all
Maintainer: jinranh <92495065+jinranh@users.noreply.github.com>
Depends: apt, sudo, gpg, ca-certificates, appstream, python3 (>= 3.10), python3-apt, python3-gi, gir1.2-gtk-3.0, gnome-terminal, libnotify-bin
Homepage: https://github.com/jinranh/software-update-center
Description: Graphical APT updater for desktop applications
 Select installed desktop applications, preview dependency changes, and
 retry items that failed to download. Optional reminders use a systemd
 user timer.
CONTROL

dpkg-deb --root-owner-group --build "$stage" "$work/software-update-center.deb"
install -m 644 "$work/software-update-center.deb" "$root/dist/software-update-center.deb"
printf 'Built %s\n' "$root/dist/software-update-center.deb"
