# 软件更新

[![简体中文](https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E5%BD%93%E5%89%8D%E8%AF%AD%E8%A8%80-555555?style=for-the-badge)](README.md)
[![English](https://img.shields.io/badge/Read%20in-English-0969da?style=for-the-badge)](README.en.md)

在一个窗口里检查、选择并升级 Ubuntu 上通过 APT 管理的桌面应用。安装前可以预览依赖变化。批量下载失败时，工具会逐项处理，复用已经下载的安装包，并在列表中标出可重试的应用。

## 官方应用 MVP

在 Ubuntu 22.04.5 LTS + GNOME 42（amd64）的桌面终端里，从此工作区运行只读预览：

```bash
SOFTWARE_UPDATE_CENTER_PREVIEW=1 /usr/bin/python3 app.py
```

预览有自己的窗口标识与缓存。可以搜索、读取本机版本、核对已配置的软件源和审查安装计划。常规模式使用 `/usr/bin/python3 app.py`。要试用完整的授权与安装流程，请在隔离 Ubuntu 环境安装本分支构建的 DEB，使用由 root 持有的系统助手。启用和安装均需在窗口确认后到终端授权。

| Ubuntu 22.04 amd64 | 发布者 / DEB 包名 | 官方渠道与更新方式 |
| --- | --- | --- |
| ChatGPT | OpenAI / `chatgpt` | 卡片显示 Linux 预览及官方入口。Ubuntu 支持版本为 24.04 和 26.04，官方 DEB 在支持平台配置签名 APT 源。[OpenAI Linux 安装指南](https://learn.chatgpt.com/docs/linux/linux-app) |
| Claude Desktop | Anthropic / `claude-desktop` | 使用 Anthropic 签名 APT 源安装和更新。[系统要求、软件源及指纹](https://support.claude.com/en/articles/10065433-install-claude-desktop) |
| Visual Studio Code | Microsoft / `code` | 使用 Microsoft 签名 APT 源安装和更新。[软件源说明](https://code.visualstudio.com/docs/setup/linux) · [签名指纹](https://learn.microsoft.com/en-us/linux/packages) |
| Google Chrome | Google / `google-chrome-stable` | 使用 Google 签名 APT 源安装和更新。Chrome 安装器管理 `google-chrome.sources`。检测到旧式源或重复源会显示对应文件，需先在系统中核对并归并为唯一的官方源。[系统要求](https://support.google.com/chrome/answer/95346) · [签名指纹](https://www.google.com/linuxrepositories/) |
| 飞书 | 北京飞书科技有限公司 / `bytedance-feishu-stable` | 从官网下载 Linux DEB。升级时可在客户端检查版本，或从官网获取新版 DEB 覆盖安装。[下载](https://www.feishu.cn/download) · [升级说明](https://www.feishu.cn/hc/zh-CN/articles/360043828013) |
| Docker Desktop | Docker / `docker-desktop` | 当前 Ubuntu 安装指南列出 24.04 和 26.04。Desktop 使用官方 DEB 换包，安装指南里的 Docker Engine 仓库用于依赖。[Ubuntu 安装及升级](https://docs.docker.com/desktop/setup/install/linux/ubuntu/) |

渠道复核日期为 2026-11-23。新增产品时，在 [catalog.py](catalog.py) 记录包名、支持平台、官方证据和签名信息。AppStream 仅补充匹配组件 ID 与软件包记录的摘要。dpkg 同名包的版本会标为本机版本，首次通过本工具替换时会在确认框说明原发布者未核实。

官方 APT 操作先刷新所选厂商源并验证指纹，使用本机已取得的 APT 索引解析依赖。下载前会展示 APT 选中的 URL 和版本，授权后重新核对计划。其他源提供同名包、依赖计划变化、签名异常、版本锁定或降级会阻止操作并显示原因。系统软件源刷新失败时，「已安装更新」暂停批量更新，「官方应用」仍能独立检查。缺少依赖索引时，安装计划会显示 APT 的具体错误。

## 快速开始

已验证环境为 **Ubuntu 22.04 LTS + GNOME**。

1. 从 [最新发布版本](https://github.com/jinranh/software-update-center/releases/latest) 的 **Assets** 下载 [software-update-center.deb](https://github.com/jinranh/software-update-center/releases/latest/download/software-update-center.deb)。
2. 在下载目录打开终端，运行：

   ```bash
   sudo apt install ./software-update-center.deb
   ```

   APT 会从系统软件源补齐缺少的依赖，包括 Python 和 GTK。下载缺少的依赖需要联网。
3. 安装完成后，在应用菜单搜索 **软件更新**。

## 从源码运行或安装

已验证环境为 **Ubuntu 22.04 LTS + GNOME**。请在当前 GNOME 桌面会话的终端里执行，升级软件时需要本机管理员权限。

1. 安装依赖和 Git。

   ```bash
   sudo apt update
   sudo apt install git python3-apt python3-gi gir1.2-gtk-3.0 gnome-terminal libnotify-bin appstream gpg ca-certificates
   ```

2. 下载并安装。

   ```bash
   git clone https://github.com/jinranh/software-update-center.git
   cd software-update-center
   ./setup.sh
   ```

   也可在仓库页面点 **Code → Download ZIP**，解压后在目录内运行 `bash setup.sh`。使用 ZIP 时可跳过 Git 依赖。

3. 在应用菜单搜索 **软件更新** 并打开。想直接从源码试用，可以在源码目录运行 `/usr/bin/python3 app.py`。

## 启用应用自身的 APT 更新

第一次只需安装一次 [软件源配置包](https://github.com/jinranh/software-update-center/releases/latest/download/software-update-center-apt-source.deb)：

```bash
sudo apt install ./software-update-center-apt-source.deb
```

此包会配置项目的签名 APT 源。在「软件更新」里点击 **检查更新** 后，已通过 DEB 安装的「软件更新」自身有新版时，也会出现在列表中。勾选确认即可升级。升级后关闭并重新打开窗口。

如果之前用 `setup.sh` 安装过，请先按下文的源码版卸载步骤移除旧的用户级启动入口和定时任务，再安装 DEB。停用此软件源可运行 `sudo apt purge software-update-center-apt-source`。

## 怎么用

1. 点击 **检查更新**。列表会显示当前版本和可用版本。
2. 勾选想升级的应用，点击 **更新所选**，核对依赖变更后选择 **继续安装**。
3. 在终端按提示授权并确认。安装结束后，重新打开升级过的应用即可使用新版本。
4. 如果某个包下载超时，其他可用项仍可继续安装。失败项会显示原因并重新勾选，恢复连接后点 **更新所选** 重试。

工具使用系统已经配置的 APT 源。Snap、Flatpak 和手动安装的用户目录应用走各自的更新渠道。目录内的软件在「官方应用」页管理。Compass 和 WorkBuddy 的默认排除规则在 [backend.py](backend.py) 中。当前界面和终端提示主要使用简体中文。

## 可选的更新提醒

```bash
systemctl --user enable --now software-update-check.timer
```

启用后在登录后的几分钟内、以及本地时间每天 10:00 左右检查一次。有更新时点击通知可打开窗口，仍由你选择并确认安装。关闭提醒可以运行 `systemctl --user disable --now software-update-check.timer`。

## 更新或卸载工具

如果选择手动更新 DEB，下载新版的 `software-update-center.deb`，在下载目录重新运行 `sudo apt install ./software-update-center.deb`。要卸载 DEB 版，请先关闭窗口，有启用更新提醒时先关闭提醒，然后运行 `sudo apt remove software-update-center`。

以下步骤适用于通过 `setup.sh` 安装的版本。

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

官方源和安装的集成验收在一次性 Ubuntu 22.04 amd64 容器中运行。脚本使用真实 Microsoft 仓库，另用本地签名仓库模拟同名包、下载失败与签名异常。

```bash
docker run --rm --platform linux/amd64 -v "$PWD:/src:ro" ubuntu:22.04 \
  bash /src/tests/integration/jammy.sh
```

### MVP 实测记录

- Ubuntu 22.04.5 amd64 容器从 Microsoft 签名仓库首次安装了 VS Code `1.146.0-1794340865`，再由已配置的 `vscode.list` 升级到 `1.147.0-1794903003`。两个阶段均核对了 dpkg 版本和应用安装记录。
- 另一个软件源离线时，系统全局刷新报告该源失败，Microsoft 软件源仍可独立验证和启用。重复微软源会列出冲突文件。
- 在签名测试源中，同名社区包、错误密钥指纹和下载 404 均被拦截。恢复下载后重试成功，测试包完成 `1.0 → 2.0`。在真实 sudo 提示中取消授权后，源和密钥文件没有变化。
- 27 项测试通过，覆盖 GTK 搜索、勾选、依赖预览保护、逐项重试和安装后版本核对。GNOME 桌面与隔离 Xvfb 均试验了 GTK 预览。
