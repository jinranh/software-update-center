"""Exercise cold APT checks and exact reviewed upgrades with an offline source."""
from functools import partial
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from repository_failures import QuietFiles, apt_flow, apt_run, build_repo, output

import apt_pkg
import backend
import partial_apt


def main():
    if os.geteuid() != 0 or not Path('/.dockerenv').is_file():
        raise SystemExit('Run only inside a disposable root test container')
    with tempfile.TemporaryDirectory(prefix='suc-partial-fixture-') as directory:
        root = Path(directory)
        root.chmod(0o755)
        gpg_home = root / 'gpg'
        gpg_home.mkdir(mode=0o700)
        gpg = ('gpg', '--batch', '--no-options', '--homedir', str(gpg_home))
        output(*gpg, '--pinentry-mode', 'loopback', '--passphrase', '', '--quick-gen-key',
               'Integration Fixture <test@example.org>', 'ed25519', 'sign', '1d')
        key = root / 'fixture.asc'
        key.write_bytes(output(*gpg, '--armor', '--export'))
        cache = root / 'cache'
        backend.CACHE = cache
        apt_flow.LOCK = root / 'install.lock'

        sources = root / 'fixture.sources'
        config = root / 'apt.conf'
        config.write_text(f'Dir::Etc::sourcelist "{sources}";\nDir::Etc::sourceparts "-";\n')
        backend.ENV['APT_CONFIG'] = str(config)
        apt_pkg.config.set('Dir::Etc::sourcelist', str(sources))
        apt_pkg.config.set('Dir::Etc::sourceparts', '-')
        apt_flow.SOURCE_LIST = sources
        apt_flow.SOURCE_PARTS = root / 'unused'

        name = 'suc-partial-demo'
        repo = root / 'healthy'
        old = build_repo(repo, name, '1.0', gpg, desktop=True)
        build_repo(repo, name, '2.0', gpg, depends='suc-partial-data', desktop=True)
        build_repo(repo, 'suc-partial-data', '1.0', gpg, architecture='all')
        output('dpkg', '-i', str(old))
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietFiles, directory=root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}/healthy'
            good = f'Types: deb\nURIs: {base}\nSuites: stable\nComponents: main\nSigned-By: {key}\n'
            dead = f'Types: deb\nURIs: http://127.0.0.1:9/offline\nSuites: stable\nComponents: main\nSigned-By: {key}\n'
            sources.write_text(good + '\n' + dead)
            state = backend.check()
            assert '127.0.0.1:9' in state['partial_error'], state
            assert any(row['package'] == name and row['available'] and row['candidate'] == '2.0'
                       for row in state['apps']), state
            plan = partial_apt.preview(f'{name}=2.0')
            assert {item['package'] for item in plan['archives']} == {name, 'suc-partial-data'}, plan
            assert all(item['uri'].startswith(base + '/') for item in plan['archives']), plan
            print('PASS failed source leaves healthy signed app and dependency reviewable', flush=True)
            digest = apt_flow.plan_digest(plan)
            partial_apt.apply(f'{name}=2.0', digest, apt_run)
            assert apt_flow.local_version(name) == '2.0'
            assert apt_flow.local_version('suc-partial-data') == '1.0'
            print('PASS real signed app and dependency installed via partial update', flush=True)
            build_repo(repo, name, '3.0', gpg, desktop=True)
            sources.write_text(good + 'Trusted: yes\n\n' + dead)
            state = backend.check()
            assert not any(row['package'] == name and row['available'] for row in state['apps']), state
            print('PASS forced-trust source excluded from partial results', flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            subprocess.run(['gpgconf', '--homedir', str(gpg_home), '--kill', 'gpg-agent'],
                           check=True)


if __name__ == '__main__':
    main()
