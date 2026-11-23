#!/bin/bash
# Run only in a disposable Ubuntu 22.04 container with the project mounted at /src.
set -euo pipefail

test -f /.dockerenv
test "$(id -u)" = 0
. /etc/os-release
test "$ID:$VERSION_ID:$(dpkg --print-architecture)" = ubuntu:22.04:amd64
cd /src
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
mode=${1:-all}

if [[ "$mode" == all ]]; then
    if [[ -n "${SUC_INTEGRATION_UBUNTU_MIRROR:-}" ]]; then
        mirror=${SUC_INTEGRATION_UBUNTU_MIRROR%/}/
        [[ "$mirror" =~ ^https://[a-zA-Z0-9./_-]+/$ ]]
        sed -i -E "s|http://(archive|security)\.ubuntu\.com/ubuntu/?|$mirror|g" /etc/apt/sources.list
    fi
    apt-get update -qq -o APT::Update::Error-Mode=any
    apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg \
        python3-apt python3-gi sudo apt-utils >/tmp/suc-bootstrap.log 2>&1 || {
        tail -40 /tmp/suc-bootstrap.log >&2
        exit 1
    }
    useradd -m -s /bin/bash tester
fi

install -d /usr/lib/software-update-center
install -m 644 /src/{backend.py,catalog.py,install.py,official_apt.py,check.conf} /usr/lib/software-update-center/

preview() {
    runuser -u tester -- /usr/bin/python3 /src/official_apt.py plan vscode
}

plan_field() {
    /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

apply() {
    local plan=$1 expected digest binary_release
    expected=$(plan_field version <<<"$plan")
    digest=$(/usr/bin/python3 -c 'import json, sys; from official_apt import plan_digest; print(plan_digest(json.load(sys.stdin)))' <<<"$plan")
    # Supply consent to the normal interactive APT apply phase.
    printf 'y\n' | /usr/bin/python3 official_apt.py apply vscode "$digest"
    test "$(dpkg-query -W -f='${Version}' code)" = "$expected"
    binary_release=$(runuser -u tester -- /usr/bin/code --version | sed -n '1p')
    test "$binary_release" = "${expected%%-*}"
    runuser -u tester -- /usr/bin/python3 /src/official_apt.py status |
        /usr/bin/python3 -c 'import json,sys; row=json.load(sys.stdin)["apps"]["vscode"]; assert row["installed"] == sys.argv[1] and row["official"], row' "$expected"
}

source_path=/etc/apt/sources.list.d/software-update-center-vscode.sources
key_path=/etc/apt/keyrings/software-update-center-vscode.asc
unrelated=/etc/apt/sources.list.d/test-offline.list

if [[ "$mode" == all || "$mode" == lifecycle ]]; then
    test ! -e "$source_path"
    test ! -e "$key_path"

    /usr/bin/python3 /src/tests/integration/cancel_authorization.py

    # One failed non-vendor source must not block the VS Code path when dependencies
    # can be planned using the existing signed Ubuntu indexes.
    printf '%s\n' 'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] http://127.0.0.1:9/ apt-test main' >"$unrelated"
    if runuser -u tester -- /usr/bin/python3 /src/backend.py --check >/tmp/suc-global-refresh.log 2>&1; then
        echo 'Expected the global refresh to report the offline source' >&2
        exit 1
    fi
    grep -F '127.0.0.1:9' /tmp/suc-global-refresh.log >/dev/null

    /usr/bin/python3 official_apt.py enable vscode
    grep -Fx "Signed-By: $key_path" "$source_path"
    key_home=$(mktemp -d)
    gpg --batch --no-options --homedir "$key_home" --with-colons --show-keys "$key_path" |
        grep -F 'BC528686B50D79E339D3721CEB3E94ADBE1229CF' >/dev/null
    rm -r "$key_home"
    echo 'PASS First enable while unrelated APT refresh fails'
fi

if [[ "$mode" == all || "$mode" == lifecycle || "$mode" == lifecycle-after-enable ]]; then
    # Read both actual signed versions. A temporary test preference selects the
    # preceding release, then removal allows the current release to be offered.
    rm -f "$unrelated"
    apt-get update -qq -o "Dir::Etc::sourcelist=$source_path" -o Dir::Etc::sourceparts=- \
        -o APT::Get::List-Cleanup=false -o APT::Update::Error-Mode=any
    mapfile -t versions < <(apt-cache madison code |
        awk '/packages.microsoft.com\/repos\/code/ {print $3}' | sort -Vru)
    test "${#versions[@]}" -ge 2
    expected_old=${versions[1]}
    pin=/etc/apt/preferences.d/mvp-vscode-previous
    printf 'Package: code\nPin: version %s\nPin-Priority: 990\n' "$expected_old" >"$pin"
    plan=$(preview)
    test "$(plan_field version <<<"$plan")" = "$expected_old"
    test "$(plan_field source <<<"$plan")" = 'https://packages.microsoft.com/repos/code'
    apply "$plan"
    echo "PASS Installed real Microsoft VS Code $expected_old"

    # A preconfigured deb line with its own Signed-By is accepted for updates.
    printf 'deb [arch=amd64 signed-by=%s] https://packages.microsoft.com/repos/code stable main\n' "$key_path" > /etc/apt/sources.list.d/vscode.list
    rm "$source_path" "$pin"
    plan=$(preview)
    expected_latest=$(plan_field version <<<"$plan")
    dpkg --compare-versions "$expected_latest" gt "$expected_old"
    test "$(plan_field previous <<<"$plan")" = "$expected_old"
    apply "$plan"
    echo "PASS Updated real Microsoft VS Code $expected_old → $expected_latest via existing source"

    # Stop before doing anything if a second vendor entry exists.
    cp /etc/apt/sources.list.d/vscode.list "$source_path.list"
    if preview >/tmp/suc-duplicate.log 2>&1; then
        echo 'Expected duplicate source to be blocked' >&2
        exit 1
    fi
    grep -F '重复' /tmp/suc-duplicate.log
    rm "$source_path.list"
    echo 'PASS Duplicated source was rejected'
fi

if [[ "$mode" == all || "$mode" == failures ]]; then
    printf 'y\ny\n' | /usr/bin/python3 /src/tests/integration/repository_failures.py
fi
