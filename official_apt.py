#!/usr/bin/python3
"""Inspect APT transactions against a fresh, individually authenticated vendor index."""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import shutil
import subprocess
import sys
import tempfile
import pwd
from urllib.request import urlopen

import apt
import apt_pkg

import backend
import catalog


apt_pkg.init()
SYSTEM_LISTS = Path(apt_pkg.config.find_dir('Dir::State::lists'))
SOURCE_LIST = Path(apt_pkg.config.find_file('Dir::Etc::sourcelist'))
SOURCE_PARTS = Path(apt_pkg.config.find_dir('Dir::Etc::sourceparts'))
KEYRINGS = Path('/etc/apt/keyrings')
RECEIPTS = Path('/var/lib/software-update-center/official')
LOCK = RECEIPTS.parent / 'official.lock'
APT_SECURITY = ['-o', 'Acquire::AllowInsecureRepositories=false',
                '-o', 'Acquire::AllowDowngradeToInsecureRepositories=false',
                '-o', 'APT::Get::AllowUnauthenticated=false',
                '-o', 'Acquire::Check-Valid-Until=true',
                '-o', 'Acquire::Check-Date=true',
                '-o', 'Acquire::https::Verify-Peer=true',
                '-o', 'Acquire::https::Verify-Host=true',
                '-o', 'Acquire::Retries=1', '-o', 'Acquire::http::Timeout=20',
                '-o', 'Acquire::https::Timeout=20']
APT_BASE = ['/usr/bin/apt-get', '-c', str(backend.ROOT / 'check.conf'), *APT_SECURITY]
INSTALL = ['--no-remove', '--no-install-recommends', '--reinstall']


@dataclass
class Source:
    path: Path
    types: list[str]
    uris: list[str]
    suites: list[str]
    components: list[str]
    fields: dict[str, str]

    @property
    def enabled(self):
        return self.fields.get('enabled', 'yes').lower() not in ('no', 'false', '0')

    @property
    def key(self):
        return self.fields.get('signed-by', '').strip()


def source_entries(path):
    body = path.read_text(encoding='utf-8')
    if path.suffix == '.sources':
        for paragraph in re.split(r'\n\s*\n', body):
            fields = {}
            current = None
            for line in paragraph.splitlines():
                if not line or line.startswith('#'):
                    continue
                if line[0].isspace() and current:
                    fields[current] += '\n' + line.strip()
                else:
                    key, separator, value = line.partition(':')
                    if not separator or key.lower() in fields:
                        raise RuntimeError(f'无法解析 APT 软件源：{path}')
                    current = key.lower()
                    fields[current] = value.strip()
            if fields:
                yield Source(path, fields.pop('types', '').split(), fields.pop('uris', '').split(),
                             fields.pop('suites', '').split(), fields.pop('components', '').split(), fields)
    else:
        for line in body.splitlines():
            active = line.split('#', 1)[0].strip()
            if not active:
                continue
            match = re.fullmatch(r'(deb(?:-src)?)\s+(?:\[([^\]]+)\]\s+)?(\S+)\s+(\S+)(?:\s+(.*))?', active)
            if not match:
                raise RuntimeError(f'无法解析 APT 软件源：{path}')
            kind, options, uri, suite, components = match.groups()
            fields = {}
            for option in (options or '').split():
                key, separator, value = option.partition('=')
                if not separator or key.lower() in fields:
                    raise RuntimeError(f'无法解析 APT 软件源选项：{path}')
                fields[key.lower()] = value
            yield Source(path, [kind], [uri], [suite], (components or '').split(), fields)


def source_paths():
    parts = sorted(path for path in SOURCE_PARTS.iterdir()
                   if path.is_file() and not path.name.startswith('.')
                   and re.fullmatch(r'[A-Za-z0-9_.-]+\.(list|sources)', path.name)) if SOURCE_PARTS.exists() else []
    return ([SOURCE_LIST] if SOURCE_LIST.exists() else []) + parts


