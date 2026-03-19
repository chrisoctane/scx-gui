from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(slots=True)
class OptionSpec:
    section: str
    raw_spec: str
    short_name: str | None
    long_name: str | None
    metavar: str | None
    description: str
    default: str | None
    possible_values: list[str]
    repeatable: bool

    @property
    def key(self) -> str:
        return self.long_name or self.short_name or self.raw_spec

    @property
    def display_name(self) -> str:
        if self.short_name and self.long_name:
            return f"{self.short_name}, {self.long_name}"
        return self.long_name or self.short_name or self.raw_spec

    @property
    def flag_name(self) -> str:
        return self.long_name or self.short_name or self.raw_spec

    @property
    def takes_value(self) -> bool:
        return self.metavar is not None

    @property
    def is_boolean_flag(self) -> bool:
        return self.metavar is None


@dataclass(slots=True)
class ParsedHelp:
    summary: str
    options: list[OptionSpec]


_DEFAULT_RE = re.compile(r"\[default[:=]\s*([^\]]+)\]")
_POSSIBLE_RE = re.compile(r"\[possible values:\s*([^\]]+)\]")


def parse_help_text(text: str) -> ParsedHelp:
    lines = text.splitlines()
    summary_lines: list[str] = []
    options: list[OptionSpec] = []
    current_section = "Options"
    current_option: dict[str, object] | None = None
    usage_seen = False

    def flush_current() -> None:
        nonlocal current_option
        if not current_option:
            return
        desc_lines = [line for line in current_option["description_lines"] if line]
        desc_text = " ".join(desc_lines).strip()
        default = _extract_single(_DEFAULT_RE, desc_text)
        possible_values_text = _extract_single(_POSSIBLE_RE, desc_text)
        possible_values = []
        if possible_values_text:
            possible_values = [item.strip() for item in possible_values_text.split(",") if item.strip()]
        clean_desc = _clean_description(desc_text).strip()
        options.append(
            OptionSpec(
                section=str(current_option["section"]),
                raw_spec=str(current_option["raw_spec"]),
                short_name=current_option.get("short_name"),  # type: ignore[arg-type]
                long_name=current_option.get("long_name"),  # type: ignore[arg-type]
                metavar=current_option.get("metavar"),  # type: ignore[arg-type]
                description=clean_desc,
                default=default,
                possible_values=possible_values,
                repeatable=bool(current_option["repeatable"]),
            )
        )
        current_option = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("Usage:"):
            usage_seen = True
            flush_current()
            continue

        if not usage_seen:
            summary_lines.append(stripped)
            continue

        if _is_section_header(stripped):
            flush_current()
            current_section = stripped[:-1]
            continue

        if _looks_like_option_line(stripped):
            flush_current()
            option = _parse_option_line(stripped, current_section)
            if option:
                current_option = option
                continue

        if current_option is not None:
            current_option["description_lines"].append(stripped)

    flush_current()

    summary = " ".join(summary_lines).strip()
    return ParsedHelp(summary=summary, options=options)


def _extract_single(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _clean_description(text: str) -> str:
    text = _DEFAULT_RE.sub("", text)
    text = _POSSIBLE_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_section_header(stripped: str) -> bool:
    if not stripped.endswith(":") or stripped.startswith("-") or stripped.startswith("Usage:"):
        return False
    candidate = stripped[:-1].strip()
    if not candidate:
        return False
    if len(candidate) > 48:
        return False
    if len(candidate.split()) > 5:
        return False
    if any(char in candidate for char in ".;,"):
        return False
    return True


def _looks_like_option_line(stripped: str) -> bool:
    return stripped.startswith("-")


def _parse_option_line(line: str, section: str) -> dict[str, object] | None:
    parts = re.split(r"\s{2,}", line, maxsplit=1)
    spec_text = parts[0].strip()
    inline_desc = parts[1].strip() if len(parts) > 1 else ""

    short_name: str | None = None
    long_name: str | None = None
    metavar: str | None = None
    repeatable = False

    for part in [item.strip() for item in spec_text.split(",")]:
        if not part:
            continue
        part_repeatable = part.endswith("...")
        if part_repeatable:
            part = part[:-3].rstrip()
            repeatable = True
        name_part, value_part = _split_option_piece(part)
        if value_part:
            metavar = value_part
            if metavar.endswith("..."):
                metavar = metavar[:-3].rstrip()
                repeatable = True
        if name_part.startswith("--"):
            long_name = name_part
        elif name_part.startswith("-"):
            short_name = name_part

    if not short_name and not long_name:
        return None

    return {
        "section": section,
        "raw_spec": spec_text,
        "short_name": short_name,
        "long_name": long_name,
        "metavar": metavar,
        "repeatable": repeatable,
        "description_lines": [inline_desc] if inline_desc else [],
    }


def _split_option_piece(part: str) -> tuple[str, str | None]:
    if "=" in part and part.startswith("-"):
        name_part, value_part = part.split("=", 1)
        return name_part.strip(), value_part.strip() or None
    if " <" in part:
        name_part, value_part = part.split(" <", 1)
        return name_part.strip(), f"<{value_part.strip()}"
    if " =" in part:
        name_part, value_part = part.split(" =", 1)
        return name_part.strip(), value_part.strip()
    return part.strip(), None
