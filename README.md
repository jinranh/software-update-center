# 软件更新

[![简体中文](https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E5%BD%93%E5%89%8D%E8%AF%AD%E8%A8%80-555555?style=for-the-badge)](README.md)
[![English](https://img.shields.io/badge/Read%20in-English-0969da?style=for-the-badge)](README.en.md)

在一个窗口里检查、选择并升级 Ubuntu 上通过 APT 管理的桌面应用。安装前可以预览依赖变化。批量下载失败时，工具会逐项处理，复用已经下载的安装包，并在列表中标出可重试的应用。

## 快速开始

已验证环境为 **Ubuntu 22.04 LTS + GNOME**。请在当前 GNOME 桌面会话的终端里执行，升级软件时需要本机管理员权限。

1. 安装依赖和 Git。

   ```bash
   sudo apt update
   sudo apt install git python3-apt python3-gi gir1.2-gtk-3.0 gnome-terminal libnotify-bin
   ```

2. 下载并安装。

   ```bash
   git clone https://github.com/jinranh/software-update-center.git
   cd software-update-center
   ./setup.sh
   ```

   也可在仓库页面点 **Code → Download ZIP**，解压后在目录内运行 `bash setup.sh`。使用 ZIP 时可跳过 Git 依赖。

3. 在应用菜单搜索 **软件更新** 并打开。想直接从源码试用，可以在源码目录运行 `/usr/bin/python3 app.py`。

## 怎么用

1. 点击 **检查更新**。列表会显示当前版本和可用版本。
2. 勾选想升级的应用，点击 **更新所选**，核对依赖变更后选择 **继续安装**。
3. 在终端按提示授权并确认。安装结束后，重新打开升级过的应用即可使用新版本。
4. 如果某个包下载超时，其他可用项仍可继续安装。失败项会显示原因并重新勾选，恢复连接后点 **更新所选** 重试。

工具使用系统已经配置的 APT 源。Snap、Flatpak 和手动安装的用户目录应用走各自的更新渠道。飞书、Docker Desktop、Compass 和 WorkBuddy 的默认排除规则在 [backend.py](backend.py) 中。当前界面和终端提示主要使用简体中文。

## 可选的更新提醒

```bash
systemctl --user enable --now software-update-check.timer
```

启用后在登录后的几分钟内、以及本地时间每天 10:00 左右检查一次。有更新时点击通知可打开窗口，仍由你选择并确认安装。关闭提醒可以运行 `systemctl --user disable --now software-update-check.timer`。

## 更新或卸载工具

通过 Git 安装时，可以在源码目录运行 `git pull --ff-only` 再运行 `./setup.sh`。通过 ZIP 安装时，下载并解压新版后重新运行 `bash setup.sh`。安装完成后，关闭并重开窗口以载入新版。

要移除工具，请关闭窗口，并在终端运行以下命令。这些命令清理的是工具自己的程序、菜单入口和定时任务。

```bash
systemctl --user disable --now software-update-check.timer
systemctl --user stop software-update-check.service
rm -f -- "${XDG_DATA_HOME:-$HOME/.local/share}/applications/software-update-center.desktop"
rm -f -- "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/software-update-check.service" \
          "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/software-update-check.timer"
rm -r -- "$HOME/.local/share/software-update-center"
systemctl --user daemon-reload
```

## 排查与反馈

- **下载超时。** 等网络恢复后重试失败项。APT 会复用已下载的安装包。
- **其他软件管理任务占用。** 等系统更新或其他 APT 操作结束，再继续。更新器会等待锁释放。
- **查看安装日志。** 打开 `~/.cache/software-update-center/install.log`。后台检查日志可用 `journalctl --user -u software-update-check.service` 查看。
- **反馈问题。** 可以在 [Issues](https://github.com/jinranh/software-update-center/issues) 提供 Ubuntu 版本、软件包名和相关报错。分享日志前请去掉账号、内部仓库地址等私人信息。

开发者可以在 GNOME 桌面会话中运行测试：

```bash
/usr/bin/python3 -m unittest discover -s tests -v
```
