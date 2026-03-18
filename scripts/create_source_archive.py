from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "dist"

EXCLUDED_DIR_NAMES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "env",
    "ENV",
    "htmlcov",
    "logs",
    "node_modules",
    "temp",
    "tmp",
    "venv",
}

EXCLUDED_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    ".env",
    "credentials.json",
    "id_ed25519",
    "id_ed25519.pub",
    "id_rsa",
    "id_rsa.pub",
    "secrets.json",
    "token.json",
}

EXCLUDED_SUFFIXES = {
    ".7z",
    ".crt",
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pid",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".zip",
}

SAFE_ENV_FILES = {".env.example"}


def should_skip(path: Path) -> bool:
    if path.is_dir():
        return path.name in EXCLUDED_DIR_NAMES

    if path.name in SAFE_ENV_FILES:
        return False

    if path.name in EXCLUDED_FILE_NAMES:
        return True

    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True

    if path.name.startswith(".env"):
        return True

    return False


def iter_source_files(root_dir: Path) -> list[Path]:
    files: list[Path] = []

    for path in root_dir.rglob("*"):
        relative_parts = path.relative_to(root_dir).parts
        if any(part in EXCLUDED_DIR_NAMES for part in relative_parts):
            continue
        if path.is_file() and not should_skip(path):
            files.append(path)

    files.sort(key=lambda item: item.relative_to(root_dir).as_posix())
    return files


def build_archive_name() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ROOT_DIR.name}_source_{timestamp}.zip"


def create_archive() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = OUTPUT_DIR / build_archive_name()
    source_files = iter_source_files(ROOT_DIR)

    with ZipFile(archive_path, mode="w", compression=ZIP_DEFLATED) as archive:
        for file_path in source_files:
            archive.write(file_path, arcname=file_path.relative_to(ROOT_DIR))

    return archive_path


def main() -> None:
    archive_path = create_archive()
    print(f"Archive created: {archive_path}")


if __name__ == "__main__":
    main()
