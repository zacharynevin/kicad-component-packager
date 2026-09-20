"""Use KiCad's own importers, never an independently guessed translation."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from .files import PartShelfError, contained, sha
from .progress import report

_cli_lock = threading.Lock()  # KiCad CLI instances share an application lock.


def find_cli() -> str | None:
    configured = os.environ.get("KICAD_COMPONENT_PACKAGER_KICAD_CLI") or os.environ.get("PARTSHELF_KICAD_CLI")
    if configured:
        return configured if Path(configured).is_file() else None
    candidates = [shutil.which("kicad-cli"), "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"]
    candidates.extend(str(p) for p in Path("C:/Program Files/KiCad").glob("*/bin/kicad-cli.exe"))
    return next((str(p) for p in candidates if p and Path(p).is_file()), None)


def run_cli(arguments: list[str], timeout: int = 240) -> str:
    cli = find_cli()
    if not cli:
        raise PartShelfError("Install KiCad 10 to import Eagle or Altium libraries, or set KICAD_COMPONENT_PACKAGER_KICAD_CLI to kicad-cli.")
    try:
        with _cli_lock:
            result = subprocess.run([cli, *arguments], capture_output=True, text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PartShelfError(f"KiCad conversion could not finish: {exc}") from exc
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode:
        raise PartShelfError(f"KiCad could not import this library (exit {result.returncode}). {output[:1800]}")
    return output


def normalize(files: dict[str, bytes]):
    """Return native assets, conversion reports, and a source-provenance map."""
    native = dict(files)
    reports, origins = [], {}
    foreign = []
    for name, data in files.items():
        suffix = Path(name).suffix.lower()
        eagle = suffix in {".lbr", ".xml"} and b"<eagle" in data[:4096]
        if suffix == ".lbr" and not eagle:
            raise PartShelfError(f"{name}: binary Eagle libraries are not supported by KiCad. Eagle 6+ XML libraries are supported.")
        if eagle or suffix in {".schlib", ".pcblib", ".intlib"} or (suffix == ".lib" and data.startswith(b"EESchema-LIBRARY")):
            foreign.append((name, eagle))
    if not foreign:
        return native, reports, origins
    report("convert", "Checking KiCad converter…")
    version = run_cli(["version"], timeout=15).splitlines()[0].strip()
    try:
        major = int(version.split(".")[0])
    except ValueError as exc:
        raise PartShelfError("Cannot determine the installed KiCad version.") from exc
    if major < 10:
        raise PartShelfError("Automatic library conversion requires KiCad 10 or newer.")
    with tempfile.TemporaryDirectory(prefix="partshelf-import-") as temp:
        root = Path(temp)
        report("convert", "Preparing libraries for KiCad…")
        for name, data in files.items():
            path = contained(root / "input", name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for index, (name, eagle) in enumerate(foreign, 1):
            source = root / "input" / name
            suffix = source.suffix.lower()
            key = sha(name.encode())[:12]
            output = root / "output" / key
            output.mkdir(parents=True)
            logs = []
            current = f"{name} · library {index} of {len(foreign)}"
            if eagle or suffix in {".schlib", ".intlib", ".lib"}:
                report("convert-symbols", "Converting symbols with KiCad…", current=current)
                logs.append(run_cli(["sym", "upgrade", "--force", "--output", str(output / "symbols.kicad_sym"), str(source)]))
            if eagle or suffix in {".pcblib", ".intlib"}:
                report("convert-footprints", "Converting footprints with KiCad…", current=current)
                logs.append(run_cli(["fp", "upgrade", "--force", "--output", str(output / (source.stem + ".pretty")), str(source)]))
            report("convert-read", "Reading converted files…", current=current)
            generated = list(output.rglob("*"))
            for path in generated:
                if path.is_file():
                    relative = "converted/" + path.relative_to(root / "output").as_posix()
                    native[relative] = path.read_bytes()
                    origins[relative] = name
            notices = [line.strip() for log in logs for line in log.splitlines() if line.strip()]
            reports.append({"source": name, "format": "Eagle XML" if eagle else ("Altium" if suffix != ".lib" else "KiCad legacy"), "converter": f"KiCad {version}", "messages": notices[:40]})
    return native, reports, origins
