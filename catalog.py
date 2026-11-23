"""Reviewed product identities. AppStream can enrich their presentation only."""
from dataclasses import dataclass
import platform
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Repository:
    uri: str
    key_url: str
    fingerprint: str
    aliases: tuple[str, ...] = ()
    suite: str = 'stable'
    component: str = 'main'
    source_file: str | None = None

    def owns(self, url, source_uri=None):
        candidate = urlsplit(url)
        for uri in (source_uri,) if source_uri else (self.uri, *self.aliases):
            base = urlsplit(uri)
            if (candidate.scheme, candidate.netloc) == (base.scheme, base.netloc):
                prefix = base.path.rstrip('/') + '/'
                if candidate.path.startswith(prefix):
                    return True
        return False


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    publisher: str
    package: str
    aliases: tuple[str, ...]
    desktop_id: str
    icon: str
    summary: str
    website: str
    evidence: tuple[tuple[str, str], ...]
    support: str
    architectures: tuple[str, ...]
    ubuntu_min: str
    ubuntu_versions: tuple[str, ...] = ()
    repo: Repository | None = None
    update: str = '通过厂商签名的 APT 软件源更新。'
    appstream_id: str | None = None

    def channel(self, distro, version, arch):
        if distro != 'ubuntu' or arch not in self.architectures:
            return 'unavailable'
        actual = tuple(map(int, version.split('.'))) if re.fullmatch(r'\d+\.\d+', version) else ()
        minimum = tuple(map(int, self.ubuntu_min.split('.')))
        if actual < minimum or (self.ubuntu_versions and version not in self.ubuntu_versions):
            return 'unavailable'
        return 'apt' if self.repo else 'deb'


def system():
    release = platform.freedesktop_os_release()
    machine = platform.machine()
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(machine, machine)
    return release['ID'], release.get('VERSION_ID', ''), arch


APPS = {app.id: app for app in (
    Product(
        'chatgpt', 'ChatGPT', 'OpenAI', 'chatgpt',
        ('chat gpt', '聊天助手', 'codex'), 'chatgpt.desktop', 'system-search',
        'OpenAI 的 ChatGPT Linux 桌面预览。',
        'https://learn.chatgpt.com/docs/linux/linux-app',
        (('Linux 支持、下载与更新', 'https://learn.chatgpt.com/docs/linux/linux-app'),),
        'Ubuntu 24.04 LTS 或 26.04 LTS（amd64 / arm64）。',
        ('amd64', 'arm64'), '24.04', ('24.04', '26.04'),
        update='从 OpenAI 下载官方 DEB。厂商安装包会配置签名软件源，随后通过系统 APT 更新。',
        appstream_id='com.openai.chatgpt'),
    Product(
        'claude', 'Claude Desktop', 'Anthropic', 'claude-desktop',
        ('claude', '克劳德'), 'com.anthropic.Claude.desktop', 'system-search',
        'Anthropic 的 Claude 桌面客户端（Linux Beta）。',
        'https://claude.com/download',
        (('安装、系统要求与签名指纹',
          'https://support.claude.com/en/articles/10065433-install-claude-desktop'),),
        'Ubuntu 22.04 LTS 及以上（amd64 / arm64）。', ('amd64', 'arm64'), '22.04',
        repo=Repository('https://downloads.claude.ai/claude-desktop/apt/stable',
                        'https://downloads.claude.ai/claude-desktop/key.asc',
                        '31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE')),
    Product(
        'vscode', 'Visual Studio Code', 'Microsoft', 'code',
        ('vs code', 'vscode', '微软代码编辑器'), 'com.microsoft.VSCode.desktop', 'accessories-text-editor',
        '微软发布的 Visual Studio Code 稳定版代码编辑器。',
        'https://code.visualstudio.com/download',
        (('Linux 安装与 APT 软件源', 'https://code.visualstudio.com/docs/setup/linux'),
         ('系统要求', 'https://code.visualstudio.com/docs/supporting/requirements'),
         ('Microsoft 签名指纹', 'https://learn.microsoft.com/en-us/linux/packages')),
        'Ubuntu 20.04 及以上（目录覆盖 amd64 / arm64，需满足厂商 glibc 要求）。', ('amd64', 'arm64'), '20.04',
        repo=Repository('https://packages.microsoft.com/repos/code',
                        'https://packages.microsoft.com/keys/microsoft.asc',
                        'BC528686B50D79E339D3721CEB3E94ADBE1229CF')),
    Product(
        'chrome', 'Google Chrome', 'Google', 'google-chrome-stable',
        ('chrome', '谷歌浏览器', '谷歌', '谷歌 Chrome'), 'google-chrome.desktop', 'web-browser',
        'Google 发布的 Chrome 浏览器稳定版。', 'https://www.google.com/chrome/',
        (('Linux 安装与系统要求', 'https://support.google.com/chrome/answer/95346'),
         ('Google 签名指纹', 'https://www.google.com/linuxrepositories/'),
         ('Google 官方 APT 索引', 'https://dl.google.com/linux/chrome-stable/deb/dists/stable/InRelease')),
        'Ubuntu 18.04 及以上。此目录验证 amd64，处理器需支持 SSE3。',
        ('amd64',), '18.04',
        repo=Repository('https://dl.google.com/linux/chrome-stable/deb',
                        'https://dl.google.com/linux/linux_signing_key.pub',
                        'EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796',
                        ('https://dl.google.com/linux/chrome/deb',
                         'http://dl.google.com/linux/chrome/deb'),
                        source_file='google-chrome.sources')),
    Product(
        'feishu', '飞书', '北京飞书科技有限公司', 'bytedance-feishu-stable',
        ('feishu', '飞书桌面版'), 'bytedance-feishu.desktop', 'internet-chat',
        '飞书桌面协作客户端。', 'https://www.feishu.cn/download',
        (('Linux 支持与安装', 'https://www.feishu.cn/hc/zh-CN/articles/360025035993'),
         ('桌面客户端升级说明', 'https://www.feishu.cn/hc/zh-CN/articles/360043828013')),
        'Ubuntu 18.04 及以上（目录覆盖 amd64 / arm64 DEB）。',
        ('amd64', 'arm64'), '18.04',
        update='在官网下载 DEB。升级时可从客户端检查版本，或下载新版 DEB 覆盖安装。'),
    Product(
        'docker', 'Docker Desktop', 'Docker', 'docker-desktop',
        ('docker', '容器桌面版'), 'docker-desktop.desktop', 'applications-system',
        'Docker 的桌面容器管理工具。',
        'https://docs.docker.com/desktop/setup/install/linux/ubuntu/',
        (('Ubuntu 安装、下载与换包更新',
          'https://docs.docker.com/desktop/setup/install/linux/ubuntu/'),
         ('发布说明与校验和', 'https://docs.docker.com/desktop/release-notes/')),
        'Ubuntu 24.04 LTS 或 26.04 LTS（amd64），需要 KVM。',
        ('amd64',), '24.04', ('24.04', '26.04'),
        update='从 Docker 官方文档下载新版 DEB 覆盖安装。Docker Engine 的仓库用于获取依赖。'),
)}


def search(query):
    normalized = re.sub(r'[^\w]', '', query.casefold())
    fields = {app.id: [re.sub(r'[^\w]', '', value.casefold())
                       for value in (app.name, app.package, app.publisher, *app.aliases)]
              for app in APPS.values()}
    exact = [app for app in APPS.values() if normalized in fields[app.id]]
    return exact or [app for app in APPS.values()
                     if any(normalized in field for field in fields[app.id])]



PACKAGES = frozenset(app.package for app in APPS.values())
