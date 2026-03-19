#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Собирает merged_code.zip в корне репозитория.

Приоритетный режим:
- использует `git ls-files --cached --others --exclude-standard`,
  поэтому уважает .gitignore без внешних Python-зависимостей.

Fallback:
- если git недоступен, использует встроенный обход файловой системы
  с базовым списком исключений.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

ZIP_NAME = 'merged_code.zip'
FALLBACK_IGNORES = {
    ZIP_NAME,
    '.git',
    '.idea',
    '.vscode',
    '.venv',
    'venv',
    'env',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    'data',
    'logs',
    'dist',
    'build',
}
FALLBACK_IGNORE_PREFIXES = ('.venv',)
FALLBACK_IGNORE_SUFFIXES = ('.pyc', '.pyo', '.pyd', '.zip')
FALLBACK_ALLOWED_DOTENVS = {'.env.example', '.env.sample', '.env.template'}


def git_tracked_and_unignored(repo_root: Path) -> list[Path] | None:
    if shutil.which('git') is None:
        return None
    try:
        result = subprocess.run(
            ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
    except Exception:
        return None

    raw_items = [item for item in result.stdout.decode('utf-8', errors='ignore').split('\x00') if item]
    paths: list[Path] = []
    for item in raw_items:
        path = repo_root / item
        if path.is_file() and path.name != ZIP_NAME:
            paths.append(path)
    return sorted(paths, key=lambda p: p.relative_to(repo_root).as_posix())


def fallback_collect(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo_root.rglob('*'):
        rel_parts = path.relative_to(repo_root).parts
        if any(part in FALLBACK_IGNORES for part in rel_parts):
            continue
        if any(part.startswith(FALLBACK_IGNORE_PREFIXES) for part in rel_parts):
            continue
        if path.name == '.env' or (path.name.startswith('.env.') and path.name not in FALLBACK_ALLOWED_DOTENVS):
            continue
        if path.name == ZIP_NAME or path.name.endswith(FALLBACK_IGNORE_SUFFIXES):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(repo_root).as_posix())


def collect_files(repo_root: Path) -> list[Path]:
    return git_tracked_and_unignored(repo_root) or fallback_collect(repo_root)


def build_zip(repo_root: Path) -> Path:
    zip_path = repo_root / ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()

    files = collect_files(repo_root)
    with zipfile.ZipFile(zip_path, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in files:
            arcname = file_path.relative_to(repo_root).as_posix()
            archive.write(file_path, arcname)
    return zip_path


def main() -> int:
    repo_root = Path.cwd()
    zip_path = build_zip(repo_root)
    print(f'Готово: {zip_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
