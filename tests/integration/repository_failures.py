"""Exercise production APT handling against a signed loopback repository on Jammy."""
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import catalog
import official_apt as apt_flow
from install import apt_run


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def output(*args, cwd=None):
    try:
        return subprocess.check_output(args, cwd=cwd, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        print((error.stdout + error.stderr).decode(errors='replace'), file=sys.stderr)
        raise


def build_repo(root, package, version, key, *, architecture='amd64', depends='', desktop=False):
    """Add an inert fixture package and sign the repository index."""
    pool = root / 'pool'
    pool.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        control = Path(work) / 'DEBIAN'
        control.mkdir()
        dependency_line = f'Depends: {depends}\n' if depends else ''
        (control / 'control').write_text(
            f'Package: {package}\nVersion: {version}\nArchitecture: {architecture}\n'
            f'{dependency_line}'
            'Maintainer: Integration Fixture <test@example.org>\n'
            'Description: Empty test-only package\n')
        payload = Path(work) / 'usr/share/doc' / package
        payload.mkdir(parents=True)
        (payload / 'readme').write_text('Local APT test fixture.\n')
        if desktop:
            launchers = Path(work) / 'usr/share/applications'
            launchers.mkdir()
            (launchers / f'{package}.desktop').write_text(
                f'[Desktop Entry]\nType=Application\nName={package}\nExec=/usr/bin/true\n')
        deb = pool / f'{package}_{version}_{architecture}.deb'
        output('dpkg-deb', '--root-owner-group', '--build', work, str(deb))
    dists = root / 'dists/stable'
    packages = dists / 'main/binary-amd64/Packages'
    packages.parent.mkdir(parents=True, exist_ok=True)
    packages.write_bytes(output('apt-ftparchive', 'packages', 'pool', cwd=root))
    release = output('apt-ftparchive', '-o', 'APT::FTPArchive::Release::Codename=stable',
                     '-o', 'APT::FTPArchive::Release::Suite=stable',
                     '-o', 'APT::FTPArchive::Release::Components=main',
                     '-o', 'APT::FTPArchive::Release::Architectures=amd64',
                     '-o', 'APT::FTPArchive::Release::Origin=Integration fixture',
                     'release', '.', cwd=dists)
    (dists / 'Release').write_bytes(release)
    output(*key, '--yes', '--clearsign', '-o', str(dists / 'InRelease'), str(dists / 'Release'))
    return deb


def expect_failure(message, operation):
    try:
        operation()
    except RuntimeError as exc:
        if message not in str(exc):
            raise
        print('PASS', str(exc).splitlines()[0], flush=True)
    else:
        raise AssertionError(f'Expected failure containing {message}')


def plan(app):
    with apt_flow.workdir() as scratch:
        return apt_flow.Session(app, scratch, 'amd64').plan()


def main():
    if os.geteuid() != 0 or not Path('/.dockerenv').is_file():
        raise SystemExit('Run only inside a disposable root test container')
    if catalog.system() != ('ubuntu', '22.04', 'amd64'):
        raise SystemExit('This integration test requires Ubuntu 22.04 amd64')
    with tempfile.TemporaryDirectory(prefix='suc-fixture-') as directory:
        root = Path(directory)
        root.chmod(0o755)
        gpg_home = root / 'gpg'
        gpg_home.mkdir(mode=0o700)
        gpg = ('gpg', '--batch', '--no-options', '--homedir', str(gpg_home))
        output(*gpg, '--pinentry-mode', 'loopback', '--passphrase', '', '--quick-gen-key',
               'Integration Fixture <test@example.org>', 'ed25519', 'sign', '1d')
        records = output(*gpg, '--with-colons', '--list-keys').decode().splitlines()
        fingerprint = next(line.split(':')[9] for line in records if line.startswith('fpr:'))
        public_key = root / 'fixture.asc'
        public_key.write_bytes(output(*gpg, '--armor', '--export'))
        package = f'software-update-center-fixture-{os.getpid()}'
        build_repo(root / 'community', 'code', '999.0', gpg)
        dependency = package + '-data'
        build_repo(root / 'vendor', dependency, '1.0', gpg, architecture='all')
        missing_deb = build_repo(root / 'vendor', package, '1.0', gpg, depends=dependency)
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietFiles, directory=root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            source = apt_flow.SOURCE_PARTS / 'suc-integration-fixture.sources'
            try:
                community = catalog.Repository(base + '/community', base + '/fixture.asc',
                                               fingerprint)
                source.write_text(apt_flow.repo_stanza(community, community.uri,
                                                       public_key, 'amd64'))
                output(*apt_flow.APT_BASE, '-o', f'Dir::Etc::sourcelist={source}',
                       '-o', 'Dir::Etc::sourceparts=-', '-o', 'APT::Get::List-Cleanup=false',
                       '-o', 'APT::Update::Error-Mode=any', 'update')
                current = apt_flow.local_version('code')
                expect_failure('其他来源提供同名软件包 code', lambda: plan(catalog.APPS['vscode']))
                assert apt_flow.local_version('code') == current

                vendor = catalog.Repository(base + '/vendor', base + '/fixture.asc', fingerprint)
                app = replace(catalog.APPS['vscode'], id='fixture', package=package, repo=vendor)
                source.write_text(apt_flow.repo_stanza(vendor, vendor.uri, public_key, 'amd64'))
                want = plan(app)
                assert want['version'] == '1.0', want
                assert {item['package']: item['arch'] for item in want['archives']} == {
                    package: 'amd64', dependency: 'all'}, want
                assert all(item['uri'].startswith(vendor.uri + '/') for item in want['archives']), want
                hidden_deb = missing_deb.with_suffix('.disabled')
                missing_deb.rename(hidden_deb)
                with apt_flow.workdir() as scratch:
                    session = apt_flow.Session(app, scratch, 'amd64')
                    expect_failure('下载', lambda: apt_flow.execute(
                        session, apt_flow.plan_digest(want), apt_run))
                assert not apt_flow.local_version(package) and not apt_flow.local_version(dependency)
                hidden_deb.rename(missing_deb)

                for version in ('1.0', '2.0'):
                    if version != '1.0':
                        build_repo(root / 'vendor', package, version, gpg, depends=dependency)
                    with apt_flow.workdir() as scratch:
                        session = apt_flow.Session(app, scratch, 'amd64')
                        candidate = session.plan()
                        assert candidate['version'] == version, candidate
                        apt_flow.execute(session, apt_flow.plan_digest(candidate), apt_run)
                    assert apt_flow.local_version(package) == version
                    assert apt_flow.local_version(dependency) == '1.0'
                    print('PASS Signed fixture installed version', version, flush=True)

                wrong_key = replace(app, repo=replace(vendor, fingerprint='0' * 40))
                expect_failure('厂商签名密钥异常', lambda: plan(wrong_key))

                metadata = root / 'vendor/dists/stable/InRelease'
                metadata.write_bytes(metadata.read_bytes().replace(b'Integration fixture', b'Tampered fixture'))
                expect_failure('厂商源刷新失败', lambda: plan(app))
            finally:
                source.unlink(missing_ok=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            subprocess.run(['gpgconf', '--homedir', str(gpg_home), '--kill', 'gpg-agent'],
                           check=True)


if __name__ == '__main__':
    main()