def configured_source(repo, paths, architecture):
    uris = {uri.rstrip('/') for uri in (repo.uri, *repo.aliases)}
    matches = [entry for path in paths for entry in source_entries(path)
               if entry.enabled and 'deb' in entry.types and
               any(uri.rstrip('/') in uris for uri in entry.uris)]
    if len(matches) > 1:
        raise RuntimeError('发现重复厂商软件源：' + '、'.join(str(entry.path) for entry in matches))
    if not matches:
        return None
    source = matches[0]
    if (len(source.uris) != 1 or source.suites != [repo.suite] or
            source.components != [repo.component]):
        raise RuntimeError(f'{source.path} 的仓库 / 套件 / 组件与目录不一致。')
    allowed = {'arch', 'architectures', 'signed-by', 'enabled'}
    unexpected = sorted(key for key in source.fields if key not in allowed and not key.startswith('x-'))
    if unexpected:
        raise RuntimeError(f'{source.path} 有未核实的 APT 选项：{", ".join(unexpected)}')
    if repo.source_file and (source.path.name != repo.source_file
                             or source.uris[0].rstrip('/') != repo.uri):
        raise RuntimeError(f'{source.path} 使用旧版或自定义源文件。厂商安装器会写入 '
                           f'{repo.source_file}，本应用停止以避免重复软件源。')
    if not source.key:
        raise RuntimeError(f'{source.path} 缺少 Signed-By。')
    arches = re.split(r'[,\s]+', source.fields.get('architectures', source.fields.get('arch', '')))
    if arches != [''] and architecture not in arches:
        raise RuntimeError(f'{source.path} 未启用架构 {architecture}。')
    return source


def weakened_sources(paths):
    unverified = ('trusted', 'allow-insecure', 'allow-weak', 'allow-downgrade-to-insecure')
    return {uri.rstrip('/') for path in paths for source in source_entries(path)
            if source.enabled and any(source.fields.get(key, '').lower() in ('yes', 'true', '1')
                                      for key in unverified)
            for uri in source.uris}


def repo_stanza(repo, uri, key, arch):
    return (f'Types: deb\nURIs: {uri}\nSuites: {repo.suite}\nComponents: {repo.component}\n'
            f'Architectures: {arch}\nSigned-By: {key}\n')


def checked_key(path, fingerprint, home):
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise RuntimeError(f'签名密钥不可读取或大小异常：{path}')
    lines = backend.run(['/usr/bin/gpg', '--no-options', '--batch', '--homedir', str(home),
                         '--with-colons', '--show-keys', str(path)]).splitlines()
    principals = []
    for line in lines:
        fields = line.split(':')
        if fields[0] == 'pub':
            principals.append((fields[1], None))
        if fields[0] == 'fpr' and principals and principals[-1][1] is None:
            principals[-1] = (principals[-1][0], fields[9])
    if (len(principals) != 1 or principals[0][1] != fingerprint
            or principals[0][0] in ('e', 'r', 'd', 'i')):
        found = ', '.join(value or '?' for _, value in principals) or '无'
        raise RuntimeError(f'厂商签名密钥异常。预期 {fingerprint}，实际 {found}。')


def source_key(source, scratch):
    if source.key.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        path = scratch / 'existing.asc'
        path.write_text(source.key.replace('\n.\n', '\n\n') + '\n')
        return path
    if not re.fullmatch(r'/[^\s,]+\.(asc|gpg)', source.key):
        raise RuntimeError(f'{source.path} 必须使用单独的 Signed-By 公钥文件或内嵌公钥。')
    return Path(source.key)


def index_file(path):
    return re.search(r'_(?:InRelease|Release(?:\.gpg)?|Packages(?:\.(?:lz4|xz|gz|zst))?)$', path.name)


@contextmanager
def cache_settings(settings):
    previous = {name: apt_pkg.config.find(name) for name in settings}
    try:
        for name, value in settings.items():
            apt_pkg.config.set(name, str(value))
        yield
    finally:
        for name, value in previous.items():
            apt_pkg.config.set(name, value)


