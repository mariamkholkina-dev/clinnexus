"""
Скрипт для разделения clusters.json на два файла по языковому признаку.

Разделяет кластеры на:
- clusters_ru.json: кластеры с русскими заголовками (top_titles_ru не пустой)
- clusters_en.json: кластеры с английскими заголовками (top_titles_en не пустой)

Использование:
    python -m tools.passport_tuning.split_clusters_by_language
    python -m tools.passport_tuning.split_clusters_by_language --input clusters.json --output-dir .
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def split_clusters_by_language(
    input_file: Path,
    output_dir: Path,
    strict: bool = False,
) -> dict[str, int]:
    """
    Разделяет clusters.json на два файла по языковому признаку.

    Args:
        input_file: Путь к входному файлу clusters.json
        output_dir: Директория для сохранения выходных файлов
        strict: Если True, кластер попадает только в один файл (RU или EN).
                Если False, кластер может попасть в оба, если есть заголовки на обоих языках.

    Returns:
        Словарь со статистикой: {
            "total": общее количество кластеров,
            "ru_only": только русские,
            "en_only": только английские,
            "both": оба языка,
            "neither": ни один язык
        }
    """
    print(f"Чтение {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        clusters = json.load(f)

    stats = {
        "total": len(clusters),
        "ru_only": 0,
        "en_only": 0,
        "both": 0,
        "neither": 0,
    }

    clusters_ru = []
    clusters_en = []

    for cluster in clusters:
        top_titles_ru = cluster.get("top_titles_ru", [])
        top_titles_en = cluster.get("top_titles_en", [])

        has_ru = bool(top_titles_ru)
        has_en = bool(top_titles_en)

        if strict:
            # Строгий режим: кластер попадает только в один файл
            if has_ru and not has_en:
                clusters_ru.append(cluster)
                stats["ru_only"] += 1
            elif has_en and not has_ru:
                clusters_en.append(cluster)
                stats["en_only"] += 1
            elif has_ru and has_en:
                # Если есть оба языка, выбираем по преобладающему
                if len(top_titles_ru) >= len(top_titles_en):
                    clusters_ru.append(cluster)
                    stats["ru_only"] += 1
                else:
                    clusters_en.append(cluster)
                    stats["en_only"] += 1
            else:
                stats["neither"] += 1
        else:
            # Нестрогий режим: кластер может попасть в оба файла
            if has_ru:
                clusters_ru.append(cluster)
            if has_en:
                clusters_en.append(cluster)

            if has_ru and not has_en:
                stats["ru_only"] += 1
            elif has_en and not has_ru:
                stats["en_only"] += 1
            elif has_ru and has_en:
                stats["both"] += 1
            else:
                stats["neither"] += 1

    # Сохраняем файлы
    output_dir.mkdir(parents=True, exist_ok=True)

    output_ru = output_dir / "clusters_ru.json"
    output_en = output_dir / "clusters_en.json"

    print(f"Сохранение {output_ru} ({len(clusters_ru)} кластеров)...")
    with open(output_ru, "w", encoding="utf-8") as f:
        json.dump(clusters_ru, f, ensure_ascii=False, indent=2)

    print(f"Сохранение {output_en} ({len(clusters_en)} кластеров)...")
    with open(output_en, "w", encoding="utf-8") as f:
        json.dump(clusters_en, f, ensure_ascii=False, indent=2)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Разделение clusters.json на два файла по языковому признаку"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("clusters.json"),
        help="Путь к входному файлу clusters.json (по умолчанию: clusters.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Директория для сохранения выходных файлов (по умолчанию: текущая)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Строгий режим: кластер попадает только в один файл (RU или EN)",
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Ошибка: файл {args.input} не найден", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("Разделение clusters.json по языковому признаку")
    print("=" * 60)
    print(f"Входной файл: {args.input}")
    print(f"Выходная директория: {args.output_dir}")
    print(f"Режим: {'строгий' if args.strict else 'нестрогий'}")
    print()

    try:
        stats = split_clusters_by_language(
            input_file=args.input,
            output_dir=args.output_dir,
            strict=args.strict,
        )

        print()
        print("=" * 60)
        print("Статистика:")
        print(f"  Всего кластеров: {stats['total']}")
        print(f"  Только RU: {stats['ru_only']}")
        print(f"  Только EN: {stats['en_only']}")
        if not args.strict:
            print(f"  Оба языка: {stats['both']}")
        print(f"  Без языка: {stats['neither']}")
        print("=" * 60)
        print()
        print("Файлы сохранены:")
        print(f"  - {args.output_dir / 'clusters_ru.json'}")
        print(f"  - {args.output_dir / 'clusters_en.json'}")

    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

