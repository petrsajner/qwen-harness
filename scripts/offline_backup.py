"""Create, verify, inspect, and restore a portable Qwen Harness offline backup.

The script deliberately uses only the Python standard library so restore can run
before project dependencies are installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

FORMAT_VERSION = 2
MANIFEST_NAME = "manifest.json"
MARKER_NAME = "offline-backup-path.txt"
BACKUP_PREFIX = "QwenHarness-Offline-Backup"
DEPENDENCY_ARCHIVE = Path("python-dependencies/site-packages.zip")
CHUNK_SIZE = 16 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe manifest path: {raw}")
    return path


def _copy_with_hash(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    digest = hashlib.sha256()
    try:
        with open(source, "rb") as src, open(temporary, "wb") as dst:
            for chunk in iter(lambda: src.read(CHUNK_SIZE), b""):
                digest.update(chunk)
                dst.write(chunk)
        shutil.copystat(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return digest.hexdigest()


def _version(root: Path) -> str:
    for path in (root / "version.txt", root / "installer" / "version.txt"):
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    return "unknown"


def _runtime_sources(root: Path) -> list[tuple[Path, Path, str]]:
    runtime = root / "runtime"
    sources: list[tuple[Path, Path, str]] = []
    models = runtime / "models"
    if models.is_dir():
        for source in sorted(models.rglob("*")):
            if (source.is_file() and ".cache" not in source.parts
                    and not source.name.endswith((".partial", ".incomplete"))):
                rel = Path("payload") / "runtime" / "models" / source.relative_to(models)
                sources.append((source, rel, "models"))
    llama = runtime / "llama"
    if llama.is_dir():
        for source in sorted(llama.rglob("*")):
            if source.is_file():
                rel = Path("payload") / "runtime" / "llama" / source.relative_to(llama)
                sources.append((source, rel, "llama"))
    selection = runtime / "model-selection.txt"
    if selection.is_file():
        sources.append((selection, Path("payload/runtime/model-selection.txt"), "settings"))
    version = _version(root)
    installer = root / "dist" / f"QwenHarness-Setup-{version}.exe"
    if installer.is_file():
        sources.append((installer, Path(installer.name), "installer"))
    return sources


def _write_readme(path: Path) -> None:
    path.write_text(
        "QWEN HARNESS OFFLINE BACKUP\n"
        "===========================\n\n"
        "This folder contains local model files, vision projectors, llama.cpp/CUDA\n"
        "runtime files, and a snapshot of the already installed Python packages.\n"
        "Keep manifest.json with the payload and python-dependencies folders.\n\n"
        "On another Windows PC:\n"
        "1. Install Python 3.12 (the only external prerequisite).\n"
        "2. Run the QwenHarness-Setup executable included in this folder.\n"
        "   The installer detects manifest.json beside itself automatically.\n"
        "   With another compatible Setup.exe, use the Start Menu command\n"
        "   'Set up from offline backup' and select this folder.\n"
        "4. Normal setup uses internet sources first. If a model, llama.cpp, or\n"
        "   Python package cannot be obtained online, it restores that component\n"
        "   from this backup. The explicit Start Menu offline command restores\n"
        "   this backup first instead.\n\n"
        "Run `python scripts/offline_backup.py verify --backup <this-folder>`\n"
        "from an installed/source Qwen Harness copy to verify every SHA-256.\n",
        encoding="utf-8")


def _create_dependency_archive(source: Path, target: Path) -> tuple[int, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    files = [path for path in sorted(source.rglob("*"))
             if path.is_file() and "__pycache__" not in path.parts
             and path.suffix.lower() not in {".pyc", ".pyo"}]
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as archive:
            for index, path in enumerate(files, 1):
                if index == 1 or index % 1000 == 0 or index == len(files):
                    print(f"  Python dependencies: {index}/{len(files)} files")
                archive.write(path, path.relative_to(source).as_posix())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target.stat().st_size, sha256_file(target)


def create_backup(root: Path, output: Path) -> dict[str, Any]:
    root = root.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Backup target already exists: {output}")
    sources = _runtime_sources(root)
    model_sources = [item for item in sources if item[2] == "models"
                     and item[0].suffix.lower() == ".gguf"]
    if not model_sources:
        raise FileNotFoundError(f"No downloaded GGUF files found in {root / 'runtime/models'}")
    if not any(item[0].name.lower() == "llama-server.exe" for item in sources):
        raise FileNotFoundError(f"llama-server.exe not found in {root / 'runtime/llama'}")
    try:
        output.relative_to(root / "runtime")
    except ValueError:
        pass
    else:
        raise ValueError("Backup target must not be inside runtime")

    site_packages = root / ".venv" / "Lib" / "site-packages"
    if not site_packages.is_dir():
        raise FileNotFoundError(
            f"Installed Python dependencies not found: {site_packages}")

    output.parent.mkdir(parents=True, exist_ok=True)
    runtime_bytes = sum(source.stat().st_size for source, _, _ in sources)
    dependency_bytes = sum(path.stat().st_size for path in site_packages.rglob("*")
                           if path.is_file() and "__pycache__" not in path.parts
                           and path.suffix.lower() not in {".pyc", ".pyo"})
    total_bytes = runtime_bytes + dependency_bytes
    reserve = 256 * 2**20
    free = shutil.disk_usage(output.parent).free
    if free < total_bytes + reserve:
        raise OSError(
            f"Not enough free space on {output.parent}: need about "
            f"{(total_bytes + reserve) / 2**30:.1f} GiB, available {free / 2**30:.1f} GiB")

    building = output.with_name(output.name + ".building")
    if building.exists():
        shutil.rmtree(building)
    building.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    try:
        requirements = root / "requirements.txt"
        if not requirements.is_file():
            raise FileNotFoundError(f"Missing requirements.txt: {requirements}")
        shutil.copy2(requirements, building / "requirements.txt")
        _write_readme(building / "README-OFFLINE.txt")
        print(f"[BACKUP] Copying {len(sources)} runtime files ({runtime_bytes / 2**30:.1f} GiB)")
        for index, (source, relative, component) in enumerate(sources, 1):
            size = source.stat().st_size
            print(f"  [{index}/{len(sources)}] {relative} ({size / 2**20:.1f} MiB)")
            digest = _copy_with_hash(source, building / relative)
            records.append({"path": relative.as_posix(), "size": size,
                            "sha256": digest, "component": component})

        print("[BACKUP] Copying installed Python dependencies from .venv ...")
        dependency_target = building / DEPENDENCY_ARCHIVE
        size, digest = _create_dependency_archive(site_packages, dependency_target)
        records.append({"path": DEPENDENCY_ARCHIVE.as_posix(), "size": size,
                        "sha256": digest, "component": "python-dependencies"})

        for path in sorted(building.rglob("*")):
            if not path.is_file() or path.name == MANIFEST_NAME:
                continue
            relative = path.relative_to(building)
            if relative.as_posix() in {item["path"] for item in records}:
                continue
            records.append({"path": relative.as_posix(), "size": path.stat().st_size,
                            "sha256": sha256_file(path),
                            "component": "metadata"})
        manifest = {
            "format_version": FORMAT_VERSION,
            "app_version": _version(root),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
            "python_architecture": platform.architecture()[0],
            "requirements_sha256": sha256_file(requirements),
            "files": records,
        }
        (building / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        building.rename(output)
        print(f"[DONE] Offline backup: {output}")
        return manifest
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise


def load_manifest(backup: Path) -> dict[str, Any]:
    backup = backup.resolve()
    path = backup / MANIFEST_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format_version") != FORMAT_VERSION or not isinstance(data.get("files"), list):
        raise ValueError(f"Unsupported or invalid backup manifest: {path}")
    return data


def verify_backup(backup: Path) -> dict[str, Any]:
    backup = backup.resolve()
    manifest = load_manifest(backup)
    errors: list[str] = []
    files = manifest["files"]
    for index, item in enumerate(files, 1):
        relative = _safe_relative(str(item["path"]))
        path = backup / relative
        print(f"  [{index}/{len(files)}] verify {relative}")
        if not path.is_file():
            errors.append(f"missing: {relative}")
            continue
        if path.stat().st_size != int(item["size"]):
            errors.append(f"size mismatch: {relative}")
            continue
        if sha256_file(path) != item["sha256"]:
            errors.append(f"sha256 mismatch: {relative}")
    result = {"ok": not errors, "files": len(files), "errors": errors,
              "app_version": manifest.get("app_version")}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def restore_backup(root: Path, backup: Path,
                   components: set[str] | None = None) -> dict[str, Any]:
    root = root.resolve()
    backup = backup.resolve()
    manifest = load_manifest(backup)
    restored: list[str] = []
    skipped: list[str] = []
    for item in manifest["files"]:
        component = str(item.get("component") or "")
        if components is not None and component not in components:
            continue
        relative = _safe_relative(str(item["path"]))
        if not relative.parts or relative.parts[0] != "payload":
            continue
        payload_rel = Path(*relative.parts[1:])
        source = backup / relative
        target = root / payload_rel
        expected_size = int(item["size"])
        expected_hash = str(item["sha256"])
        if not source.is_file() or source.stat().st_size != expected_size:
            raise FileNotFoundError(f"Backup payload missing or damaged: {relative}")
        if target.is_file() and target.stat().st_size == expected_size \
                and sha256_file(target) == expected_hash:
            skipped.append(str(payload_rel))
            continue
        print(f"  restore {payload_rel} ({expected_size / 2**20:.1f} MiB)")
        digest = _copy_with_hash(source, target)
        if digest != expected_hash:
            target.unlink(missing_ok=True)
            raise ValueError(f"SHA-256 mismatch while restoring {relative}")
        restored.append(str(payload_rel))
    dependency_state = "not-requested"
    if components is None or "python-dependencies" in components:
        dependency_state = _restore_dependencies(root, backup, manifest)
    result = {"ok": True, "restored": restored, "skipped": skipped,
              "dependencies": dependency_state,
              "app_version": manifest.get("app_version")}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _restore_dependencies(root: Path, backup: Path, manifest: dict[str, Any]) -> str:
    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    current_arch = platform.architecture()[0]
    requirements = root / "requirements.txt"
    compatible = (
        current_python == manifest.get("python_major_minor")
        and current_arch == manifest.get("python_architecture")
        and requirements.is_file()
        and sha256_file(requirements) == manifest.get("requirements_sha256")
    )
    dependency_item = next((item for item in manifest["files"]
                            if item.get("component") == "python-dependencies"), None)
    if not compatible or dependency_item is None:
        print("[BACKUP] Python dependency snapshot is not compatible; normal setup will repair it.")
        return "incompatible"

    archive_path = backup / _safe_relative(str(dependency_item["path"]))
    if (not archive_path.is_file()
            or archive_path.stat().st_size != int(dependency_item["size"])
            or sha256_file(archive_path) != dependency_item["sha256"]):
        raise ValueError("Python dependency snapshot is missing or damaged")
    venv = root / ".venv"
    site_packages = venv / "Lib" / "site-packages"
    temporary = venv / ".offline-dependencies-restoring"
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                _safe_relative(member.filename)
            archive.extractall(temporary)
        site_packages.mkdir(parents=True, exist_ok=True)
        shutil.copytree(temporary, site_packages, dirs_exist_ok=True)
        marker = venv / ".requirements.sha256"
        marker.write_text(str(manifest["requirements_sha256"]) + "\n", encoding="ascii")
        (venv / ".deps.ok").unlink(missing_ok=True)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    print("[BACKUP] Restored installed Python dependencies from the local snapshot.")
    return "restored"


def backup_info(backup: Path) -> dict[str, Any]:
    backup = backup.resolve()
    manifest = load_manifest(backup)
    files = manifest["files"]
    return {"path": str(backup), "app_version": manifest.get("app_version"),
            "created": manifest.get("created"), "files": len(files),
            "bytes": sum(int(item.get("size", 0)) for item in files),
            "models": [Path(item["path"]).name for item in files
                       if item.get("component") == "models"
                       and str(item["path"]).lower().endswith(".gguf")],
            "dependencies": any(item.get("component") == "python-dependencies"
                                for item in files),
            "installer": next((Path(item["path"]).name for item in files
                               if item.get("component") == "installer"), None)}


def attach_installer(backup: Path, installer: Path) -> dict[str, Any]:
    backup = backup.resolve()
    installer = installer.resolve()
    manifest = load_manifest(backup)
    if not installer.is_file() or installer.suffix.lower() != ".exe":
        raise FileNotFoundError(f"Setup executable not found: {installer}")
    target = backup / installer.name
    digest = _copy_with_hash(installer, target)
    readme = backup / "README-OFFLINE.txt"
    _write_readme(readme)
    manifest["files"] = [item for item in manifest["files"]
                         if item.get("component") != "installer"
                         and item.get("path") != "README-OFFLINE.txt"]
    manifest["files"].append({"path": installer.name, "size": target.stat().st_size,
                              "sha256": digest, "component": "installer"})
    manifest["files"].append({"path": "README-OFFLINE.txt", "size": readme.stat().st_size,
                              "sha256": sha256_file(readme), "component": "metadata"})
    manifest_path = backup / MANIFEST_NAME
    temporary = manifest_path.with_name(manifest_path.name + ".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, manifest_path)
    print(f"[DONE] Installer added to offline backup: {target}")
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--output", required=True)
    create.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    verify = sub.add_parser("verify")
    verify.add_argument("--backup", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    restore.add_argument("--components", default=None,
                         help="comma-separated components (models,llama,python-dependencies)")
    info = sub.add_parser("info")
    info.add_argument("--backup", required=True)
    attach = sub.add_parser("attach-installer")
    attach.add_argument("--backup", required=True)
    attach.add_argument("--installer", required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            create_backup(Path(args.root), Path(args.output))
        elif args.command == "verify":
            return 0 if verify_backup(Path(args.backup))["ok"] else 1
        elif args.command == "restore":
            selected = ({part.strip() for part in args.components.split(",") if part.strip()}
                        if args.components else None)
            restore_backup(Path(args.root), Path(args.backup), selected)
        elif args.command == "info":
            print(json.dumps(backup_info(Path(args.backup)), ensure_ascii=False, indent=2))
        elif args.command == "attach-installer":
            attach_installer(Path(args.backup), Path(args.installer))
        return 0
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
