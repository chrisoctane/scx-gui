#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
topdir="${repo_root}/rpm-build"
version="$(python3 -c "from scx_gui import __version__; print(__version__)")"
archive_basename="scx-gui-${version}"

rm -rf "${topdir}"
mkdir -p "${topdir}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

staged_source="${tmpdir}/${archive_basename}"
mkdir -p "${staged_source}"

rsync -a \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude '*.egg-info' \
  --exclude 'build' \
  --exclude 'dist' \
  --exclude 'rpm-build' \
  "${repo_root}/" "${staged_source}/"

tar -C "${tmpdir}" -czf "${topdir}/SOURCES/${archive_basename}.tar.gz" "${archive_basename}"
cp "${repo_root}/packaging/scx-gui.spec" "${topdir}/SPECS/"

rpmbuild -bb \
  --define "_topdir ${topdir}" \
  "${topdir}/SPECS/scx-gui.spec"

find "${topdir}/RPMS" -type f -name '*.rpm' -print
