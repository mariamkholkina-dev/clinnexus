# collect_protocol_versions_ru_default.py
from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_INCLUDE_EXTS = {".doc", ".docx", ".pdf"}  # можно расширить при необходимости

# оставляем только "похожие на протокол" файлы:
INCLUDE_HINTS = ("protocol", "протокол")
EXCLUDE_HINTS = (
    "check-list", "checklist", "check list", "protocol check",
    "questions", "вопрос",
    "template", "шаблон",
    "synopsis", "синопсис",
    "сслыки", "ссылки", "link",
)

INVALID_FS_CHARS = r'<>:"/\|?*'

# маркеры языка в имени (ловит ru/rus и en/eng как отдельные токены: _ru, -rus, (eng), .en. и т.п.)
LANG_RU_RE = re.compile(r"(^|[^a-z0-9])(ru|rus)([^a-z0-9]|$)", re.IGNORECASE)
LANG_EN_RE = re.compile(r"(^|[^a-z0-9])(en|eng)([^a-z0-9]|$)", re.IGNORECASE)


def sanitize_folder_name(name: str, max_len: int = 120) -> str:
    name = "".join("_" if c in INVALID_FS_CHARS else c for c in name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:max_len] if len(name) > max_len else name


def extract_study_key(filename: str) -> str:
    """
    Берём всё до первого '__'. Если '__' нет — берём имя файла целиком без расширения.
    """
    if "__" in filename:
        return filename.split("__", 1)[0].strip()
    return Path(filename).stem.strip()


def looks_like_protocol(file_name: str) -> bool:
    s = file_name.lower()
    if not any(h in s for h in INCLUDE_HINTS):
        return False
    if any(h in s for h in EXCLUDE_HINTS):
        return False
    return True


def is_ru_or_default(file_name: str) -> bool:
    """
    Правило:
      - если есть маркер EN/ENG -> НЕ берём
      - если есть маркер RU/RUS -> берём
      - если нет ни RU/RUS ни EN/ENG -> считаем RU и берём
    """
    has_en = LANG_EN_RE.search(file_name) is not None
    if has_en:
        return False
    # либо явно ru/rus, либо вообще нет языковой метки
    return True


@dataclass(frozen=True)
class FileInfo:
    path: Path
    mtime: float


def choose_files(files: list[FileInfo], keep_first_n: int = 3) -> list[FileInfo]:
    """
    files должны быть уже отсортированы по mtime (возрастание).
    Логика отбора:
      - если файлов <= keep_first_n + 1: берём все
      - иначе: берём первые keep_first_n + последний
    """
    if len(files) <= keep_first_n + 1:
        return files

    first = files[:keep_first_n]
    last = files[-1]
    out = first + ([last] if last not in first else [])
    return out


def iter_candidate_files(src: Path, exts: set[str], include_nonprotocol: bool) -> Iterable[Path]:
    for p in src.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue

        # язык: берём RU/RUS и "без метки", но исключаем EN/ENG
        if not is_ru_or_default(p.name):
            continue

        if include_nonprotocol or looks_like_protocol(p.name):
            yield p


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Выбор: последняя версия + до 3 самых ранних версий RU-протокола на исследование. "
                    "Если нет метки rus/eng в имени — считаем файл rus."
    )
    ap.add_argument("--src", required=True, help="Исходная папка с файлами протоколов")
    ap.add_argument("--out", required=True, help="Куда складывать результаты")
    ap.add_argument("--keep-first", type=int, default=3, help="Сколько самых ранних версий сохранять (по умолчанию 3)")
    ap.add_argument(
        "--include-nonprotocol",
        action="store_true",
        help="Не фильтровать по словам 'protocol/протокол' (возьмёт все RU/без-метки документы подходящих расширений)",
    )
    ap.add_argument(
        "--ext",
        action="append",
        default=None,
        help="Расширения файлов (можно несколько раз). Пример: --ext .docx --ext .pdf",
    )
    ap.add_argument("--dry-run", action="store_true", help="Только показать, что будет скопировано")
    args = ap.parse_args()

    src = Path(args.src)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in args.ext} if args.ext else DEFAULT_INCLUDE_EXTS

    groups: dict[str, list[FileInfo]] = {}

    for f in iter_candidate_files(src, exts, args.include_nonprotocol):
        key = extract_study_key(f.name)
        info = FileInfo(path=f, mtime=f.stat().st_mtime)
        groups.setdefault(key, []).append(info)

    if not groups:
        print(
            "Не найдено подходящих файлов (RU/RUS или без метки языка). "
            "Проверьте --src, --ext и что в именах EN/ENG помечены корректно."
        )
        return 2

    for key, items in sorted(groups.items(), key=lambda x: x[0].lower()):
        items_sorted = sorted(items, key=lambda x: x.mtime)  # от самых ранних к самым поздним
        chosen = choose_files(items_sorted, keep_first_n=max(0, args.keep_first))

        folder = sanitize_folder_name(key) or "UNNAMED"
        dst_dir = out_root / folder
        dst_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n[{folder}] всего файлов (RU/без-метки): {len(items_sorted)} -> выбрано: {len(chosen)}")
        for fi in chosen:
            dst = dst_dir / fi.path.name
            print(f"  {'DRY ' if args.dry_run else ''}COPY: {fi.path.name} -> {dst}")
            if not args.dry_run:
                shutil.copy2(fi.path, dst)

    print("\nГотово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
