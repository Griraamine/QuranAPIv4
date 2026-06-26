from __future__ import annotations

import json
import secrets
import stat
import time
import zipfile
from pathlib import Path

import httpx
from pydantic import BaseModel

from quran_video.rendering.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, probe_media

MAX_PART_BYTES = int(1.9 * 1024 * 1024 * 1024)
MAX_MANIFEST_BYTES = 10 * 1024 * 1024


class BackgroundManifestEntry(BaseModel):
    zip_part: str
    relative_path: str
    byte_size: int
    media_type: str
    sha256: str
    width: int
    height: int
    duration: float | None = None


class BackgroundManifest(BaseModel):
    schema_version: int
    release_tag: str
    entries: list[BackgroundManifestEntry]


def build_background_release(
    source_dir: Path, output_dir: Path, max_part_bytes: int = MAX_PART_BYTES
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"{path.relative_to(source_dir).as_posix()} is a symlink")
        if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
            files.append(path)
    entries: list[BackgroundManifestEntry] = []
    part_index = 1
    current_size = 0
    zip_path = output_dir / f"backgrounds-part-{part_index:03d}.zip"
    zip_handle = zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED)
    try:
        for path in files:
            probe = probe_media(path)
            relative = path.relative_to(source_dir).as_posix()
            if path.stat().st_size >= max_part_bytes:
                raise ValueError(f"{relative} exceeds the GitHub release asset limit")
            if current_size + path.stat().st_size > max_part_bytes and current_size > 0:
                zip_handle.close()
                part_index += 1
                current_size = 0
                zip_path = output_dir / f"backgrounds-part-{part_index:03d}.zip"
                zip_handle = zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED)
            zip_handle.write(path, relative)
            current_size += path.stat().st_size
            entries.append(
                BackgroundManifestEntry(
                    zip_part=zip_path.name,
                    relative_path=relative,
                    byte_size=path.stat().st_size,
                    media_type=probe.media_type,
                    sha256=probe.sha256,
                    width=probe.width,
                    height=probe.height,
                    duration=probe.duration_seconds,
                )
            )
    finally:
        zip_handle.close()
    manifest = {
        "schema_version": 1,
        "release_tag": "backgrounds-latest",
        "entries": [entry.model_dump() for entry in entries],
    }
    manifest_path = output_dir / "backgrounds-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_path


def extract_manifest_entry(
    zip_path: Path, entry: BackgroundManifestEntry, output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        info = archive.getinfo(entry.relative_path)
        target = (output_dir / entry.relative_path).resolve()
        root = output_dir.resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError("zip member escapes extraction directory") from error
        if any(part in {"", ".", ".."} for part in Path(entry.relative_path).parts):
            raise ValueError("zip member escapes extraction directory")
        if info.is_dir():
            raise ValueError("manifest entry points to a directory")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError("zip member is a symlink")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as destination:
            destination.write(source.read())
    probe = probe_media(target)
    if probe.sha256 != entry.sha256:
        target.unlink(missing_ok=True)
        raise ValueError("extracted background hash does not match manifest")
    return target


def load_background_manifest(path: Path) -> BackgroundManifest:
    return BackgroundManifest.model_validate_json(path.read_text(encoding="utf-8"))


def choose_manifest_entry(manifest: BackgroundManifest) -> BackgroundManifestEntry:
    if not manifest.entries:
        raise ValueError("background release manifest contains no media entries")
    return secrets.SystemRandom().choice(manifest.entries)


def download_random_release_background(
    repository: str,
    output_dir: Path,
    cache_dir: Path,
    *,
    tag: str = "backgrounds-latest",
) -> Path:
    if "/" not in repository:
        raise ValueError("GitHub repository must be in owner/name form")
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "backgrounds-manifest.json"
    _download_url(
        _release_asset_url(repository, tag, "backgrounds-manifest.json"),
        manifest_path,
        max_bytes=MAX_MANIFEST_BYTES,
    )
    manifest = load_background_manifest(manifest_path)
    if manifest.release_tag != tag:
        raise ValueError("background manifest release tag does not match requested tag")
    entry = choose_manifest_entry(manifest)
    zip_path = cache_dir / entry.zip_part
    _download_url(
        _release_asset_url(repository, tag, entry.zip_part),
        zip_path,
        max_bytes=MAX_PART_BYTES,
    )
    return extract_manifest_entry(zip_path, entry, output_dir)


def _release_asset_url(repository: str, tag: str, asset_name: str) -> str:
    return f"https://github.com/{repository}/releases/download/{tag}/{asset_name}"


def _download_url(url: str, target: Path, max_bytes: int) -> Path:
    temporary = target.with_suffix(target.suffix + ".tmp")
    for attempt in range(5):
        try:
            with (
                httpx.Client(
                    timeout=httpx.Timeout(60.0, read=300.0),
                    follow_redirects=True,
                    max_redirects=5,
                ) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and int(length) > max_bytes:
                    raise ValueError("release asset exceeds allowed size")
                total = 0
                with temporary.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("release asset exceeds allowed size")
                        handle.write(chunk)
            temporary.replace(target)
            return target
        except ValueError:
            temporary.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError):
            temporary.unlink(missing_ok=True)
            if attempt == 4:
                raise
            delay = min(2 ** (attempt + 1), 32) + secrets.randbelow(1000) / 1000
            time.sleep(delay)
    raise RuntimeError("unreachable background release download state")
