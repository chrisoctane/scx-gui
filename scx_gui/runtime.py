from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import glob
import os
import shlex
import subprocess
import tempfile


DEFAULTS_PATH = Path("/etc/default/scx")
SERVICE_NAME = "scx.service"
ZYPPER_PATH = Path("/usr/bin/zypper")
SCX_PACKAGE_NAME = "scx"


@dataclass(slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def combined_output(self) -> str:
        return "\n".join(part for part in [self.stdout.strip(), self.stderr.strip()] if part).strip()


@dataclass(slots=True)
class ScxConfig:
    scheduler: str
    flags_raw: str
    original_lines: list[str]
    path: Path = DEFAULTS_PATH


@dataclass(slots=True)
class ServiceState:
    load_state: str = "unknown"
    active_state: str = "unknown"
    sub_state: str = "unknown"
    unit_file_state: str = "unknown"
    fragment_path: str = ""
    exec_main_pid: str = "0"
    sched_ext_state: str = "unknown"
    sched_ext_ops: list[str] = None  # type: ignore[assignment]
    environment: str = ""
    environment_files: str = ""
    drop_in_paths: str = ""
    exec_start: str = ""
    active_scheduler: str = ""
    scheduler_override: str = ""
    flags_override: str = ""
    uses_override_placeholders: bool = False

    def __post_init__(self) -> None:
        if self.sched_ext_ops is None:
            self.sched_ext_ops = []

    @property
    def override_active(self) -> bool:
        return bool(self.scheduler_override or self.flags_override)


def read_scx_config(path: Path = DEFAULTS_PATH) -> ScxConfig:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    scheduler = ""
    flags_raw = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = _shell_unquote(value.strip())
        if key == "SCX_SCHEDULER":
            scheduler = value
        elif key == "SCX_FLAGS":
            flags_raw = value
    return ScxConfig(
        scheduler=scheduler,
        flags_raw=flags_raw,
        original_lines=lines,
        path=path,
    )


def render_scx_config(config: ScxConfig) -> str:
    lines = list(config.original_lines)
    rendered_scheduler = f"SCX_SCHEDULER={shlex.quote(config.scheduler)}"
    rendered_flags = f"SCX_FLAGS={shlex.quote(config.flags_raw)}"

    lines = _replace_or_append(lines, "SCX_SCHEDULER", rendered_scheduler)
    lines = _replace_or_append(lines, "SCX_FLAGS", rendered_flags)
    if not lines:
        lines = [rendered_scheduler, "", rendered_flags]
    return "\n".join(lines).rstrip() + "\n"


def write_scx_config(config: ScxConfig) -> CommandResult:
    content = render_scx_config(config)
    if os.access(config.path, os.W_OK):
        config.path.write_text(content, encoding="utf-8")
        return CommandResult(args=[str(config.path)], returncode=0, stdout="Updated config.", stderr="")

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write(content)
            tmp_path = handle.name
        result = run_command(["/usr/bin/install", "-m", "0644", tmp_path, str(config.path)], require_root=True)
        return result
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def read_service_state() -> ServiceState:
    show = run_command(
        [
            "/usr/bin/systemctl",
            "show",
            SERVICE_NAME,
            "--property=LoadState,ActiveState,SubState,UnitFileState,FragmentPath,ExecMainPID,Environment,EnvironmentFiles,DropInPaths,ExecStart",
        ]
    )
    values: dict[str, str] = {}
    for line in show.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    environment = values.get("Environment", "")
    environment_map = _parse_systemd_environment(environment)
    exec_start = values.get("ExecStart", "")
    return ServiceState(
        load_state=values.get("LoadState", "unknown"),
        active_state=values.get("ActiveState", "unknown"),
        sub_state=values.get("SubState", "unknown"),
        unit_file_state=values.get("UnitFileState", "unknown"),
        fragment_path=values.get("FragmentPath", ""),
        exec_main_pid=values.get("ExecMainPID", "0"),
        sched_ext_state=_read_sched_ext_state(),
        sched_ext_ops=_read_sched_ext_ops(),
        environment=environment,
        environment_files=values.get("EnvironmentFiles", ""),
        drop_in_paths=values.get("DropInPaths", ""),
        exec_start=exec_start,
        active_scheduler=_read_active_scheduler_name(values.get("ExecMainPID", "0")),
        scheduler_override=environment_map.get("SCX_SCHEDULER_OVERRIDE", ""),
        flags_override=environment_map.get("SCX_FLAGS_OVERRIDE", ""),
        uses_override_placeholders=(
            "SCX_SCHEDULER_OVERRIDE" in exec_start or "SCX_FLAGS_OVERRIDE" in exec_start
        ),
    )


def read_service_journal(lines: int = 120) -> str:
    result = run_command(
        ["/usr/bin/journalctl", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager", "--output=short-precise"]
    )
    output = result.combined_output
    return output or "No journal output available."


def run_service_action(action: str) -> CommandResult:
    return run_command(["/usr/bin/systemctl", action, SERVICE_NAME], require_root=True)


def can_install_scx_package() -> bool:
    return ZYPPER_PATH.exists()


def install_scx_package() -> CommandResult:
    if not can_install_scx_package():
        return CommandResult(
            args=[str(ZYPPER_PATH), "install", SCX_PACKAGE_NAME],
            returncode=127,
            stdout="",
            stderr="Automatic installation is only supported on systems with zypper available.",
        )
    return run_command(
        [
            str(ZYPPER_PATH),
            "--non-interactive",
            "install",
            "--auto-agree-with-licenses",
            SCX_PACKAGE_NAME,
        ],
        require_root=True,
        timeout=1200,
    )


def run_command(args: list[str], *, require_root: bool = False, timeout: int = 30) -> CommandResult:
    full_args = ["/usr/bin/pkexec", *args] if require_root else list(args)
    try:
        completed = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            args=full_args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except FileNotFoundError as exc:
        missing = exc.filename or full_args[0]
        return CommandResult(
            args=full_args,
            returncode=127,
            stdout="",
            stderr=f"{missing} was not found.",
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _coerce_subprocess_text(exc.stdout)
        stderr = _coerce_subprocess_text(exc.stderr)
        return CommandResult(
            args=full_args,
            returncode=124,
            stdout=stdout,
            stderr=f"{stderr}\nCommand timed out after {timeout} seconds.".strip(),
        )


def open_in_terminal(command: str) -> tuple[bool, str]:
    shell_command = ["bash", "-lc", command]
    for candidate in _terminal_candidates():
        try:
            subprocess.Popen([*candidate, *shell_command])
            return True, f"Opened in terminal using {candidate[0]}."
        except FileNotFoundError:
            continue
    return False, "No supported terminal emulator was found."


def _terminal_candidates() -> list[list[str]]:
    return [
        ["/usr/bin/xdg-terminal-exec"],
        ["/usr/bin/konsole", "-e"],
        ["/usr/bin/gnome-terminal", "--"],
        ["/usr/bin/xfce4-terminal", "-x"],
        ["/usr/bin/kitty"],
        ["/usr/bin/alacritty", "-e"],
        ["/usr/bin/xterm", "-e"],
        ["/usr/bin/x-terminal-emulator", "-e"],
    ]


def _replace_or_append(lines: list[str], key: str, new_line: str) -> list[str]:
    replaced = False
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}=") and not replaced:
            updated.append(new_line)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(new_line)
    return updated


def _shell_unquote(value: str) -> str:
    try:
        parts = shlex.split(value)
    except ValueError:
        return value.strip("\"'")
    if not parts:
        return ""
    return " ".join(parts)


def _coerce_subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_systemd_environment(value: str) -> dict[str, str]:
    if not value.strip():
        return {}
    entries: dict[str, str] = {}
    try:
        tokens = shlex.split(value)
    except ValueError:
        tokens = value.split()
    for token in tokens:
        if "=" not in token:
            continue
        key, raw_value = token.split("=", 1)
        entries[key] = raw_value
    return entries


def _read_sched_ext_state() -> str:
    path = Path("/sys/kernel/sched_ext/state")
    if not path.exists():
        return "unknown"
    return path.read_text(encoding="utf-8").strip() or "unknown"


def _read_sched_ext_ops() -> list[str]:
    ops: list[str] = []
    for path in sorted(glob.glob("/sys/kernel/sched_ext/**/ops", recursive=True)):
        try:
            content = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if content and content not in ops:
            ops.append(content)
    return ops


def _read_active_scheduler_name(exec_main_pid: str) -> str:
    if not exec_main_pid.isdigit() or exec_main_pid == "0":
        return ""
    exe_path = Path(f"/proc/{exec_main_pid}/exe")
    try:
        resolved_name = exe_path.resolve().name
    except OSError:
        resolved_name = ""
    if resolved_name.startswith("scx_"):
        return resolved_name

    comm_path = Path(f"/proc/{exec_main_pid}/comm")
    try:
        comm_name = comm_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return comm_name if comm_name.startswith("scx_") else ""
