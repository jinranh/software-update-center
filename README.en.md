# Software Update Center

[![简体中文](https://img.shields.io/badge/%E9%98%85%E8%AF%BB-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-0969da?style=for-the-badge)](README.md)
[![English](https://img.shields.io/badge/English-Current%20language-555555?style=for-the-badge)](README.en.md)

Check, select, and upgrade APT-managed desktop apps on Ubuntu from one window. Preview dependency changes before installing. If a batch download fails, the updater tries the selected apps individually, reuses cached packages, and marks apps you can retry.

## Try the official app catalog

Run from a source checkout in your GNOME session:

```bash
SOFTWARE_UPDATE_CENTER_PREVIEW=1 /usr/bin/python3 app.py
```

Preview mode uses a separate application ID and cache. Search the curated catalog in Apps. Updates shows actionable updates from verified vendor repositories and the system APT sources in one place. You can review candidates in preview mode. Install the DEB built from this branch in an isolated Ubuntu environment to try the full privileged install workflow. The catalog handles verified APT installs and updates for Claude Desktop, Visual Studio Code and Google Chrome. On Ubuntu 22.04 the ChatGPT and Docker Desktop cards show the vendor's Ubuntu version requirements and link to official instructions. Feishu provides an official DEB download. See [the channel table](README.md#官方应用-mvp) and [catalog data](catalog.py) for evidence links and repository fingerprints.

## Quick start

Tested on **Ubuntu 22.04 LTS with GNOME**.

1. Download [software-update-center.deb](https://github.com/jinranh/software-update-center/releases/latest/download/software-update-center.deb) from the **Assets** section of the [latest release](https://github.com/jinranh/software-update-center/releases/latest).
2. Open a terminal in the download directory and run:

   ```bash
   sudo apt install ./software-update-center.deb
   ```

   APT installs missing dependencies, including Python and GTK, from your configured system repositories. An internet connection is required to download missing dependencies.
3. Open **Software Update Center** (软件更新) from the applications menu.

## Run or install from source

Tested on **Ubuntu 22.04 LTS with GNOME**. Run these commands from a terminal in your GNOME session. You'll need local administrator privileges to install updates.

1. Install the dependencies and Git.

   ```bash
   sudo apt update
   sudo apt install git python3-apt python3-gi gir1.2-gtk-3.0 gnome-terminal libnotify-bin appstream gpg ca-certificates
   ```

2. Download and install the updater.

   ```bash
   git clone https://github.com/jinranh/software-update-center.git
   cd software-update-center
   ./setup.sh
   ```

   You can also select **Code → Download ZIP** on the repository page, extract the archive, and run `bash setup.sh` from the extracted directory. You can skip the Git dependency when using the ZIP.

3. Open **Software Update Center** (软件更新) from the applications menu. To run it directly from the source directory, use `/usr/bin/python3 app.py`.

## Enable updates for the updater itself

Install the separate [APT source package](https://github.com/jinranh/software-update-center/releases/latest/download/software-update-center-apt-source.deb) once:

```bash
sudo apt install ./software-update-center-apt-source.deb
```

This opts in to the project's signed APT source. If you installed the updater as a DEB, its new versions will appear in the app after **Check for updates**. Select the upgrade on the Updates page and confirm it, then close and reopen the updater.

If you previously used `setup.sh`, follow the source uninstall instructions below to remove the old user launcher and timer before installing the DEB. To remove the APT source, run `sudo apt purge software-update-center-apt-source`.

## Use the updater

1. Click **Check for updates** (检查更新) to see installed and available versions.
2. On the Updates page, click **Update** (更新) beside an app and review its dependencies. When **Select a batch** (批量选择) is available, you can select multiple eligible updates.
3. Follow the terminal prompts to authorize and confirm the installation. Once it finishes, reopen the apps you updated to use their new versions.
4. If a package times out, other available updates can still finish. The updater shows the reason and selects failed items again. Once the connection recovers, click **Update selected** to retry.

The updater uses your system's existing APT sources. Snap, Flatpak, and manually installed apps in your home directory use their own update channels. The Updates page lists available updates from verified vendor repositories and system APT sources together. The exclusions for Compass and WorkBuddy are listed in [backend.py](backend.py). The app interface and terminal prompts are primarily in Simplified Chinese.

## Optional update notifications

```bash
systemctl --user enable --now software-update-check.timer
```

The timer checks a few minutes after you sign in and around 10:00 each day in your local time zone. Reminders cover system APT updates and configured signed vendor repositories. Click a notification to open the Updates page and choose what to install. To turn off reminders, run `systemctl --user disable --now software-update-check.timer`.

## Upgrade or uninstall the updater

To update the DEB manually, download the new `software-update-center.deb` and run `sudo apt install ./software-update-center.deb` from the download directory again. To uninstall the DEB, close the updater, turn off reminders if enabled, and run `sudo apt remove software-update-center`.

The steps below apply to installations made with `setup.sh`.

For a Git checkout, run `git pull --ff-only` and then `./setup.sh` from the source directory. For a ZIP installation, download and extract the new archive, then run `bash setup.sh`. Close and reopen the updater to load the new version.

To remove the updater, close its window and run the commands below. They remove the updater's files, menu entry, and timer.

```bash
systemctl --user disable --now software-update-check.timer
systemctl --user stop software-update-check.service
rm -f -- "${XDG_DATA_HOME:-$HOME/.local/share}/applications/software-update-center.desktop"
rm -f -- "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/software-update-check.service" \
          "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/software-update-check.timer"
rm -r -- "$HOME/.local/share/software-update-center"
systemctl --user daemon-reload
```

## Troubleshooting and feedback

- **Download timed out.** Retry the failed items once the connection recovers. APT reuses packages it has already downloaded.
- **Another package manager is busy.** Wait for the system updater or other APT operation to finish. The updater waits for the lock to clear.
- **Find the logs.** See `~/.cache/software-update-center/install.log` for installations and `journalctl --user -u software-update-check.service` for background checks.
- **Report an issue.** Include your Ubuntu version, package name, and relevant error in [Issues](https://github.com/jinranh/software-update-center/issues). Remove private details such as account names and internal repository URLs before sharing logs.

To run the tests from a GNOME desktop session:

```bash
/usr/bin/python3 -m unittest discover -s tests -v
```
