"""Bundle the official Windows embeddable Python without a system installation."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

VERSION = "3.13.15"
URL = f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip"


def main():
    target = Path(sys.argv[1]).resolve()
    target.mkdir(parents=True, exist_ok=True)
    archive = target.parent / f"python-{VERSION}-embed-amd64.zip"
    if not archive.exists():
        print("Downloading official Windows Python runtime…", flush=True)
        with urllib.request.urlopen(URL, timeout=90) as response:
            archive.write_bytes(response.read(32 * 1024 * 1024))
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    with urllib.request.urlopen(URL + ".sigstore", timeout=60) as response:
        bundle = json.load(response)
    expected = base64.b64decode(bundle["messageSignature"]["messageDigest"]["digest"]).hex()
    if actual != expected:
        raise RuntimeError("Python archive does not match its official release digest.")
    runtime = target / "python"
    runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            if Path(info.filename).name != info.filename:
                raise RuntimeError("Unexpected nested path in the official embedded runtime.")
        source.extractall(runtime)
    (runtime / "python313._pth").write_text("python313.zip\n.\n..\n", encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    shutil.copytree(root / "partshelf", target / "partshelf", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "static"))
    shutil.copyfile(root / "desktop" / "backend_entry.py", target / "backend_entry.py")
    (target / "python-runtime.json").write_text(json.dumps({"version": VERSION, "source": URL, "sha256": actual}, indent=2))
    print("Windows runtime verified and bundled.", flush=True)


if __name__ == "__main__":
    main()
