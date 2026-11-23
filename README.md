# 软件更新

适用于 Ubuntu GNOME 桌面的 APT 更新工具，已在 Ubuntu 22.04 LTS 上验证。它根据本机安装的软件和已配置的 APT 源列出桌面应用，显示当前版本和可用版本，支持勾选更新和预览依赖变更。

安装时先下载安装包，再在终端确认。批量下载遇到网络错误时会逐项处理，复用 APT 已下载的安装包。失败或待处理的应用会留在列表里，可重新选择安装。更新是否完成以实际安装版本为准。

## 安装

在 Ubuntu 桌面会话里安装依赖：

```bash
sudo apt install python3-apt python3-gi gir1.2-gtk-3.0 gnome-terminal libnotify-bin
```

在仓库根目录运行 `./setup.sh`，然后在应用菜单搜索「软件更新」。也可直接运行 `/usr/bin/python3 app.py`。

需要登录后和每日 10:00 左右的更新提醒时，运行：

```bash
systemctl --user enable --now software-update-check.timer
```

定时任务只检查并发出通知。软件升级需要在窗口中选择软件，再按终端提示确认。

## 范围与排查

更新列表包含已有 APT 源的桌面应用。Snap、Flatpak 和用户目录手动安装的应用由各自的渠道管理。工作设备上单独维护的飞书、Docker Desktop、Compass、WorkBuddy 在 `backend.py` 的排除列表中。

安装日志位于 `~/.cache/software-update-center/install.log`。后台检查日志可用 `journalctl --user -u software-update-check.service` 查看。

在 GNOME 桌面会话内运行测试：

```bash
/usr/bin/python3 -m unittest discover -s tests -v
```
