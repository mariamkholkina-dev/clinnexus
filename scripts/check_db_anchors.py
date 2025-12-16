from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

# Позволяет запускать скрипт из корня репо: `python .\\scripts\\check_db_anchors.py`
sys.path.insert(0, "backend")
from app.core.config import settings  # noqa: E402


def main() -> None:
    url = settings.sync_database_url
    print(f"DB: {url}")
    engine = create_engine(url)
    with engine.connect() as conn:
        exists = conn.execute(text("select to_regclass('public.anchors')")).scalar()
        print(f"public.anchors: {exists}")


if __name__ == "__main__":
    main()