def apt_options(settings):
    return [part for name, value in settings.items() for part in ('-o', f'{name}={value}')]


def package_archives(cache, version):
    for package_file, offset in version._cand.file_list:
        index = cache._list.find_index(package_file)
        if index is None:
            continue  # dpkg status has no index or archive URI.
        if not cache._records.lookup((package_file, offset)):
            raise RuntimeError(f'无法读取 {version.package.name} 的 APT 索引记录。')
        yield (index.archive_uri(cache._records.filename), cache._records.sha256_hash,
               index.is_trusted, index.archive_uri('').rstrip('/'), Path(package_file.filename).name)


def vendor_candidate(cache, app, source_uri, fresh_indexes=None):
    if app.package not in cache:
        raise RuntimeError('厂商索引没有提供此架构的软件包。')
    package = cache[app.package]
    base = source_uri.rstrip('/')
    foreign = sorted({uri for version in package.versions
                      for uri, _, _, origin, index in package_archives(cache, version)
                      if origin != base or (fresh_indexes is not None and index not in fresh_indexes)})
    if foreign:
        raise RuntimeError('发现其他来源提供同名软件包 ' + app.package + '：\n' + '\n'.join(foreign[:8]))
    version = package.candidate
    if version is None or not any(trusted and digest and origin == base and app.repo.owns(uri, source_uri)
                                  and (fresh_indexes is None or index in fresh_indexes)
                                  for uri, digest, trusted, origin, index in package_archives(cache, version)):
        if package.is_installed:
            raise RuntimeError(f'APT 候选版本冲突。当前 {package.installed.version}，请核对 APT 优先级和厂商版本。')
        raise RuntimeError('APT 未选择可验证的厂商候选版本，请核对架构和 APT 优先级。')
    return package, version


def changes_from_simulation(output):
    changes = []
    lines = output.splitlines()
    for line in lines:
        if not line.startswith('Inst '):
            continue
        match = re.match(r'^Inst (\S+)(?: \[([^\]]+)\])? \((\S+) .*\[([^\]]+)\]\)$', line)
        if not match:
            raise RuntimeError(f'无法解析 APT 依赖计划：{line}')
        name, installed, version, arch = match.groups()
        changes.append((name, installed, version, arch))
    altered = {name for name, *_ in changes}
    pending = [line.split()[1] for line in lines
               if line.startswith('Conf ') and line.split()[1] not in altered]
    if pending:
        raise RuntimeError('APT 还有未完成配置的软件包：' + '、'.join(pending))
    return changes


