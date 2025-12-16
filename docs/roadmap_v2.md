# Roadmap B — V2 “Ferrari” (MVP → Production-grade)

Цель: увеличить автоматизацию, масштабируемость и соответствие требованиям (GxP/Part 11), не ломая MVP-каркас.

---

## V2.1 LLM-assisted Section Mapping (Auto + QC gate)
**Outcome:** система сама предлагает mapping кандидаты, пользователь подтверждает.

- LLM JSON contract: candidates per section_key
- QC gate:
  - coverage threshold
  - confidence threshold
  - deny out-of-scope anchors
- secure_mode: BYO keys only

**Value**
- сокращение времени mapping 10×
- повышение качества retrieval

---

## V2.2 Claim-first “2-pass” generation (качество текста + контроль)
**Outcome:** два прохода:
1) claims[] + evidence anchors (структурно)
2) финальный нарратив из claims (литературно и консистентно)

- хранить оба артефакта
- QC между проходами

**Value**
- меньше галлюцинаций
- выше качество/стиль текста

---

## V2.3 Retrieval upgrade: Hybrid + adaptive recipes
**Outcome:** устойчивый retrieval на разных документах/языках.

- Hybrid BM25+vector
- learned reranker (по мере данных)
- adaptive chunking (структура + семантика)
- CAG/кэширование по секциям

---

## V2.4 Section Passport authoring UI (без “конфиг-ада”)
**Outcome:** UI для редактирования паспортов без ручного JSON.

- form-based editor + JSON preview
- встроенная валидация схемы
- “lint” контракта (ошибки/анти-паттерны)
- inheritance: base_contract + overrides

---

## V2.5 Cross-document consistency engine (rules + semi-semantic)
**Outcome:** сильный модуль несоответствий.

- deterministic invariants (facts)
- semantic contradictions (LLM assist) только как “signal”, не как truth
- workflow: triage → investigate → resolve/accept risk/suppress

---

## V2.6 Compliance hardening (GxP / 21 CFR Part 11 ready)
**Outcome:** прод-готовность для regulated клиентов.

- RBAC расширенный, policies per workspace/study/doc
- immutable audit trail
- e-signature (опционально)
- validated deployment procedures
- data retention / GDPR tooling

---

## V2.7 Integrations & enterprise readiness
- EDC/CTMS links (read-only first)
- DMS/eTMF export packages
- SSO (SAML/OIDC)
- on-prem / private cloud вариант

---

## V2.8 Scale & performance
- async ingestion workers
- job orchestration
- cost controls: budget per workspace, caching, rate limits
- observability: traces, metrics, alerting

---

## V2 Definition of Done (приземлённо)
- mapping auto-suggest + manual confirm
- claim-first 2-pass включаем для “важных” секций
- паспорт-редактор UI (минимум)
- compliance controls закрывают базовый чек-лист
- стабильная работа на 10+ исследованиях/клиентах
