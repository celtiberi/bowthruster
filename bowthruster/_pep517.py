"""Zero-dependency PEP 517 / PEP 660 build backend (`bowthruster._pep517`)."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST_INFO = "bowthruster-0.1.0.dist-info"
WHEEL_NAME = "bowthruster-0.1.0-py3-none-any.whl"


def get_requires_for_build_wheel(config_settings: dict[str, Any] | None = None) -> list[str]:
    _ = config_settings
    return []


def get_requires_for_build_editable(config_settings: dict[str, Any] | None = None) -> list[str]:
    _ = config_settings
    return []


def _record_line(arcname: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"{arcname},sha256={digest},{len(data)}"


def _write_dist_info(zf: zipfile.ZipFile, records: list[str]) -> None:
    files = {
        f"{DIST_INFO}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: bowthruster\n"
            "Version: 0.1.0\n"
            "Summary: Architecture memory, focused code lookup, and pre-commit verification for AI coding agents.\n"
            "Requires-Python: >=3.11\n"
        ).encode("utf-8"),
        f"{DIST_INFO}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: bowthruster._pep517\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode("utf-8"),
        f"{DIST_INFO}/entry_points.txt": (
            "[console_scripts]\nbowthruster = bowthruster.cli:main\nbowthruster-mcp = bowthruster.mcp_server:main\n"
        ).encode("utf-8"),
    }
    for arcname, data in files.items():
        zf.writestr(arcname, data)
        records.append(_record_line(arcname, data))
    rec_arc = f"{DIST_INFO}/RECORD"
    records.append(f"{rec_arc},,")
    zf.writestr(rec_arc, ("\n".join(records) + "\n").encode("utf-8"))


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _ = (config_settings, metadata_directory)
    whl_path = Path(wheel_directory) / WHEEL_NAME
    records: list[str] = []
    with zipfile.ZipFile(whl_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        pth_data = (str(ROOT) + "\n").encode("utf-8")
        zf.writestr("bowthruster.pth", pth_data)
        records.append(_record_line("bowthruster.pth", pth_data))
        _write_dist_info(zf, records)
    return WHEEL_NAME


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _ = (config_settings, metadata_directory)
    whl_path = Path(wheel_directory) / WHEEL_NAME
    records: list[str] = []
    pkg_root = ROOT / "bowthruster"
    with zipfile.ZipFile(whl_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(pkg_root.rglob("*.py")):
            arcname = p.relative_to(ROOT).as_posix()
            data = p.read_bytes()
            zf.writestr(arcname, data)
            records.append(_record_line(arcname, data))
        _write_dist_info(zf, records)
    return WHEEL_NAME
