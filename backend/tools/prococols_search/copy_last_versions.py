import argparse
import csv
import logging
import shutil
from pathlib import Path
from datetime import datetime


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("copy_protocol_last_version_from_csv")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)

    if not logger.handlers:
        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger


def unique_target_path(dst_dir: Path, filename: str) -> Path:
    """Если файл уже существует — добавляет суффикс __2, __3 ..."""
    dst = dst_dir / filename
    if not dst.exists():
        return dst

    stem = dst.stem
    suffix = dst.suffix
    i = 2
    while True:
        cand = dst_dir / f"{stem}__{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def read_filenames_from_csv(csv_path: Path, filename_col: str, logger: logging.Logger) -> list[str]:
    """
    Читает CSV и возвращает список имён файлов из колонки filename_col.
    Берёт только имя файла (без путей), убирает дубли (case-insensitive), сохраняет порядок.
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    # Попробуем UTF-8, если не получится — UTF-8-SIG (часто у CSV из Excel)
    text = None
    for enc in ("utf-8", "utf-8-sig"):
        try:
            text = csv_path.read_text(encoding=enc, errors="strict")
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = csv_path.read_text(encoding="utf-8", errors="replace")

    rows = csv.DictReader(text.splitlines())
    if rows.fieldnames is None:
        raise ValueError("CSV has no header row")

    if filename_col not in rows.fieldnames:
        raise ValueError(f"CSV missing required column '{filename_col}'. Columns: {rows.fieldnames}")

    out = []
    seen = set()

    for r in rows:
        raw = (r.get(filename_col) or "").strip()
        if not raw:
            continue

        fn = Path(raw).name  # на всякий случай, если в CSV вдруг попались пути
        key = fn.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(fn)

    logger.info(f"Unique filenames read from CSV: {len(out)}")
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Copy protocol last-version files listed in latest_protocols_by_study_language.csv into C:\\protocol_last_version"
    )
    parser.add_argument("--csv", required=True, help="Путь к latest_protocols_by_study_language.csv")
    parser.add_argument("--src", default=".", help="Откуда копировать (по умолчанию текущая папка '.')")
    parser.add_argument("--dst", default=r"C:\protocol_last_version", help=r"Куда копировать (по умолчанию C:\protocol_last_version)")
    parser.add_argument("--col", default="filename", help="Имя колонки с именем файла (по умолчанию filename)")
    parser.add_argument("--overwrite", action="store_true", help="Перезаписывать существующие файлы (иначе делать уникальные имена)")
    parser.add_argument("--dry-run", action="store_true", help="Только показать действия без копирования")
    parser.add_argument("--log", default=None, help="Путь к лог-файлу (если не задан — logs/ рядом со скриптом)")

    args = parser.parse_args()

    csv_path = Path(args.csv)
    src_dir = Path(args.src).resolve()
    dst_dir = Path(args.dst)

    if args.log:
        log_path = Path(args.log)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = Path(__file__).resolve().parent / "logs" / f"copy_protocol_last_version_{ts}.log"

    logger = setup_logger(log_path)

    logger.info(f"CSV: {csv_path}")
    logger.info(f"SRC: {src_dir}")
    logger.info(f"DST: {dst_dir}")
    logger.info(f"COL: {args.col}")
    logger.info(f"OVERWRITE: {args.overwrite}")
    logger.info(f"DRY_RUN: {args.dry_run}")
    logger.info(f"LOGFILE: {log_path}")

    if not src_dir.is_dir():
        logger.error(f"Source directory not found: {src_dir}")
        raise SystemExit(2)

    dst_dir.mkdir(parents=True, exist_ok=True)

    wanted = read_filenames_from_csv(csv_path, args.col, logger)

    copied = 0
    missing = 0
    errors = 0

    for fn in wanted:
        src = src_dir / fn
        if not src.exists():
            missing += 1
            logger.warning(f"MISSING: {src}")
            continue

        if args.overwrite:
            dst = dst_dir / fn
        else:
            dst = unique_target_path(dst_dir, fn)

        if args.dry_run:
            logger.info(f"DRY_RUN COPY: {src} -> {dst}")
            continue

        try:
            shutil.copy2(src, dst)
            copied += 1
            logger.info(f"COPIED: {src} -> {dst}")
        except Exception as e:
            errors += 1
            logger.error(f"FAILED: {src} -> {dst}: {e}")

    logger.info("==== SUMMARY ====")
    logger.info(f"Listed (unique): {len(wanted)}")
    logger.info(f"Copied: {copied}")
    logger.info(f"Missing: {missing}")
    logger.info(f"Errors: {errors}")
    logger.info("Done.")


if __name__ == "__main__":
    main()
