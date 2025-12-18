#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    print("Missing dependency: requests. Install: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print("Warning: python-dotenv not installed. .env file will not be loaded.")
    print("Install: pip install python-dotenv")
    load_dotenv = None


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        die(f"ASSERT FAILED: {msg}")


def http_json(method: str, url: str, *, json_body: Any = None, timeout: int = 30) -> Any:
    headers = {"Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    r = requests.request(method, url, headers=headers, json=json_body, timeout=timeout)
    if r.status_code >= 400:
        # show response body for debugging
        try:
            body = r.json()
        except Exception:
            body = r.text
        die(f"{method} {url} -> {r.status_code}\n{body}")
    if r.text.strip() == "":
        return None
    return r.json()


def upload_file(api_base: str, version_id: str, docx_path: str) -> Dict[str, Any]:
    url = f"{api_base}/api/document-versions/{version_id}/upload"
    with open(docx_path, "rb") as f:
        files = {"file": (os.path.basename(docx_path), f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        r = requests.post(url, files=files, headers={"Accept": "application/json"}, timeout=60)
    if r.status_code >= 400:
        die(f"Upload failed {r.status_code}: {r.text}")
    return r.json()


def poll_ingest(api_base: str, version_id: str, timeout_sec: int = 180) -> Dict[str, Any]:
    deadline = time.time() + timeout_sec
    url = f"{api_base}/api/document-versions/{version_id}"
    while True:
        v = http_json("GET", url, timeout=30)
        status = v.get("ingestion_status")
        print(f"  ingestion_status={status}")
        if status in ("ready", "needs_review", "failed"):
            if status == "failed":
                die(f"Ingestion failed:\n{json.dumps(v, indent=2, ensure_ascii=False)}")
            return v
        if time.time() > deadline:
            die("Timeout waiting for ingestion to finish")
        time.sleep(1)


def create_study_document_version(api_base: str, workspace_id: str) -> Tuple[str, str, str]:
    # 1) study
    study_body = {
        "workspace_id": workspace_id,
        "study_code": f"STEP6_5-{int(time.time())}",
        "title": "Step 6.5 LLM Assist Smoke",
        "status": "active",
    }
    study = http_json("POST", f"{api_base}/api/studies", json_body=study_body, timeout=30)
    study_id = study["id"]
    print(f"study_id={study_id}")

    # 2) document
    doc_body = {"doc_type": "protocol", "title": "Protocol (LLM Assist Smoke)", "lifecycle_status": "draft"}
    doc = http_json("POST", f"{api_base}/api/studies/{study_id}/documents", json_body=doc_body, timeout=30)
    document_id = doc["id"]
    print(f"document_id={document_id}")

    # 3) version
    ver_body = {"version_label": "v1.0"}
    ver = http_json("POST", f"{api_base}/api/documents/{document_id}/versions", json_body=ver_body, timeout=30)
    version_id = ver["id"]
    print(f"version_id={version_id}")

    return study_id, document_id, version_id


def get_section_maps(api_base: str, version_id: str) -> List[Dict[str, Any]]:
    return http_json("GET", f"{api_base}/api/document-versions/{version_id}/section-maps", timeout=30)


def ensure_section_contracts(api_base: str, workspace_id: str, section_keys: List[str]) -> None:
    """Создаёт недостающие section contracts для указанных section_keys."""
    # Получаем существующие контракты
    existing = http_json("GET", f"{api_base}/api/section-contracts?doc_type=protocol&is_active=true", timeout=30)
    existing_keys = {c["section_key"] for c in existing}
    
    missing = [sk for sk in section_keys if sk not in existing_keys]
    if not missing:
        return
    
    print(f"Creating missing section contracts: {missing}")
    
    # Базовые контракты для создания
    contracts_data = {
        "protocol.objectives": {
            "title": "Objectives",
            "required_facts_json": {"primary_objective": {"type": "string"}},
            "allowed_sources_json": {"doc_types": ["protocol"], "section_keys": ["protocol.objectives"]},
            "retrieval_recipe_json": {
                "version": 1,
                "heading_match": {"must": ["objective", "objectives"], "should": [], "not": []},
                "regex": {"heading": ["^(\\d+\\.)?\\s*(Objectives|Study Objectives)\\b"]},
                "capture": {"strategy": "heading_block", "min_anchors": 2},
            },
            "qc_ruleset_json": {"rules": []},
            "citation_policy": "per_sentence",
        },
        "protocol.soa": {
            "title": "Schedule of Activities",
            "required_facts_json": {},
            "allowed_sources_json": {"doc_types": ["protocol"], "section_keys": ["protocol.soa"]},
            "retrieval_recipe_json": {
                "version": 1,
                "heading_match": {"must": ["schedule", "activities", "soa"], "should": [], "not": []},
                "regex": {"heading": ["^(\\d+\\.)?\\s*(Schedule of Activities|SoA)\\b"]},
                "capture": {"strategy": "heading_block", "min_anchors": 2},
            },
            "qc_ruleset_json": {"rules": []},
            "citation_policy": "per_claim",
        },
        "protocol.eligibility.inclusion": {
            "title": "Inclusion Criteria",
            "required_facts_json": {},
            "allowed_sources_json": {"doc_types": ["protocol"], "section_keys": ["protocol.eligibility.inclusion"]},
            "retrieval_recipe_json": {
                "version": 1,
                "heading_match": {"must": ["inclusion", "inclusion criteria"], "should": [], "not": ["exclusion"]},
                "regex": {"heading": ["^(\\d+\\.)?\\s*(Inclusion Criteria|Inclusion)\\b"]},
                "capture": {"strategy": "heading_block", "min_anchors": 2},
            },
            "qc_ruleset_json": {"rules": []},
            "citation_policy": "per_sentence",
        },
    }
    
    for section_key in missing:
        if section_key not in contracts_data:
            print(f"Warning: No template for {section_key}, skipping")
            continue
        
        data = contracts_data[section_key]
        body = {
            "workspace_id": workspace_id,
            "doc_type": "protocol",
            "section_key": section_key,
            **data,
        }
        try:
            http_json("POST", f"{api_base}/api/section-contracts", json_body=body, timeout=30)
            print(f"  Created: {section_key}")
        except SystemExit as e:
            print(f"  Failed to create {section_key}: {e}")
            # Продолжаем, даже если не удалось создать


def assist_mapping(
    api_base: str,
    version_id: str,
    section_keys: List[str],
    apply: bool,
    max_candidates: int,
    allow_visual: bool,
) -> Dict[str, Any]:
    # Endpoint name per design prompt:
    # POST /api/document-versions/{version_id}/section-maps/assist?apply=true|false
    url = f"{api_base}/api/document-versions/{version_id}/section-maps/assist"

    body = {
        "doc_type": "protocol",
        "section_keys": section_keys,
        "max_candidates_per_section": max_candidates,
        "allow_visual_headings": allow_visual,
        "apply": apply,
    }
    return http_json("POST", url, json_body=body, timeout=60)


def find_doc_files(script_dir: Path) -> List[Path]:
    """Находит все .doc и .docx файлы в каталоге скрипта и всех поддиректориях."""
    doc_files = []
    for ext in [".doc", ".docx"]:
        # Рекурсивный поиск: **/*.docx находит файлы во всех поддиректориях
        doc_files.extend(script_dir.glob(f"**/*{ext}"))
    # Исключаем сам скрипт, если он случайно имеет расширение .docx
    script_name = Path(__file__).name
    doc_files = [f for f in doc_files if f.name != script_name]
    # Сортируем по полному пути для предсказуемого порядка
    return sorted(doc_files)


def process_single_file(
    api_base: str,
    workspace_id: str,
    docx_path: Path,
    section_keys: List[str],
    apply: bool,
    max_candidates: int,
    allow_visual: bool,
    timeout: int,
    skip_llm: bool,
) -> Tuple[str, bool]:
    """Обрабатывает один DOC/DOCX файл. Возвращает (version_id, success)."""
    print(f"\n{'='*80}")
    print(f"Обработка файла: {docx_path.name}")
    print(f"{'='*80}")
    
    try:
        # Создаём study/document/version
        print("Creating study/document/version...")
        _, _, version_id = create_study_document_version(api_base, workspace_id)

        # Загружаем файл
        print("Uploading DOCX...")
        up = upload_file(api_base, version_id, str(docx_path))
        print(f"  upload sha256={up.get('sha256')} uri={up.get('uri')}")

        # Запускаем ингестию
        print("Starting ingestion...")
        try:
            http_json("POST", f"{api_base}/api/document-versions/{version_id}/ingest?force=true", json_body={}, timeout=120)
        except SystemExit:
            # some servers require empty body or ignore it; retry without json
            r = requests.post(f"{api_base}/api/document-versions/{version_id}/ingest?force=true", timeout=120)
            if r.status_code >= 400:
                print(f"ERROR: ingest failed {r.status_code}: {r.text}")
                return version_id, False

        # Ожидаем завершения ингестии
        print("Polling ingestion...")
        v = poll_ingest(api_base, version_id, timeout_sec=timeout)
        print(f"Final ingestion_status={v.get('ingestion_status')}")

        # Пропускаем LLM assist, если указан флаг
        if skip_llm:
            print("\n[SKIP] LLM assist пропущен (--skip-llm)")
            print(f"Файл загружен и обработан. version_id={version_id}")
            return version_id, True

        # Убеждаемся, что нужные section contracts существуют
        print("\nEnsuring section contracts exist...")
        ensure_section_contracts(api_base, workspace_id, section_keys)

        # Получаем section_maps до assist
        print("\nFetching section_maps BEFORE assist...")
        before_maps = get_section_maps(api_base, version_id)
        before_by_key = {m["section_key"]: m for m in before_maps}
        print(f"  section_maps count={len(before_maps)}")

        # Вызываем LLM assist
        print("\nCalling LLM assist endpoint...")
        resp = assist_mapping(
            api_base=api_base,
            version_id=version_id,
            section_keys=section_keys,
            apply=apply,
            max_candidates=max_candidates,
            allow_visual=allow_visual,
        )

        # Базовые проверки
        assert_true(resp.get("version_id") == version_id, "Response version_id mismatch")
        assert_true(resp.get("secure_mode") is True, "Expected secure_mode=true in response (Step 6.5 should be gated)")
        assert_true(resp.get("llm_used") is True, "Expected llm_used=true in response")

        candidates = resp.get("candidates") or {}
        qc = resp.get("qc") or {}

        print("\nAssist candidates summary:")
        for sk in section_keys:
            c = candidates.get(sk, [])
            print(f"  {sk}: {len(c)} candidates")
            if c:
                top = c[0]
                print(f"    top.heading_anchor_id={top.get('heading_anchor_id')} conf={top.get('confidence')}")

        print("\nQC summary:")
        for sk in section_keys:
            q = qc.get(sk, {})
            print(f"  {sk}: status={q.get('status')} selected={q.get('selected_heading_anchor_id')}")
            errs = q.get("errors") or []
            if errs:
                print(f"    errors: {errs[:2]}{' ...' if len(errs)>2 else ''}")

        # Проверка apply
        if apply:
            print("\nFetching section_maps AFTER assist (apply=true)...")
            after_maps = get_section_maps(api_base, version_id)
            after_by_key = {m["section_key"]: m for m in after_maps}

            changed = 0
            for sk in section_keys:
                b = before_by_key.get(sk)
                a = after_by_key.get(sk)
                if not a:
                    continue
                if not b:
                    changed += 1
                    continue
                if (b.get("status"), b.get("confidence"), b.get("anchor_ids")) != (a.get("status"), a.get("confidence"), a.get("anchor_ids")):
                    changed += 1

            print(f"  changed mappings (heuristic) = {changed}")
            any_mapped = any((qc.get(sk, {}).get("status") == "mapped") for sk in section_keys)
            if any_mapped:
                assert_true(changed > 0, "QC had mapped sections, but section_maps did not change. apply may not be working.")
        else:
            print("\napply=false: not verifying DB updates (expected).")

        print(f"\n[OK] Файл {docx_path.name} обработан успешно")
        print(f"version_id={version_id}")
        return version_id, True
        
    except SystemExit as e:
        print(f"\n[ERROR] Ошибка при обработке файла {docx_path.name}: {e}")
        return "", False
    except Exception as e:
        print(f"\n[ERROR] Неожиданная ошибка при обработке файла {docx_path.name}: {e}")
        import traceback
        traceback.print_exc()
        return "", False


def main() -> None:
    # Windows консоль часто использует cp1251, из-за чего печать некоторых символов (например, ✅)
    # падает с UnicodeEncodeError. Делаем вывод безопасным: переходим на UTF-8, если возможно.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # Загружаем переменные окружения из backend/.env
    if load_dotenv:
        # Определяем путь к backend/.env относительно расположения скрипта
        script_dir = Path(__file__).parent.absolute()
        backend_dir = script_dir.parent / "backend"
        env_path = backend_dir / ".env"
        
        if env_path.exists():
            load_dotenv(env_path, override=False)  # override=False: не перезаписывать существующие переменные
            print(f"Loaded environment variables from {env_path}")
        else:
            print(f"Warning: .env file not found at {env_path}")
            print("LLM settings should be configured in backend/.env or as environment variables")
    
    ap = argparse.ArgumentParser(description="Step 6.5 smoke-check: LLM-assisted section mapping")
    ap.add_argument("--api", default="http://localhost:8000", help="API base URL (default: http://localhost:8000)")
    ap.add_argument("--workspace-id", default="", help="Workspace UUID (required if --version-id not provided)")
    ap.add_argument("--version-id", default="", help="Existing document version UUID to test against")
    ap.add_argument("--docx", default="", help="Path to protocol DOC/DOCX (если не указан, обрабатываются все DOC/DOCX в каталоге скрипта)")
    ap.add_argument("--timeout", type=int, default=180, help="Ingest polling timeout seconds (default: 180)")
    ap.add_argument("--apply", action="store_true", help="Apply mappings if QC passes")
    ap.add_argument("--max-candidates", type=int, default=3, help="Max candidates per section (default: 3)")
    ap.add_argument("--allow-visual", action="store_true", help="Allow visual headings in assist (default: false)")
    ap.add_argument("--skip-llm", action="store_true", help="Пропустить вызов LLM assist (только ингестия и загрузка)")
    ap.add_argument(
        "--sections",
        default="protocol.objectives,protocol.soa,protocol.eligibility.inclusion",
        help="Comma-separated section_keys to map",
    )

    args = ap.parse_args()
    api_base = args.api.rstrip("/")

    # Inputs
    section_keys = [s.strip() for s in args.sections.split(",") if s.strip()]
    assert_true(len(section_keys) > 0, "No section_keys provided")

    version_id = args.version_id.strip()

    # Если указан version_id, используем существующий (старая логика)
    if version_id:
        print(f"Using existing version_id={version_id}")
        if not args.workspace_id.strip():
            workspace_id = input("Enter WORKSPACE_ID (UUID) for section contracts: ").strip()
        else:
            workspace_id = args.workspace_id.strip()
        assert_true(len(workspace_id) == 36, f"WorkspaceId looks wrong: {workspace_id}")

        # Пропускаем LLM assist, если указан флаг
        if args.skip_llm:
            print("\n[SKIP] LLM assist пропущен (--skip-llm)")
            print(f"version_id={version_id}")
            return

        # Убеждаемся, что нужные section contracts существуют
        print("\nEnsuring section contracts exist...")
        ensure_section_contracts(api_base, workspace_id, section_keys)

        print("\nFetching section_maps BEFORE assist...")
        before_maps = get_section_maps(api_base, version_id)
        before_by_key = {m["section_key"]: m for m in before_maps}
        print(f"  section_maps count={len(before_maps)}")

        print("\nCalling LLM assist endpoint...")
        resp = assist_mapping(
            api_base=api_base,
            version_id=version_id,
            section_keys=section_keys,
            apply=args.apply,
            max_candidates=args.max_candidates,
            allow_visual=args.allow_visual,
        )

        # Basic checks
        assert_true(resp.get("version_id") == version_id, "Response version_id mismatch")
        assert_true(resp.get("secure_mode") is True, "Expected secure_mode=true in response (Step 6.5 should be gated)")
        assert_true(resp.get("llm_used") is True, "Expected llm_used=true in response")

        candidates = resp.get("candidates") or {}
        qc = resp.get("qc") or {}

        print("\nAssist candidates summary:")
        for sk in section_keys:
            c = candidates.get(sk, [])
            print(f"  {sk}: {len(c)} candidates")
            if c:
                top = c[0]
                print(f"    top.heading_anchor_id={top.get('heading_anchor_id')} conf={top.get('confidence')}")

        print("\nQC summary:")
        for sk in section_keys:
            q = qc.get(sk, {})
            print(f"  {sk}: status={q.get('status')} selected={q.get('selected_heading_anchor_id')}")
            errs = q.get("errors") or []
            if errs:
                print(f"    errors: {errs[:2]}{' ...' if len(errs)>2 else ''}")

        # Apply verification
        if args.apply:
            print("\nFetching section_maps AFTER assist (apply=true)...")
            after_maps = get_section_maps(api_base, version_id)
            after_by_key = {m["section_key"]: m for m in after_maps}

            changed = 0
            for sk in section_keys:
                b = before_by_key.get(sk)
                a = after_by_key.get(sk)
                if not a:
                    continue
                if not b:
                    changed += 1
                    continue
                if (b.get("status"), b.get("confidence"), b.get("anchor_ids")) != (a.get("status"), a.get("confidence"), a.get("anchor_ids")):
                    changed += 1

            print(f"  changed mappings (heuristic) = {changed}")
            any_mapped = any((qc.get(sk, {}).get("status") == "mapped") for sk in section_keys)
            if any_mapped:
                assert_true(changed > 0, "QC had mapped sections, but section_maps did not change. apply may not be working.")
        else:
            print("\napply=false: not verifying DB updates (expected).")

        print("\n[OK] STEP 6.5 SMOKE CHECK PASSED")
        print(f"version_id={version_id}")
        print("Tip: if this fails because secure_mode=false, enable SECURE_MODE and BYO keys in your backend env/config.")
        return

    # Новая логика: обработка файлов
    workspace_id = args.workspace_id.strip()
    if not workspace_id:
        workspace_id = input("Enter WORKSPACE_ID (UUID): ").strip()
    assert_true(len(workspace_id) == 36, f"WorkspaceId looks wrong: {workspace_id}")

    script_dir = Path(__file__).parent.absolute()
    
    # Определяем список файлов для обработки
    docx_path_arg = args.docx.strip()
    if docx_path_arg:
        # Обрабатываем только указанный файл
        docx_path = Path(docx_path_arg)
        if not docx_path.is_absolute():
            docx_path = script_dir / docx_path
        assert_true(docx_path.exists(), f"Файл не найден: {docx_path}")
        assert_true(docx_path.suffix.lower() in [".doc", ".docx"], f"Ожидается файл .doc или .docx: {docx_path}")
        files_to_process = [docx_path]
    else:
        # Обрабатываем все DOC/DOCX файлы в каталоге скрипта и поддиректориях
        files_to_process = find_doc_files(script_dir)
        if not files_to_process:
            die(f"Не найдено файлов .doc или .docx в каталоге и поддиректориях: {script_dir}")
        print(f"Найдено {len(files_to_process)} файл(ов) для обработки (рекурсивный поиск):")
        for f in files_to_process:
            # Показываем относительный путь от каталога скрипта
            rel_path = f.relative_to(script_dir)
            print(f"  - {rel_path}")

    # Обрабатываем каждый файл
    results = []
    for docx_path in files_to_process:
        version_id, success = process_single_file(
            api_base=api_base,
            workspace_id=workspace_id,
            docx_path=docx_path,
            section_keys=section_keys,
            apply=args.apply,
            max_candidates=args.max_candidates,
            allow_visual=args.allow_visual,
            timeout=args.timeout,
            skip_llm=args.skip_llm,
        )
        results.append((docx_path.name, version_id, success))

    # Итоговая сводка
    print(f"\n{'='*80}")
    print("ИТОГОВАЯ СВОДКА")
    print(f"{'='*80}")
    success_count = sum(1 for _, _, s in results if s)
    total_count = len(results)
    
    for filename, vid, success in results:
        status = "✓ УСПЕШНО" if success else "✗ ОШИБКА"
        print(f"  {status}: {filename} (version_id={vid})")
    
    print(f"\nОбработано: {success_count}/{total_count} файл(ов) успешно")
    
    if success_count == total_count:
        print("\n[OK] ВСЕ ФАЙЛЫ ОБРАБОТАНЫ УСПЕШНО")
    else:
        print(f"\n[WARNING] {total_count - success_count} файл(ов) завершились с ошибками")
        sys.exit(1)


if __name__ == "__main__":
    main()
