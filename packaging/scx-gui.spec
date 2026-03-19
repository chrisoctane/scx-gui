Name:           scx-gui
Version:        0.2
Release:        0
Summary:        PySide6 GUI frontend for the installed openSUSE scx package
License:        NOASSERTION
URL:            https://github.com/chrisoctane/scx-gui
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python313-devel
BuildRequires:  python313-pip
BuildRequires:  python313-setuptools
BuildRequires:  python313-wheel

Requires:       python313-pyside6 >= 6.6

%description
scx-gui is a lightweight PySide6 frontend for the openSUSE scx package.
It uses the installed scx binaries, their --help output, /etc/default/scx,
and scx.service instead of replacing how scx works.

%prep
%autosetup

%build
/usr/bin/python3.13 -m pip wheel \
  --verbose \
  --progress-bar off \
  --disable-pip-version-check \
  --use-pep517 \
  --no-build-isolation \
  --no-deps \
  --wheel-dir ./dist \
  .

%install
/usr/bin/python3.13 -m pip install \
  --verbose \
  --progress-bar off \
  --disable-pip-version-check \
  --root %{buildroot} \
  --no-compile \
  --ignore-installed \
  --no-deps \
  --no-index \
  --find-links ./dist \
  scx-gui==%{version}
install -D -m 0644 packaging/scx-gui.desktop %{buildroot}%{_datadir}/applications/scx-gui.desktop

%check
QT_QPA_PLATFORM=offscreen /usr/bin/python3.13 -m unittest discover -s tests -q

%files
%doc README.md
%{python3_sitelib}/scx_gui/
%{python3_sitelib}/scx_gui-*.dist-info/
%{_bindir}/scx-gui
%{_datadir}/applications/scx-gui.desktop
