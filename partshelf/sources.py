"""Public GitHub and direct-download source adapters."""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import socket
import tempfile
import time
from urllib import error, parse, request

from . import __version__
from .files import MAX_BYTES, PartShelfError, load_source, sha
from .progress import report


def public_url(url):
    parsed = parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise PartShelfError("Use a public HTTPS link without embedded credentials.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PartShelfError(f"Could not resolve {parsed.hostname}.") from exc
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise PartShelfError("Link imports only support public internet addresses.")
    return parsed


def provenance_url(url):
    # Signed download URLs can include credentials in their query parameters.
    # Preserve the public origin/path; never put transient query tokens in packages.
    parsed = parse.urlsplit(url)
    return parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class PublicRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, label="Downloading library…"):
    report("download", label, 0, unit="bytes")
    public_url(url)
    opener = request.build_opener(PublicRedirect())
    try:
        req = request.Request(url, headers={"User-Agent": "KiCad-Component-Packager/" + __version__, "Accept": "application/vnd.github+json, application/octet-stream;q=0.9, */*;q=0.8"})
        with opener.open(req, timeout=25) as response:
            header = response.headers.get("Content-Length", "")
            expected = int(header) if header.isdigit() and int(header) > 0 else None
            if expected and expected > MAX_BYTES:
                raise PartShelfError("Download exceeds the 128 MB limit.")
            chunks, size, start = [], 0, time.monotonic()
            report("download", label, 0, expected, "bytes")
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES or time.monotonic() - start > 120:
                    raise PartShelfError("Download exceeded its size or time limit.")
                chunks.append(chunk)
                if expected and size > expected:
                    expected = None
                report("download", label, size, expected, "bytes")
            report("download", label, size, expected, "bytes")
            return b"".join(chunks), response.headers.get("Content-Type", ""), response.geturl()
    except (OSError, error.URLError, ValueError) as exc:
        if isinstance(exc, PartShelfError):
            raise
        raise PartShelfError(f"Could not download this link: {exc}") from exc


def fetch_source(url):
    parsed = public_url(url)
    provenance = {"url": provenance_url(url)}
    if parsed.hostname.lower() in {"github.com", "www.github.com"}:
        parts = [parse.unquote(p) for p in parsed.path.strip("/").split("/")]
        if len(parts) < 2:
            raise PartShelfError("Paste a GitHub repository, folder or library-file URL.")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        api = "https://api.github.com/repos/" + parse.quote(owner, safe="") + "/" + parse.quote(repo, safe="")
        if len(parts) > 2 and parts[2] not in {"tree", "blob"}:
            # Release/download URLs are ordinary files, not repository browsing URLs.
            return direct_source(url)
        if len(parts) > 3:
            ref, subpath = parts[3], "/".join(parts[4:])
        else:
            data, _, _ = download(api, label="Finding GitHub repository…")
            ref, subpath = json.loads(data)["default_branch"], ""
        data, _, _ = download(api + "/commits/" + parse.quote(ref, safe=""), label="Resolving GitHub revision…")
        commit = json.loads(data)["sha"]
        archive_url = f"https://codeload.github.com/{parse.quote(owner, safe='')}/{parse.quote(repo, safe='')}/zip/{commit}"
        data, _, final_url = download(archive_url)
        with tempfile.TemporaryDirectory(prefix="partshelf-download-") as temp:
            path = Path(temp) / "repository.zip"
            path.write_bytes(data)
            archived = load_source(path)
        files = {name.split("/", 1)[1]: data for name, data in archived.items() if "/" in name}
        if subpath:
            files = {name: data for name, data in files.items() if name == subpath or name.startswith(subpath.rstrip("/") + "/") or ("/" not in name and name.lower().startswith(("license", "readme", "notice", "copying")))}
        if not files:
            raise PartShelfError("The selected repository path contains no files.")
        provenance.update({"kind": "github", "repository": f"{owner}/{repo}", "ref": ref, "commit": commit, "archive_sha256": sha(data), "download_url": provenance_url(final_url)})
        return files, provenance
    return direct_source(url)


def direct_source(url):
    data, content_type, final_url = download(url)
    if "text/html" in content_type or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise PartShelfError("This link opens a web page, not a library download. For DigiKey / Ultra Librarian, download the KiCad ZIP after signing in, then import that ZIP here. Product-page and account connectors are not yet implemented.")
    filename = Path(parse.unquote(parse.urlsplit(final_url).path)).name
    if data.startswith(b"PK\x03\x04"):
        filename = "download.zip"
    if not filename:
        raise PartShelfError("The download link has no recognizable filename.")
    with tempfile.TemporaryDirectory(prefix="partshelf-download-") as temp:
        path = Path(temp) / filename
        path.write_bytes(data)
        files = load_source(path)
    return files, {"kind": "download", "url": provenance_url(url), "download_url": provenance_url(final_url), "sha256": sha(data)}
