import hashlib
import io
import os
import stat
import zipfile
from pathlib import Path


EXCLUDED = {".git", ".agent", ".native", "node_modules", "__pycache__", ".venv"}


def safe_path(root: Path, name: str) -> Path:
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        raise ValueError("Invalid relative file path")
    parts = Path(name).parts
    if any(part in {".", ".."} | EXCLUDED or part.startswith(".") for part in parts):
        raise ValueError("Hidden, internal and parent paths are not accessible")
    target = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink() or (
            hasattr(current, "is_junction") and current.is_junction()
        ):
            raise ValueError("Symbolic links are not accessible")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes workspace")
    return target


def inventory(root: Path):
    result = []
    for parent, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [
            d
            for d in directories
            if not d.startswith(".")
            and d not in EXCLUDED
            and not (Path(parent) / d).is_symlink()
            and not (Path(parent) / d).is_junction()
        ]
        for name in filenames:
            try:
                path = safe_path(
                    root, (Path(parent) / name).relative_to(root).as_posix()
                )
                if path.is_file():
                    result.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "size": path.stat().st_size,
                        }
                    )
            except (ValueError, OSError):
                continue
            if len(result) >= 5000:
                return sorted(result, key=lambda x: x["path"])
    return sorted(result, key=lambda x: x["path"])


def digest(path: Path):
    if not path.is_file():
        return None
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def import_zip(root: Path, raw: bytes, limit: int):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > limit:
            raise ValueError("Archive exceeds expanded size or file count limit")
        checked = []
        for entry in entries:
            if entry.is_dir():
                continue
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Archive links are not allowed")
            checked.append((entry, safe_path(root, entry.filename)))
        for entry, target in checked:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
    return len(checked)
