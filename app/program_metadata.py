from __future__ import annotations

import math
import re


PROGRAM_METADATA_PREFIX_BYTES = 64 * 1024
_VERSION_RE = re.compile(r"^\s*\(\s*MPS-METADATA-V1\s*\)\s*$", re.IGNORECASE | re.MULTILINE)
_TOOLS_RE = re.compile(r"^\s*\(\s*MPS-TOOLS\s*:?\s*([0-9,\s]*)\)\s*$", re.IGNORECASE | re.MULTILINE)
_CYCLE_RE = re.compile(
    r"^\s*\(\s*MPS-CYCLE-SECONDS\s*:?\s*([0-9]+(?:\.[0-9]+)?)\s*\)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BASIS_RE = re.compile(r"^\s*\(\s*MPS-CYCLE-BASIS\s*:?\s*([^\r\n)]*)\)\s*$", re.IGNORECASE | re.MULTILINE)
_STATUS_FILE_RE = re.compile(r"^\s*\(\s*MPS-STATUS-FILE\s*:?\s*([^\r\n)]*)\)\s*$", re.IGNORECASE | re.MULTILINE)
_WCS_RE = re.compile(r"^\s*\(\s*MPS-WCS\s*:?\s*([^\r\n)]*)\)\s*$", re.IGNORECASE | re.MULTILINE)
_WCS_VALUE_RE = re.compile(r"^G(?:5[4-9]|59\.[1-3]|54\.1\s+P(?:[1-9]\d{0,2}|[1-4]\d{3}|500))$", re.IGNORECASE)
_GCODE_WCS_RE = re.compile(
    r"(?<![A-Z0-9.])(G(?:5[4-9]|59\.[1-3]|54\.1\s+P(?:[1-9]\d{0,2}|[1-4]\d{3}|500)))(?![A-Z0-9.])",
    re.IGNORECASE,
)


def unavailable_program_metadata(detail: str) -> dict[str, object]:
    return {
        "program_tools": [],
        "program_tool_counts": {},
        "program_wcs": [],
        "expected_cycle_seconds": None,
        "program_metadata_state": "unavailable",
        "program_metadata_detail": detail,
        "program_cycle_basis": None,
        "mill_status_file": None,
    }


def _work_coordinate_systems(text: str, header_match: re.Match[str] | None) -> list[str]:
    values = header_match.group(1).split(",") if header_match else _GCODE_WCS_RE.findall(text)
    systems: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", value.strip().upper())
        if normalized and _WCS_VALUE_RE.fullmatch(normalized) and normalized not in systems:
            systems.append(normalized)
    return systems


def parse_program_metadata(text: str) -> dict[str, object]:
    """Parse the bounded, versioned comment header emitted by the Fusion post."""
    if not _VERSION_RE.search(text):
        return unavailable_program_metadata("This program does not contain an MPS metadata header. Repost it with the updated Fusion post.")

    tools_match = _TOOLS_RE.search(text)
    cycle_match = _CYCLE_RE.search(text)
    if not tools_match or not cycle_match:
        return unavailable_program_metadata("The MPS metadata header is incomplete.")

    try:
        tool_numbers = [int(value.strip()) for value in tools_match.group(1).split(",") if value.strip()]
        tools = sorted(set(tool_numbers))
        cycle_seconds = float(cycle_match.group(1))
    except ValueError:
        return unavailable_program_metadata("The MPS metadata header contains an invalid number.")
    if any(tool < 1 or tool > 999 for tool in tools):
        return unavailable_program_metadata("The MPS metadata header contains a tool outside T1-T999.")
    if not math.isfinite(cycle_seconds) or cycle_seconds < 0 or cycle_seconds > 365 * 24 * 60 * 60:
        return unavailable_program_metadata("The MPS cycle-time estimate is outside the supported range.")

    basis_match = _BASIS_RE.search(text)
    status_file_match = _STATUS_FILE_RE.search(text)
    wcs_match = _WCS_RE.search(text)
    return {
        "program_tools": [f"T{tool}" for tool in tools],
        "program_tool_counts": {f"T{tool}": tool_numbers.count(tool) for tool in tools},
        "program_wcs": _work_coordinate_systems(text, wcs_match),
        "expected_cycle_seconds": int(math.ceil(cycle_seconds)),
        "program_metadata_state": "parsed",
        "program_metadata_detail": "Metadata read from the assigned G-code header.",
        "program_cycle_basis": basis_match.group(1).strip() if basis_match else None,
        "mill_status_file": status_file_match.group(1).strip() if status_file_match else None,
    }
