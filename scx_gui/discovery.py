from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import glob
import os
import subprocess

from .help_parser import OptionSpec, parse_help_text


SCX_BINARY_GLOB = "/usr/bin/scx_*"
UTILITY_PATHS = [
    Path("/usr/bin/scxtop"),
    Path("/usr/bin/scxcash"),
]
DOC_PATHS = [
    Path("/usr/share/doc/packages/scx/README.md"),
    Path("/usr/share/doc/packages/scx/OVERVIEW.md"),
]


@dataclass(slots=True)
class ProgramInfo:
    name: str
    path: Path
    kind: str
    summary: str
    version: str
    help_text: str
    options: list[OptionSpec]
    help_returncode: int


@dataclass(slots=True)
class DocInfo:
    title: str
    path: Path
    content: str


@dataclass(slots=True)
class BundleInfo:
    schedulers: list[ProgramInfo]
    utilities: list[ProgramInfo]
    docs: list[DocInfo]


def discover_bundle() -> BundleInfo:
    schedulers = [
        _discover_program(Path(path), kind="scheduler")
        for path in sorted(glob.glob(SCX_BINARY_GLOB))
        if Path(path).is_file()
    ]
    utilities = [
        _discover_program(path, kind="utility")
        for path in UTILITY_PATHS
        if path.exists()
    ]
    docs = [
        DocInfo(title=path.name, path=path, content=path.read_text(encoding="utf-8"))
        for path in DOC_PATHS
        if path.exists()
    ]
    return BundleInfo(schedulers=schedulers, utilities=utilities, docs=docs)


def _discover_program(path: Path, *, kind: str) -> ProgramInfo:
    help_result = _capture_command(path, "-h")
    help_text = help_result["text"]
    if not help_text.strip():
        help_result = _capture_command(path, "--help")
        help_text = help_result["text"]

    version_result = _capture_command(path, "-V")
    version_text = version_result["text"].strip() if version_result["returncode"] == 0 else ""
    if not version_text:
        alt_version = _capture_command(path, "--version")
        version_text = alt_version["text"].strip() if alt_version["returncode"] == 0 else ""

    parsed = parse_help_text(help_text)
    summary = parsed.summary or version_text or f"No summary available for {path.name}"
    if help_result["returncode"] != 0 and not parsed.options:
        summary = f"{path.name} did not return normal help output on this host. See Raw Help for details."

    return ProgramInfo(
        name=path.name,
        path=path,
        kind=kind,
        summary=summary,
        version=version_text,
        help_text=help_text.strip(),
        options=parsed.options,
        help_returncode=help_result["returncode"],
    )


def _capture_command(path: Path, *args: str) -> dict[str, str | int]:
    try:
        env = dict(os.environ)
        env.setdefault("COLUMNS", "140")
        env.setdefault("LINES", "60")
        env.setdefault("NO_COLOR", "1")
        completed = subprocess.run(
            [str(path), *args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            env=env,
        )
        text = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
        return {
            "text": text,
            "returncode": completed.returncode,
        }
    except FileNotFoundError:
        return {
            "text": f"{path} was not found while collecting metadata.",
            "returncode": 127,
        }
    except subprocess.TimeoutExpired:
        return {
            "text": f"{path.name} {' '.join(args)} timed out while collecting metadata.",
            "returncode": 124,
        }
