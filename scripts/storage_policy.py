"""Optional student-local E-drive guard; public installations are portable."""
import os
from pathlib import Path, PureWindowsPath


def validate_storage(paths, environ, platform):
    """Pure validation also allows tests of Windows paths without writing there."""
    if environ.get("BDF_REQUIRE_E_STORAGE") != "1":
        return
    if platform != "nt":
        raise ValueError("Student-local E-drive policy requires Windows")
    checked = [str(p) for p in paths]
    checked += [environ.get(k, "") for k in ("TEMP", "TMP", "MPLCONFIGDIR", "PIP_CACHE_DIR")]
    for value in checked:
        p = PureWindowsPath(value)
        if not p.is_absolute() or p.drive.upper() != "E:":
            raise ValueError("Student-local policy: use E-drive output and Set_GROUPCW_Runtime.ps1")


def ensure_storage(*paths):
    validate_storage([Path(p).resolve() for p in paths], os.environ, os.name)
