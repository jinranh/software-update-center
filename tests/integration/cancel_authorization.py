"""Cancel the real installer sudo prompt in a disposable Jammy container."""
import errno
import json
import os
from pathlib import Path
import pty
import select
import signal
import time
import uuid


def cancel_password_prompt(token):
    pid, terminal = pty.fork()
    if pid == 0:
        os.execl('/usr/sbin/runuser', 'runuser', '-u', 'tester', '--', '/usr/bin/python3',
                 '/src/install.py', token, '--official', 'enable', 'vscode')
    reaped = False
    try:
        found_prompt = False
        found_end = False
        data = b''
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not select.select([terminal], [], [], 0.25)[0]:
                continue
            try:
                data += os.read(terminal, 4096)
            except OSError as exc:
                if exc.errno != errno.EIO:  # Linux PTY reports EIO when the terminal is closed.
                    raise
                break
            if not found_prompt and b'[sudo] password for tester:' in data:
                os.write(terminal, b'\x03')
                found_prompt = True
            if not found_end and '按回车关闭'.encode() in data:
                os.write(terminal, b'\n')
                found_end = True
        assert found_prompt and found_end, data.decode(errors='replace')
        _, status = os.waitpid(pid, 0)
        reaped = True
        assert os.waitstatus_to_exitcode(status) != 0, data.decode(errors='replace')
    finally:
        os.close(terminal)
        if not reaped:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)


def main():
    if os.geteuid() != 0 or not Path('/.dockerenv').is_file():
        raise SystemExit('Run only inside a disposable root test container')
    source = Path('/etc/apt/sources.list.d/software-update-center-vscode.sources')
    key = Path('/etc/apt/keyrings/software-update-center-vscode.asc')
    before = [(path, path.read_bytes() if path.exists() else None) for path in (source, key)]
    rule = Path('/etc/sudoers.d/suc-cancel-test')
    with rule.open('x') as handle:
        os.fchmod(handle.fileno(), 0o440)
        handle.write('tester ALL=(root) /usr/bin/python3 -E -s /usr/lib/software-update-center/official_apt.py enable vscode\n')
    try:
        token = uuid.uuid4().hex
        cancel_password_prompt(token)
        result = Path('/home/tester/.cache/software-update-center') / f'result-{token}.json'
        summary = json.loads(result.read_text())
        assert summary['code'] != 0 and not summary['updated'], summary
        for path, content in before:
            assert (path.read_bytes() if path.exists() else None) == content
        print('PASS Real terminal authorization cancelled; source/key untouched', flush=True)
    finally:
        rule.unlink()


if __name__ == '__main__':
    main()
