#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

OBSOLETE_PATHS = [
    'bot',
    'webapp',
    'webapp_v2',
    'tests_v2',
    'main_prod.py',
    'README_V2.md',
    'requirements_v2.txt',
    'config.py',
    'profiles.json',
    'profiles.example.json',
    'scripts/create_source_archive.py',
]

OBSOLETE_TEST_FILES = [
    'tests/test_access.py',
    'tests/test_profiles.py',
    'tests/test_source_archive.py',
    'tests/test_storage.py',
    'tests/test_web_app.py',
]


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description='Удаляет legacy-пути после закрытия миграции.')
    parser.add_argument('--apply', action='store_true', help='выполнить удаление; без флага работает как dry-run')
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    targets = [repo_root / item for item in OBSOLETE_PATHS + OBSOLETE_TEST_FILES]
    existing = [item for item in targets if item.exists()]

    if not existing:
        print('Legacy-пути не найдены. Cleanup не требуется.')
        return 0

    print('Будут удалены:' if args.apply else 'Найдены legacy-пути:')
    for item in existing:
        print(f'- {item.relative_to(repo_root)}')

    if not args.apply:
        print('\nDry-run завершён. Добавь --apply, чтобы выполнить удаление.')
        return 0

    for item in existing:
        remove_path(item)
    print('\nCleanup завершён.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
