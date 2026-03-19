# scx-gui

`scx-gui` is a standalone PySide6 frontend for the `scx` package installed on
your system. It does not ship its own schedulers or replace how `scx` works.
It is just a small wrapper around the installed `scx_*` binaries, their
`--help` output, `/etc/default/scx`, and `scx.service`, so you do not have to
keep remembering file paths and flag names.

If `scx` is missing on openSUSE, the app can also install the package for you
through `zypper` with `pkexec`.

## What it wraps

- Installed schedulers in `/usr/bin/scx_*`
- `/etc/default/scx`
- `scx.service`

## Features

- Lists installed schedulers dynamically
- Offers to install the openSUSE `scx` package if it is missing
- Lets you edit the saved `SCX_FLAGS` value directly
- Provides a pop-out quick-add browser built from the selected scheduler's `--help`
- Keeps raw help and config-file preview behind buttons so the main window stays simple
- Saves scheduler config back to `/etc/default/scx` using `pkexec`
- Starts, stops, restarts, enables, and disables `scx.service`
- Lets you reset a latched failed state for `scx.service`
- Shows current service, boot, and `sched_ext` state with large status indicators

## Run

From the project folder:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .
python -m scx_gui
```

For a quick non-interactive UI construction check:

```bash
QT_QPA_PLATFORM=offscreen python -m scx_gui --smoke-test
```

The smoke test now skips live scheduler/service discovery on purpose. It checks
that the Qt UI can be constructed headlessly without depending on a fully
configured `scx` runtime on the host.

## Build RPM

On openSUSE Tumbleweed, you can build a local RPM with:

```bash
chmod +x packaging/build-rpm.sh
./packaging/build-rpm.sh
```

The built package will be written under `rpm-build/RPMS/`.
