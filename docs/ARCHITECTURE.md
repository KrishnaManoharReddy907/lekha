# Architecture

## Shape

```
Browser ── Next.js (Vercel) ── /api/* proxy ──► FastAPI (Render)
                                                 │  routers → services → engines
                                                 │  in-process worker (Postgres job queue)
                                                 │  OCR process pool
                                                 ├──► PostgreSQL (Supabase) — data, jobs, RLS
                                                 ├──► Object storage (Supabase S3 / local disk)
                                                 └──► AI provider (Gemini free tier · Claude · offline)
```

- **One origin for the browser.** The web app proxies `/api/*` to the API, so the session cookie is first-party and there is no CORS surface.
- **One Python codebase** for API and worker: OCR, imaging, matching, statistics and graph code are Python-native.
- **Postgres as the queue.** Jobs are rows claimed with `FOR UPDATE SKIP LOCKED`, enqueued in the same transaction as the change that caused them, retried with backoff, then marked dead with an `on_dead` hook. No Redis to run.
- **OCR in a process pool.** OCR holds the GIL; running it in threads starved the event loop and slowed every request. A small `ProcessPoolExecutor` keeps the API responsive.
- **Long work never blocks a request.** Demo creation, notice packs, reconciliation runs and the monthly close are background jobs; pages poll for status. This survives proxy and hosting timeouts.

## Layers (apps/api/lekha)

| Layer | Responsibility | Examples |
|---|---|---|
| `engines/` | Pure domain logic, no I/O, exhaustively tested | `gstin`, `rules`, `validation`, `matching`, `ims`, `risk`, `table4`, `forecast`, `graph`, `qr`, `tally`, `confidence` |
| `services/` | Use-cases: load data, call engines, persist results | `pipeline`, `reconcile`, `risk`, `close`, `notices`, `copilot`, `demo` |
| `routers/` | HTTP, auth context, serialization | one file per area |
| `core/` | config, DB + tenant context, security, storage, audit, errors | `deps.Ctx` carries db, firm, role, scope |
| `ai/` | provider-neutral `complete()`; Gemini (REST) and Claude (SDK) adapters; extraction prompt + schema; offline extractor | |
| `ocr/` | page rendering, deskew, OCR tokens with normalised boxes, page hashes | |
| `jobs/` | queue, worker, handlers | |
| `bench/` | synthetic invoice + GSTR-2B generators, evaluation harness | |

## Multi-tenancy and security

- Every tenant table carries `firm_id`. Each request runs in a transaction that sets `app.firm_id`; Postgres **row-level security** (`FORCE ROW LEVEL SECURITY`, policy `firm_id = current_setting('app.firm_id')`) enforces isolation even if application code has a bug. The app connects through a dedicated `lekha_app` role without `BYPASSRLS`. Tests prove a raw query under firm B cannot read or write firm A's rows.
- Sessions: random tokens stored as keyed hashes, httpOnly SameSite cookies, CSRF header on every mutation. Passwords: Argon2. API keys: hashed, shown once. Public links (client upload, supplier portal): hashed tokens with expiry and revocation. Files: served only through short-lived HMAC-signed URLs.
- Audit log is append-only (the app role has no UPDATE/DELETE on it).

## Key algorithms

- **Grounded extraction.** The model receives page images and the OCR token list and must cite token ids per field. Confidence fuses model certainty, grounding (is the value actually in the cited text?), validator results, signed-QR agreement, supplier memory and OCR confidence through a logistic model calibrated on the benchmark.
- **Signed-QR reconciliation.** A disagreement with the e-invoice QR is corrected only if the QR value is printed on the page, the extracted value is provably invalid, or the two are near-identical; otherwise it stays a QR mismatch (tamper signal).
- **Matching.** Candidates by supplier PAN; features for GSTIN, number (exact / normalised / separator-insensitive / shared serial / fuzzy), date, taxable value, tax, document type; split/merge pre-pass; Hungarian assignment per supplier; typed mismatches with explanations.
- **Rules time-travel.** `RuleBook.get(code, on=date)` returns the version effective on that date; drafts overlay published rules for impact simulation.
- **Forecaster.** Discrete-time hazard per lag, per supplier, shrunk to the firm population with a Beta prior; outputs P(next 2B), P(before Sec 16(4) deadline), P(never), with a recommendation.
- **Graph.** Bounded simple-cycle search over supplier→buyer value edges, shell-supplier scoring, layout-hash template reuse — computed per firm only.

## AI usage

- Extraction, close summaries, notice letter polishing and the copilot are the only AI calls. Everything with money or legal effect is deterministic code, and every consequential action needs human approval.
- Provider-neutral: Gemini free tier by default (model chain with quota/overload/timeouts handled by cool-downs and fast failover), Claude when configured, offline rules otherwise. Demo documents always use offline extraction so visitors never consume quota.
- The copilot is a JSON tool loop over read-only, tenant-scoped functions; it cannot change data.

## Data model

38 tables; see `apps/api/lekha/models`. Money is `NUMERIC(14,2)` end to end; the API serialises it as strings.
