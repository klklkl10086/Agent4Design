"""Import XMI artifacts with the Rhapsody XMI Toolkit."""

from pathlib import Path
import os
import subprocess
from typing import List, Optional, Union

from pydantic import BaseModel


class XMIImportResult(BaseModel):
    """Structured result returned by an XMI Toolkit invocation."""

    xmi_path: str
    success: bool
    return_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    log_path: Optional[str] = None


def _write_log(log_dir: Union[str, Path], xmi_path: Path, stdout: str, stderr: str) -> str:
    output_dir = Path(log_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = (output_dir / f"{xmi_path.name}.log").resolve()
    log_path.write_text(f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}", encoding="utf-8")
    return str(log_path)


def import_xmi_file(
    xmi_path: Union[str, Path],
    toolkit_bat: Union[str, Path],
    log_dir: Union[str, Path] = "xmi_import_logs",
    timeout: int = 600,
) -> XMIImportResult:
    """Import one XMI file and capture enough detail to diagnose failures."""
    source_path = Path(xmi_path).resolve()
    toolkit_path = Path(toolkit_bat).resolve()

    if not toolkit_path.is_file():
        return XMIImportResult(
            xmi_path=str(source_path),
            success=False,
            stderr=f"Toolkit not found: {toolkit_path}",
        )
    if not source_path.is_file():
        return XMIImportResult(
            xmi_path=str(source_path),
            success=False,
            stderr=f"XMI file not found: {source_path}",
        )

    command = [
        str(toolkit_path),
        "-silent",
        "true",
        "-mode",
        "IMPORT",
        "-format",
        "uml21",
        "-xmi",
        str(source_path),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or f"Import timed out after {timeout}s"
        log_path = _write_log(log_dir, source_path, stdout, stderr)
        return XMIImportResult(
            xmi_path=str(source_path),
            success=False,
            stdout=stdout,
            stderr=stderr,
            log_path=log_path,
        )
    except Exception as exc:
        stderr = str(exc)
        log_path = _write_log(log_dir, source_path, "", stderr)
        return XMIImportResult(
            xmi_path=str(source_path),
            success=False,
            stderr=stderr,
            log_path=log_path,
        )

    log_path = None
    if result.returncode != 0:
        log_path = _write_log(log_dir, source_path, result.stdout, result.stderr)

    return XMIImportResult(
        xmi_path=str(source_path),
        success=result.returncode == 0,
        return_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        log_path=log_path,
    )


def import_xmi_folder(
    xmi_folder: Union[str, Path],
    toolkit_bat: Union[str, Path],
    log_dir: Union[str, Path] = "xmi_import_logs",
    timeout: int = 600,
) -> List[XMIImportResult]:
    """Import every XMI file in a folder in a deterministic order."""
    folder_path = Path(xmi_folder).resolve()
    if not folder_path.is_dir():
        raise FileNotFoundError(f"XMI folder not found: {folder_path}")

    return [
        import_xmi_file(xmi_path, toolkit_bat, log_dir, timeout)
        for xmi_path in sorted(folder_path.glob("*.xmi"))
    ]