class Session:
    def __init__(self, app, scratch, arch):
        self.app = app
        self.repo = app.repo
        self.arch = arch
        self.scratch = scratch
        self.parts = scratch / 'sources'
        self.key_home = scratch / 'gpg'
        self.lists = scratch / 'lists'
        self.vendor_lists = scratch / 'vendor-lists'
        self.archives = scratch / 'archives'
        for path in (self.parts, self.lists, self.vendor_lists, self.archives):
            path.mkdir(mode=0o755)
        self.key_home.mkdir(mode=0o700)
        self.parts.chmod(0o700)
        self.lists.chmod(0o700)
        paths = source_paths()
        self.weakened = weakened_sources(paths)
        self.source = configured_source(self.repo, paths, arch)
        self.uri = self.source.uris[0] if self.source else self.repo.uri
        if self.source:
            configured_key = source_key(self.source, scratch)
            self.key_path = scratch / ('vendor' + configured_key.suffix)
            shutil.copyfile(configured_key, self.key_path)
        else:
            self.key_path = scratch / 'vendor.asc'
            with urlopen(self.repo.key_url, timeout=20) as response:
                key = response.read(1024 * 1024 + 1)
            self.key_path.write_bytes(key)
        checked_key(self.key_path, self.repo.fingerprint, self.key_home)
        single = scratch / 'vendor.sources'
        single.write_text(repo_stanza(self.repo, self.uri, self.key_path, arch))
        system = scratch / ('system.sources' if SOURCE_LIST.suffix == '.sources' else 'system.list')
        system.touch(mode=0o600)
        for path in paths:
            shutil.copyfile(path, system if path == SOURCE_LIST else self.parts / path.name)
        if not self.source:
            shutil.copyfile(single, self.parts / 'software-update-center-preview.sources')
        self.settings = {'Dir::Etc::sourcelist': system, 'Dir::Etc::sourceparts': self.parts,
                         'Dir::State::lists': self.lists, 'Dir::Cache::archives': self.archives,
                         'Dir::Cache::pkgcache': '', 'Dir::Cache::srcpkgcache': ''}
        isolated = {**self.settings, 'Dir::Etc::sourcelist': single,
                    'Dir::Etc::sourceparts': '-', 'Dir::State::lists': self.vendor_lists}
        try:
            backend.run([*APT_BASE, *apt_options(isolated), '-o', 'APT::Update::Error-Mode=any',
                         'update'], timeout=150)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f'厂商源刷新失败：{self.uri}\n{error}') from error

        prefix = apt_pkg.uri_to_filename(self.uri.rstrip('/') + '/dists/' + self.repo.suite + '/')
        for path in SYSTEM_LISTS.iterdir() if SYSTEM_LISTS.exists() else ():
            if path.is_file() and index_file(path) and not path.name.startswith(prefix):
                shutil.copyfile(path, self.lists / path.name)
        self.vendor_indexes = set()
        for path in self.vendor_lists.iterdir():
            if path.name.startswith(prefix) and index_file(path):
                shutil.copyfile(path, self.lists / path.name)
                self.vendor_indexes.add(path.name)

    def commands(self):
        return [*APT_BASE, *apt_options(self.settings), '-o', 'DPkg::Lock::Timeout=180']

    def plan(self):
        with cache_settings(self.settings):
            cache = apt.Cache(memonly=True)
            package, target = vendor_candidate(cache, self.app, self.uri, self.vendor_indexes)
            _, _, arch = catalog.system()
            if target.architecture != arch:
                raise RuntimeError(f'APT 候选架构为 {target.architecture}，预期 {arch}。')
            previous = package.installed.version if package.is_installed else ''
            if package._pkg.selected_state == apt_pkg.SELSTATE_HOLD:
                raise RuntimeError(f'{self.app.package} 已锁定版本。')
            if previous and apt_pkg.version_compare(target.version, previous) < 0:
                raise RuntimeError(f'APT 候选版本 {target.version} 低于已安装版本 {previous}。')
            spec = f'{self.app.package}:{arch}={target.version}'
            command = [*self.commands(), *INSTALL, 'install', spec]
            simulation = backend.run([*command, '--simulate'])
            backend.validate_plan(simulation)
            changes = changes_from_simulation(simulation)
            if not changes or not any(name.split(':')[0] == self.app.package for name, *_ in changes):
                raise RuntimeError('APT 依赖计划没有安装或更新所选应用。')
            archive_records = {}
            protected = catalog.PACKAGES - {self.app.package}
            for name, installed, version, package_arch in changes:
                if name.split(':')[0] in protected:
                    raise RuntimeError(f'依赖计划涉及另一个官方目录软件包：{name}')
                if installed and apt_pkg.version_compare(version, installed) < 0:
                    raise RuntimeError(f'依赖计划包含降级：{name} {installed} → {version}')
                pkg = cache[name] if ':' in name or package_arch == 'all' else cache[f'{name}:{package_arch}']
                for uri, digest, trusted, origin, index in package_archives(cache, pkg.versions[version]):
                    archive_records.setdefault(uri, set()).add(
                        (name, installed or '', version, package_arch, digest, trusted, origin, index))
            manifest = self.manifest(command, changes, archive_records)
            return {'package': self.app.package, 'previous': previous, 'version': target.version,
                    'spec': spec, 'source': self.uri, 'fingerprint': self.repo.fingerprint,
                    'archives': manifest}

    def manifest(self, command, changes, archive_records):
        manifest = []
        with tempfile.TemporaryDirectory(dir=self.scratch) as empty:
            output = backend.run([*command, '-o', f'Dir::Cache::archives={empty}',
                                  '--print-uris', '--download-only', '--assume-yes'])
        for line in output.splitlines():
            if not line.startswith("'"):
                continue
            fields = shlex.split(line)
            if len(fields) not in (3, 4):
                raise RuntimeError(f'无法解析 APT 下载地址：{line}')
            uri, filename, size = fields[:3]
            candidates = archive_records.get(uri, set())
            # Ubuntu updates/security can carry the same authenticated bytes under
            # the same URI. Keep distinct hashes or trust decisions ambiguous.
            records = {record[:-1] for record in candidates}
            if len(records) != 1 or Path(filename).name != filename or not size.isdecimal():
                raise RuntimeError(f'APT 下载地址与依赖计划不一致：{uri}')
            name, previous, version, arch, digest, trusted, origin = records.pop()
            if (not trusted or origin in self.weakened
                    or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                raise RuntimeError(f'依赖缺少已认证的 APT 索引或 SHA-256：{uri}')
            if name.split(':')[0] == self.app.package and (
                    origin != self.uri.rstrip('/') or not self.repo.owns(uri, self.uri)
                    or any(record[-1] not in self.vendor_indexes for record in candidates)):
                raise RuntimeError(f'APT 选择了其他发布者的下载地址：{uri}')
            manifest.append(dict(package=name, previous=previous, version=version, arch=arch, uri=uri,
                                 file=filename, size=int(size), sha256=digest))
        if sorted((a['package'], a['version'], a['arch']) for a in manifest) != sorted(
                (name, version, arch) for name, _, version, arch in changes):
            raise RuntimeError('APT 下载清单没有覆盖模拟计划中的全部变更。')
        return sorted(manifest, key=lambda a: a['package'])


def platform_arch(app):
    system = catalog.system()
    if app.channel(*system) != 'apt':
        raise RuntimeError(f'当前系统没有启用此应用的厂商 APT 渠道。厂商要求：{app.support}')
    return system[2]


@contextmanager
def workdir():
    with tempfile.TemporaryDirectory(prefix='software-update-official-') as path:
        scratch = Path(path)
        # APT's _apt user needs to read the public key and traverse the directory.
        scratch.chmod(0o755)
        yield scratch


def local_version(package):
    result = subprocess.run(['/usr/bin/dpkg-query', '-W', '-f', '${Status}\t${Version}', package],
                            capture_output=True, text=True)
    status, _, version = result.stdout.partition('\t')
    return version if result.returncode == 0 and status.endswith(' ok installed') else ''


def metadata_digest(package):
    path = Path(apt_pkg.config.find_file('Dir::State::status')).parent / 'info'
    for name in (package, package + ':' + catalog.system()[2]):
        try:
            return sha256_file(path / (name + '.md5sums'))
        except FileNotFoundError:
            continue
    return ''


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def official_installation(app, version):
    if not version or app.repo is None:
        return False
    try:
        record = json.loads((RECEIPTS / app.id).read_text())
        return (record['version'] == version and bool(record['md5sums'])
                and record['md5sums'] == metadata_digest(app.package))
    except (OSError, KeyError, ValueError):
        return False


def status():
    output = {}
    paths = source_paths()
    system = catalog.system()
    cache = None
    try:
        cache = apt.Cache(memonly=True)
    except (SystemError, apt_pkg.Error):
        pass
    for app in catalog.APPS.values():
        installed = local_version(app.package)
        row = dict(channel=app.channel(*system), installed=installed, version='',
                   official=official_installation(app, installed), error='', uri='')
        output[app.id] = row
        if row['channel'] != 'apt':
            continue
        try:
            source = configured_source(app.repo, paths, system[2])
            if source:
                row['uri'] = source.uris[0]
                row['signed_by'] = ('内嵌公钥' if source.key.startswith('-----BEGIN') else source.key)
                if cache is not None:
                    try:
                        package, version = vendor_candidate(cache, app, row['uri'])
                        row['version'] = version.version
                        if package._pkg.selected_state == apt_pkg.SELSTATE_HOLD:
                            row['error'] = f'{app.package} 已锁定版本。'
                        else:
                            row['available'] = (bool(installed)
                                                and apt_pkg.version_compare(version.version, installed) > 0)
                    except RuntimeError as error:
                        # A missing cache is recoverable by checking the vendor.
                        if app.package in cache and '同名' in str(error):
                            row['error'] = str(error)
        except RuntimeError as error:
            row['error'] = str(error)
    return {'system': system, 'apps': output}


def activate(session):
    app = session.app
    if session.source:
        raise RuntimeError('厂商源已启用，请重新打开应用卡片。')
    session.plan()
    if configured_source(session.repo, source_paths(), session.arch):
        raise RuntimeError('审查计划期间已有其他程序启用了厂商源，请重新读取本机状态。')
    key = KEYRINGS / f'software-update-center-{app.id}.asc'
    name = session.repo.source_file or f'software-update-center-{app.id}.sources'
    path = SOURCE_PARTS / name
    for directory in (KEYRINGS, SOURCE_PARTS):
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise RuntimeError(f'软件源文件已存在，请核对：{path}')
    if key.exists() or key.is_symlink():
        if key.is_symlink() or key.read_bytes() != session.key_path.read_bytes():
            raise RuntimeError(f'密钥文件已存在且内容不同，请核对：{key}')
    else:
        atomic_write(key, session.key_path.read_bytes())
    atomic_write(path, repo_stanza(session.repo, session.uri, key, session.arch).encode())
    print(f'已启用厂商软件源 {session.uri}。返回窗口即可审查安装计划。', flush=True)


def atomic_write(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.', delete=False) as stream:
        temp = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(content)
            stream.flush()
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def verify_dpkg_plan(path, stream):
    """APT calls this hook under the package-manager lock, before invoking dpkg."""
    archives = json.loads(path.read_text())
    approved = {(item['package'].split(':')[0], item['arch']): item for item in archives}
    if stream.readline().strip() != 'VERSION 3':
        raise RuntimeError('APT 未提供完整的锁内安装清单。')
    for line in stream:
        if not line.strip():
            break
    received = set()
    for line in stream:
        fields = line.strip().split(maxsplit=8)
        if len(fields) != 9:
            raise RuntimeError('APT 锁内安装清单格式异常。')
        name, previous, _, _, direction, version, arch, _, action = fields
        key = (name, arch)
        item = approved.get(key)
        if item is None or version != item['version'] or direction == '>':
            raise RuntimeError(f'APT 锁内安装计划已变化：{name} {version}。')
        if action == '**CONFIGURE**':
            continue
        archive = path.parent / item['file']
        if (action != str(archive) or previous != (item['previous'] or '-')
                or key in received or sha256_file(archive) != item['sha256']):
            raise RuntimeError(f'APT 锁内安装计划或校验值已变化：{name}。')
        received.add(key)
    if received != set(approved):
        raise RuntimeError('APT 锁内安装计划已变化，清单缺少已审查的安装包。')


def execute(session, expected, runner):
    if session.source is None:
        raise RuntimeError('厂商源尚未启用。')
    plan = session.plan()
    if plan_digest(plan) != expected:
        raise RuntimeError('APT 候选包、下载来源或依赖已变化。请返回窗口重新审查。')
    command = [*session.commands(), *INSTALL, 'install', plan['spec']]
    staging = session.scratch / 'downloads'
    staging.mkdir(mode=0o700)
    apt_user = pwd.getpwnam('_apt')
    os.chown(staging, apt_user.pw_uid, apt_user.pw_gid)
    download = ['/usr/lib/apt/apt-helper', *APT_SECURITY, 'download-file']
    for archive in plan['archives']:
        download.extend((archive['uri'], str(staging / archive['file']),
                         'SHA256:' + archive['sha256']))
    downloaded = runner(download)
    if downloaded.code:
        reason = downloaded.failure_message('安装包下载或校验未完成')
        failures = [line for line in downloaded.output.splitlines()
                    if line.startswith(('E:', 'Err:'))]
        raise RuntimeError(reason + ('。' + failures[-1] if failures else '，详情见终端。'))
    for archive in plan['archives']:
        source = staging / archive['file']
        destination = session.archives / archive['file']
        # Copy only regular files. The final cache belongs to root and gets checked
        # again by APT's hook before dpkg sees its paths.
        descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RuntimeError(f"下载结果不是普通文件：{archive['package']}")
            with destination.open('xb') as target:
                shutil.copyfileobj(stream, target)
    if session.plan() != plan:
        raise RuntimeError('下载期间 APT 计划已变化。请返回窗口重新审查。')
    approved = session.archives / 'approved.json'
    approved.write_text(json.dumps(plan['archives']))
    hook = shlex.join(['/usr/bin/python3', '-Es', str(backend.ROOT / 'official_apt.py'),
                       'verify-dpkg', str(approved)])
    outcome = runner([*command, '--no-download', '-o', f'DPkg::Pre-Install-Pkgs::={hook}',
                      '-o', 'DPkg::Tools::Options::/usr/bin/python3::Version=3'])
    if outcome.code:
        changed = next((line for line in outcome.output.splitlines() if line.startswith('APT 锁内')), '')
        raise RuntimeError(changed or 'APT 安装未完成或已取消，详情见终端。')
    if local_version(session.app.package) != plan['version']:
        raise RuntimeError('dpkg 未报告所选版本已安装。')
    RECEIPTS.mkdir(mode=0o755, parents=True, exist_ok=True)
    record = dict(version=plan['version'], md5sums=metadata_digest(session.app.package))
    atomic_write(RECEIPTS / session.app.id, json.dumps(record).encode())
    print(f"已核对安装版本：{plan['version']}", flush=True)


def plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def root_command(operation, app, expected, runner):
    if os.geteuid() != 0:
        raise RuntimeError('需要本机管理员授权。')
    arch = platform_arch(app)
    LOCK.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with LOCK.open('a') as lock, workdir() as scratch:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        session = Session(app, scratch, arch)
        if operation == 'enable':
            activate(session)
        elif operation == 'apply' and re.fullmatch('[0-9a-f]{64}', expected):
            execute(session, expected, runner)
        else:
            raise ValueError('未知的官方软件操作。')


def main(args):
    if args[:1] == ['verify-dpkg'] and len(args) == 2:
        verify_dpkg_plan(Path(args[1]), sys.stdin)
        return
    if args == ['status']:
        print(json.dumps(status(), ensure_ascii=False))
        return
    operation, product, *extra = args
    app = catalog.APPS[product]
    if operation == 'plan' and not extra:
        with workdir() as scratch:
            session = Session(app, scratch, platform_arch(app))
            if session.source is None:
                raise RuntimeError('厂商源尚未启用。')
            print(json.dumps(session.plan(), ensure_ascii=False))
    elif (operation == 'enable' and not extra) or (operation == 'apply' and len(extra) == 1):
        from install import apt_run
        root_command(operation, app, extra[0] if extra else '', apt_run)
    else:
        raise ValueError('未知的官方软件操作。')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (Exception, KeyboardInterrupt) as error:
        message = str(error) or '操作已取消。'
        if sys.argv[1:2] == ['enable'] or sys.argv[1:2] == ['apply']:
            message = ' '.join(message.splitlines())[-2400:]
        print(message, file=sys.stderr)
        sys.exit(1)
