import argparse
import logging
import shutil
from pathlib import Path
from datetime import datetime


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("nextcloud_study_docs_copy")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # console
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)

    # avoid duplicate handlers if re-run in same interpreter
    if not logger.handlers:
        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger


def find_matching_subdirs(parent: Path, needle: str) -> list[Path]:
    needle_l = needle.lower()
    matches = []
    try:
        for p in parent.iterdir():
            if p.is_dir() and needle_l in p.name.lower():
                matches.append(p)
    except Exception:
        # will be logged by caller
        pass
    return matches


def copy_tree_files_only(src_dir: Path, dst_dir: Path, *, overwrite: bool, logger: logging.Logger) -> tuple[int, int]:
    """
    Copies files (recursively) from src_dir into dst_dir, preserving relative paths.
    Returns (files_copied, files_skipped).
    """
    copied = 0
    skipped = 0

    for item in src_dir.rglob("*"):
        if not item.is_file():
            continue

        rel = item.relative_to(src_dir)
        target = dst_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and not overwrite:
            skipped += 1
            logger.info(f"SKIP (exists): {target}")
            continue

        try:
            shutil.copy2(item, target)
            copied += 1
            logger.info(f"COPY: {item} -> {target}")
        except Exception as e:
            logger.error(f"FAILED to copy {item} -> {target}: {e}")

    return copied, skipped


def main():
    parser = argparse.ArgumentParser(
        description="Copy files from '*Study documents*' under Nextcloud структуру в C:/protocols/<level2>/"
    )
    parser.add_argument(
        "--root",
        default=r"d:\Nextcloud ZZZ copy",
        help=r"Корневая директория d:Nextcloud ZZZ copy\ (абсолютный или относительный путь).",
    )
    parser.add_argument(
        "--dest",
        default=r"C:\protocols",
        help=r"Куда складывать результаты (по умолчанию C:\protocols).",
    )
    parser.add_argument(
        "--match",
        default="Study documents",
        help='Подстрока для поиска папки (по умолчанию "Study documents").',
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Перезаписывать существующие файлы (по умолчанию пропускать).",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Путь к лог-файлу. Если не задан, создаст рядом со скриптом logs/<timestamp>.log",
    )

    args = parser.parse_args()

    root = Path(args.root)
    dest_root = Path(args.dest)
    dest_root.mkdir(parents=True, exist_ok=True)

    if args.log:
        log_path = Path(args.log)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = Path(__file__).resolve().parent / "logs" / f"copy_study_documents_{ts}.log"

    logger = setup_logger(log_path)

    logger.info(f"ROOT: {root}")
    logger.info(f"DEST: {dest_root}")
    logger.info(f"MATCH: {args.match!r}")
    logger.info(f"OVERWRITE: {args.overwrite}")
    logger.info(f"LOGFILE: {log_path}")

    if not root.exists() or not root.is_dir():
        logger.error(f"Root directory does not exist or not a directory: {root}")
        raise SystemExit(2)

    level1_count = 0
    level2_count = 0
    working_space_missing = 0
    files_missing = 0

    found_folders = 0
    total_files_copied = 0
    total_files_skipped = 0

    for level1 in sorted([p for p in root.iterdir() if p.is_dir()]):
        level1_count += 1
        logger.info(f"[L1] {level1}")

        files_dir = level1 / "files"
        if not files_dir.is_dir():
            files_missing += 1
            logger.info(f"  - No 'files' folder: {files_dir}")
            continue

        for level2 in sorted([p for p in files_dir.iterdir() if p.is_dir()]):
            level2_count += 1
            logger.info(f"  [L2] {level2.name}")

            ws = level2 / "Working space"
            if not ws.is_dir():
                working_space_missing += 1
                logger.info(f"    - No 'Working space' folder: {ws}")
                continue

            try:
                matches = find_matching_subdirs(ws, args.match)
            except Exception as e:
                logger.error(f"    - Failed to scan Working space {ws}: {e}")
                continue

            if not matches:
                logger.info(f"    - No folder containing {args.match!r} in name under: {ws}")
                continue

            # Create destination folder named as level2 directory name
            dst_dir = dest_root / level2.name
            dst_dir.mkdir(parents=True, exist_ok=True)

            for src in matches:
                found_folders += 1
                logger.info(f"    FOUND: {src} -> DEST: {dst_dir}")

                copied, skipped = copy_tree_files_only(
                    src, dst_dir, overwrite=args.overwrite, logger=logger
                )
                total_files_copied += copied
                total_files_skipped += skipped

    logger.info("==== SUMMARY ====")
    logger.info(f"Level1 scanned: {level1_count}")
    logger.info(f"Level2 scanned: {level2_count}")
    logger.info(f"Missing 'files' folders: {files_missing}")
    logger.info(f"Missing 'Working space' folders: {working_space_missing}")
    logger.info(f"Found matching folders: {found_folders}")
    logger.info(f"Files copied: {total_files_copied}")
    logger.info(f"Files skipped (exists): {total_files_skipped}")
    logger.info("Done.")


if __name__ == "__main__":
    main()
