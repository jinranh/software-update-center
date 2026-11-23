#!/bin/bash
set -euo pipefail

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$HOME/.local/share/software-update-center"
launcher_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if (( EUID == 0 )); then
    echo '请以当前桌面用户运行 setup.sh。'
    exit 1
fi

/usr/bin/python3 -c 'import apt, gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk'
command -v gnome-terminal >/dev/null
command -v notify-send >/dev/null

install -d "$app_dir" "$launcher_dir" "$unit_dir"
if [[ "$source_dir" != "$app_dir" ]]; then
    install -m 644 "$source_dir"/{app.py,backend.py,catalog.py,catalog_view.py,check.conf,official_apt.py,install.py,notify.py} "$app_dir/"
fi
install -m 644 "$source_dir/desktop/software-update-center.desktop" "$launcher_dir/"
install -m 644 "$source_dir"/desktop/software-update-check.{service,timer} "$unit_dir/"
systemctl --user daemon-reload
printf '%s\n' '已安装。请在应用菜单搜索「软件更新」。'
