#!/bin/sh
# Build the .deb inside a Debian/Ubuntu container (see .github/workflows/ci.yml):
#
#   docker run --rm -v "$PWD":/src -w /src ubuntu:26.04 sh packaging/deb/build.sh
#
# Needs requirements.txt from `uv export --frozen --no-dev --no-hashes`, so the
# package gets exactly the locked dependencies.
#
# On Docker Desktop (a Mac), the last step fails: the newest tar can't stat the
# venv's symlinks there ("Cannot stat: Function not implemented"). Stage with
# DEB_ROOT=/src/build/debroot STAGE_ONLY=1, then pack in an older container:
#   docker run --rm -v "$PWD":/src -w /src ubuntu:24.04 \
#     dpkg-deb --build --root-owner-group build/debroot dist/
# CI runs on a real Linux kernel and doesn't need this.
set -eu

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    python3 python3-venv ca-certificates >/dev/null

ARCH=$(dpkg --print-architecture)
PY=$(python3 -c 'import sys; print(f"python{sys.version_info[0]}.{sys.version_info[1]}")')
VERSION=$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' src/bench_parts/__init__.py)

# Built in place at its installed path: a venv's scripts hard-code it. Made
# with the versioned interpreter, so its python link points at /usr/bin/pythonX.Y,
# the package this .deb depends on, not /usr/bin/python3 (a different package).
"$PY" -m venv /opt/bench-parts
/opt/bench-parts/bin/pip install -q --no-cache-dir -r requirements.txt
/opt/bench-parts/bin/pip install -q --no-cache-dir --no-deps .
find /opt/bench-parts -name __pycache__ -prune -exec rm -rf {} +

ROOT=${DEB_ROOT:-$(mktemp -d)}
rm -rf "$ROOT"
install -d "$ROOT/DEBIAN" "$ROOT/opt" "$ROOT/usr/bin" "$ROOT/lib/systemd/system"
cp -a /opt/bench-parts "$ROOT/opt/"
install -m 0755 packaging/deb/bench-parts "$ROOT/usr/bin/bench-parts"
install -m 0644 packaging/deb/bench-parts.service "$ROOT/lib/systemd/system/"
install -m 0755 packaging/deb/postinst packaging/deb/prerm packaging/deb/postrm "$ROOT/DEBIAN/"

cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: bench-parts
Version: $VERSION
Architecture: $ARCH
Maintainer: Aaron Cupp <mrcupp@mrcupp.com>
Depends: $PY, passwd
Section: web
Priority: optional
Homepage: https://github.com/IamMrCupp/bench-parts
Description: Bench parts inventory, run as a systemd service
 A web app for the bench's parts drawers and a REST API benchhud and scripts
 use. It listens on loopback until told otherwise and refuses to start
 without a password.
CONTROL

if [ -n "${STAGE_ONLY:-}" ]; then
    echo "staged in $ROOT"
    exit 0
fi

mkdir -p dist
dpkg-deb --build --root-owner-group "$ROOT" "dist/bench-parts_${VERSION}_${ARCH}.deb"
ls -l dist/
