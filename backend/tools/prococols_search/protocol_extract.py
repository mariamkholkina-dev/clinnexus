import argparse
import logging
import shutil
from pathlib import Path
from datetime import datetime


INCLUDE_TOKENS = ["protocol", "протокол"]
EXCLUDE_TOKENS = ["ведомость", "проверочн", "лист", "clarification", "letter"]
ALLOWED_EXTS = {".doc", ".docx"}


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("protocols_collector")
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


def contains_any(text: str, tokens: list[str]) -> bool:
    t = text.lower()
    return any(tok in t for tok in tokens)


def contains_excluded(text: str) -> str | None:
    t = text.lower()
    for tok in EXCLUDE_TOKENS:
        if tok in t:
            return tok
    return None


def choose_search_root(study_root: Path, logger: logging.Logger) -> tuple[Path, bool]:
    """
    Returns (search_root, used_protocol_folder).
    If any directory anywhere inside study_root contains protocol tokens in its name,
    choose the first (closest to root, then alphabetical).
    Otherwise return study_root.
    """
    candidates = []
    for p in study_root.rglob("*"):
        if p.is_dir() and contains_any(p.name, INCLUDE_TOKENS):
            # prioritize "closer" folders
            depth = len(p.relative_to(study_root).parts)
            candidates.append((depth, str(p).lower(), p))
    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        chosen = candidates[0][2]
        logger.info(f"  Using protocol-like folder as search root: {chosen}")
        return chosen, True
    else:
        logger.info("  No protocol-like folder found; searching entire study folder")
        return study_root, False


def is_target_file(path: Path) -> tuple[bool, str]:
    """
    Returns (ok, reason). If not ok, reason explains why.
    """
    if not path.is_file():
        return False, "not a file"
    if path.suffix.lower() not in ALLOWED_EXTS:
        return False, "wrong extension"
    name = path.name
    if not contains_any(name, INCLUDE_TOKENS):
        return False, "missing include token"
    bad = contains_excluded(name)
    if bad:
        return False, f"excluded by token '{bad}'"
    return True, "ok"


def safe_copy(src: Path, dst_dir: Path, prefix: str, *, overwrite: bool, logger: logging.Logger) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)

    # sanitize prefix for Windows filenames
    safe_prefix = "".join(c if c not in r'<>:"/\|?*' else "_" for c in prefix).strip()
    target_name = f"{safe_prefix}__{src.name}"
    dst = dst_dir / target_name

    if dst.exists() and not overwrite:
        # make unique name
        stem = dst.stem
        suffix = dst.suffix
        i = 2
        while True:
            alt = dst_dir / f"{stem}__{i}{suffix}"
            if not alt.exists():
                dst = alt
                break
            i += 1

    shutil.copy2(src, dst)
    logger.info(f"    COPIED: {src} -> {dst}")
    return dst


def main():
    parser = argparse.ArgumentParser(description="Collect protocol DOC/DOCX files into C:/protocols/all")
    parser.add_argument("--root", default=r"C:\protocols", help=r"Каталог Protocols (в котором лежат папки-исследования).")
    parser.add_argument("--out", default=r"C:\protocols_all", help=r"Куда складывать найденные файлы.")
    parser.add_argument("--overwrite", action="store_true", help="Перезаписывать (иначе делать уникальные имена).")
    parser.add_argument("--log", default=None, help="Путь к лог-файлу (если не задан — logs/ рядом со скриптом).")

    args = parser.parse_args()

    root = Path(args.root)
    out_dir = Path(args.out)

    if args.log:
        log_path = Path(args.log)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = Path(__file__).resolve().parent / "logs" / f"collect_protocols_{ts}.log"

    logger = setup_logger(log_path)

    logger.info(f"ROOT: {root}")
    logger.info(f"OUT:  {out_dir}")
    logger.info(f"OVERWRITE: {args.overwrite}")
    logger.info(f"LOGFILE: {log_path}")

    if not root.is_dir():
        logger.error(f"Root does not exist or not a directory: {root}")
        raise SystemExit(2)

    out_dir.mkdir(parents=True, exist_ok=True)

    studies = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.lower())

    total_studies = 0
    studies_with_protocol_folder = 0
    total_found_files = 0
    total_copied = 0
    total_skipped = 0

    for study in studies:
        total_studies += 1
        study_name = study.name
        logger.info(f"[STUDY] {study_name}")

        search_root, used_protocol_folder = choose_search_root(study, logger)
        if used_protocol_folder:
            studies_with_protocol_folder += 1

        found_here = 0
        copied_here = 0

        for file_path in search_root.rglob("*"):
            ok, reason = is_target_file(file_path)
            if not ok:
                continue

            found_here += 1
            total_found_files += 1
            try:
                safe_copy(file_path, out_dir, prefix=study_name, overwrite=args.overwrite, logger=logger)
                copied_here += 1
                total_copied += 1
            except Exception as e:
                logger.error(f"    FAILED to copy {file_path}: {e}")
                total_skipped += 1

        logger.info(f"  Found in this study: {found_here}, copied: {copied_here}")

    logger.info("==== SUMMARY ====")
    logger.info(f"Studies scanned: {total_studies}")
    logger.info(f"Studies where protocol-like folder used: {studies_with_protocol_folder}")
    logger.info(f"Matched files found: {total_found_files}")
    logger.info(f"Copied: {total_copied}")
    logger.info(f"Errors/Skipped: {total_skipped}")
    logger.info("Done.")


if __name__ == "__main__":
    main()
