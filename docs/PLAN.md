# Lekha — Technical Plan

> Status: **FINAL — approved for build** · Timeline: 3 days · Owner: Krishna (product) · Builder: Claude Code
> Companion study guide (domain from zero): https://claude.ai/artifact/AhUvR7bqKjT2SETX6wWXtz

Lekha takes an Indian GST purchase invoice from **photo → verified data → GSTR-2B match → IMS decision → protected tax credit**, tracks every rupee of input tax credit (ITC) at risk, predicts whether missing credit will come back, drives suppliers to fix errors, and defends the business when a notice arrives. Every number traces back to the source document.

---

## 1. Scope (22 parts — all ship in v1)

| ID | Part | One-line definition |
|----|------|---------------------|
| M1 | Workspaces & access | Firm → clients → GSTINs; roles (owner, partner, staff, client, viewer); invites; Postgres row-level security; append-only audit log |
| M2 | Smart intake | Bulk upload (PDF/JPG/PNG/HEIC), phone camera capture (PWA), **per-client magic upload link** (no login), duplicate-file detection (SHA-256 + perceptual hash) |
| M3 | Extraction engine | OCR tokens with boxes + Claude vision → strict-schema JSON; every field **grounded** to OCR tokens; calibrated confidence; **supplier memory** (past corrections become hints) |
| M4 | Validation engine | GSTIN checksum/state/PAN, IGST vs CGST+SGST by place of supply, rate valid **for invoice date**, arithmetic, invoice-number rules, FY checks, **e-invoice QR decode + cross-check**; auto-corrections where provable |
| M5 | Review workstation | Image/PDF beside fields, click field ↔ highlight box, low-confidence first, keyboard-first, approve/next |
| M6 | Purchase register & payments | Register from approved invoices; Excel/Tally-CSV import with column mapping; payments; aging |
| M7 | Reconciliation engine | GSTR-2B JSON/Excel import; candidate generation → feature scoring → optimal assignment (Hungarian) → many-to-one; mismatch taxonomy; **explanation per match**; tolerances |
| M8 | IMS decision assistant | Accept / Reject / Pending recommendation with reason codes; deadline countdown (14th); bulk decide; exportable action list |
| M9 | ITC risk ledger | Rule 37 (180-day), Rule 37A, §43B(h) MSME 45-day, §16(4) time limit, §17(5) blocked credits; ₹ amounts, due dates, alerts |
| M10 | Supplier hub | Compliance scorecard, follow-up drafts (email/WhatsApp text, EN/HI/TE), **supplier self-service link**, follow-up tracking & escalation |
| M11 | Anomaly & fraud signals | Duplicates (exact/fuzzy/image), new-supplier-large-value, round amounts, sequence anomalies, QR-vs-print mismatch, **template reuse across different GSTINs** |
| M12 | Exports & reports | Tally XML vouchers, Excel recon workbook, client PDF summary, CSV/JSON |
| M13 | Copilot | Plain-language Q&A over a client's data via safe read-only tools; explanations in English/Hindi/Telugu |
| M14 | Dashboards | Firm readiness board, client ITC claimed / at-risk / recovered, **working capital trapped**, supplier trends |
| M15 | Benchmark & demo data | Synthetic GST invoice generator (many templates + phone-photo degradation), GSTR-2B generator with planted errors, eval harness, published accuracy; one-click demo workspace |
| M16 | Platform | Postgres job queue + workers, notifications, public API + keys, tests, CI/CD, logging/metrics, deploy, docs |
| N1 | ITC Recovery Forecaster | Per-supplier filing-delay model (discrete-time hazard, empirical-Bayes shrinkage) → P(appears next 2B), P(never) → recommendation |
| N2 | Notice Defense Pack | DRC-01C / ASMT-10 reply draft + evidence table + downloadable ZIP bundle, generated from the traceability chain |
| N3 | Cross-client supplier intelligence | Firm-wide supplier signals propagated to all clients; supply graph; circular-trading cycle detection; shell-supplier scoring |
| N4 | Rules time-travel engine | Versioned rules with effective dates; evaluate each invoice under the rules of its date; draft-rule **impact simulation** before publishing |
| N5 | Monthly close workflow ("close agent") | On 2B upload, runs the full cycle automatically and produces an approval checklist; nothing is filed or sent without a human |
| N6 | GSTR-3B Table 4 builder | Computes 4A / 4B(1) / 4B(2) / 4C / 4D(1) / 4D(2) from recon + risk ledger, every cell drill-down to invoices |

Out of scope by necessity (not deferral): live GST-portal API (needs a licensed GSP), live GSTIN active-status lookup, WhatsApp Business API sending (needs Meta business verification). Each has a clean adapter interface so it can be plugged in later.

---

## 2. Architecture

```
                ┌──────────────────────────── Browser ────────────────────────────┐
                │  Next.js 15 (App Router, TS, Tailwind)  · PWA camera capture     │
                └───────────────┬──────────────────────────────────────────────────┘
                                │ HTTPS (cookie session / API key)
                ┌───────────────▼──────────────────────────────────────────────────┐
                │  FastAPI (Python 3.12)   REST + OpenAPI  · auth · RLS context     │
                │  routers → services → repositories (SQLAlchemy 2)                │
                └───────┬───────────────┬────────────────────┬─────────────────────┘
                        │ enqueue       │ SQL                │ signed URLs
                ┌───────▼──────┐ ┌──────▼──────────┐ ┌───────▼──────────────┐
                │ Worker(s)    │ │ PostgreSQL 16   │ │ Object storage (S3   │
                │ same codebase│◄┤ data + jobs     │ │ API: Supabase / MinIO│
                │ SKIP LOCKED  │ │ queue + RLS     │ │ locally)             │
                └───────┬──────┘ └─────────────────┘ └──────────────────────┘
                        │
              ┌─────────▼─────────┐
              │ Claude API        │  extraction (vision), reasons, drafts, copilot
              └───────────────────┘
```

**Why this shape**
- **One Python codebase** for API + worker: OCR, imaging, matching, statistics and graph code are all Python-native.
- **Postgres as the queue** (`jobs` table, `FOR UPDATE SKIP LOCKED`, retries with backoff, dead-letter state) — no Redis to host, transactional enqueue with the data that caused it.
- **Row-level security**: every tenant table has `firm_id`; the API sets `app.firm_id` per transaction; RLS policies enforce isolation even if application code has a bug. Tested explicitly.
- **Workflow, not free-roaming agent**, for the monthly close (N5): steps are deterministic code; Claude writes only reasons and messages. Safer and auditable.
- **Copilot** (M13) uses the Messages API tool runner with in-process, read-only, tenant-scoped tools — a short in-app Q&A that needs no sandbox. (Anthropic Managed Agents is the alternative if we ever want hosted long-running agents.)

### Repository layout
```
lekha/
  apps/
    api/            FastAPI app + worker + domain engines (Python)
      lekha/
        core/       config, db, security, rls, logging, errors
        models/     SQLAlchemy models
        schemas/    Pydantic I/O schemas
        routers/    HTTP endpoints
        services/   use-cases (orchestrate engines + repos)
        engines/    pure domain logic: gstin, tax, rules, validation, matching,
                    risk, forecast, graph, table4, tally, qr
        ai/         Claude client, prompts, extraction, drafting, copilot tools
        ocr/        OCR + PDF text layer + image preprocessing
        jobs/       queue, worker loop, job handlers
        bench/      synthetic invoice + 2B generators, eval harness
      migrations/   Alembic
      tests/        unit / property / integration / api
    web/            Next.js app
  docs/             PLAN, ARCHITECTURE, ADRs, USER_GUIDE, API, BENCHMARK
  infra/            docker-compose, Dockerfiles, render.yaml
  .github/workflows CI
```

---

## 3. Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind, Radix primitives, TanStack Query + Table, Recharts, pdf.js | Industry standard, fast to build polished UI |
| API | FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic | Typed, auto OpenAPI, Python ecosystem for ML |
| DB | PostgreSQL 16 (Supabase in prod, Docker locally) | Relational integrity, RLS, JSONB, queue |
| Storage | S3-compatible (Supabase Storage prod, MinIO local) | Portable |
| OCR | RapidOCR (ONNX) for images, PyMuPDF text layer for digital PDFs | Pip-installable, returns word boxes for grounding |
| QR | zxing-cpp | Decodes e-invoice signed QR (JWT payload) |
| AI | Anthropic Python SDK, model `claude-opus-5-5` (env-configurable), structured outputs, server-side refusal fallbacks | Best vision+reasoning; model swap is one env var |
| Matching | rapidfuzz, scipy `linear_sum_assignment` | Fuzzy features + optimal assignment |
| Stats / graph | numpy, networkx | Forecaster, cycle detection |
| Images | Pillow, imagehash, OpenCV-headless | Preprocess, perceptual hashing, degradation |
| Exports | openpyxl, lxml (Tally XML), WeasyPrint-free PDF via reportlab | No system deps |
| Auth | Argon2 passwords, server-side sessions (httpOnly cookie), hashed API keys, signed magic-link tokens | No 3rd-party auth vendor |
| Tests | pytest, hypothesis, httpx test client, testcontainers-style Docker Postgres, Vitest, Playwright | Unit → E2E |
| CI/CD | GitHub Actions; Vercel (web), Render (API + worker), Supabase (DB + storage) | Free tiers |
| Tooling | uv (Python), npm, ruff, mypy, eslint, prettier | Fast, strict |

---

## 4. Data model (core tables)

All tenant tables carry `firm_id` (RLS) and `created_at/updated_at`. Money is stored as `NUMERIC(14,2)`; never floats.

- **firms**, **users**, **memberships**(firm, user, role), **sessions**, **api_keys**, **invites**
- **clients** (a business served by the firm) → **gstins** (client may hold several)
- **suppliers** (firm-scoped by GSTIN; legal/trade name, state, msme_category, udyam_no, first_seen) — shared across the firm's clients (enables N3)
- **documents** (storage key, sha256, phash, mime, pages, source: upload/link/camera, status)
- **extractions** (document, model, prompt_version, raw_json, tokens_in/out, cost, latency)
- **invoices** (client_gstin, supplier, doc_type: invoice/credit/debit/bill_of_supply, number, number_norm, date, pos, reverse_charge, taxable, igst, cgst, sgst, cess, total, irn, qr_status, status: extracted/review/approved/rejected, approved_by)
- **invoice_lines** (hsn, description, qty, unit, rate, taxable, tax_rate, tax amounts, blocked_17_5 flag)
- **field_evidence** (invoice, field_path, value, confidence, token_ids, bbox, source: model/validator/memory/qr/human)
- **validation_results** (invoice, rule_code, rule_version, severity, message, auto_fix)
- **supplier_memory** (supplier, field_path, pattern/hint, hits)
- **payments** (invoice, amount, date, mode, reference)
- **gstr2b_imports** (client_gstin, period, file, generated_on) → **gstr2b_records** (supplier gstin, trade name, number, date, pos, values, itc_available, reason, irn, filing date)
- **matches** (invoice, record, score, features JSON, explanation JSON, kind: exact/normalised/fuzzy/split, status)
- **ims_decisions** (record, recommendation, reason_codes, decided_action, decided_by, decided_at)
- **risk_items** (client_gstin, invoice/record, type: R37/R37A/43BH/16_4/17_5/NOT_IN_2B/MISMATCH, amount, due_date, status)
- **forecasts** (record/invoice, p_next, p_never, model_version)
- **followups** (supplier, client, channel, language, body, status, sent_at, responded_at) · **supplier_links** (token hash, scope, expires)
- **graph_signals** (firm, kind: cycle/shell/template_reuse, members, score, evidence)
- **notices** (client_gstin, form, period, amount, received_on) → **notice_packs** (draft text, evidence manifest, zip key)
- **rules** / **rule_versions** (code, params JSON, effective_from/to, status: draft/published) · **hsn_rates** (hsn prefix, rate, effective_from/to)
- **close_runs** / **close_steps** (period, step, status, output, approved_by)
- **table4_snapshots** (client_gstin, period, cells JSON, sources JSON)
- **jobs** (kind, payload, status, attempts, run_after, last_error)
- **notifications**, **audit_log** (actor, action, entity, before/after JSON — append-only)

---

## 5. Key algorithms

**Extraction (M3)** — preprocess (EXIF rotate, deskew, PDF → page images; digital PDFs keep text layer) → OCR tokens with ids + boxes → Claude receives the page image(s) + compact token list + supplier-memory hints → returns schema-valid JSON where each field cites token ids → grounding check (does the cited text actually contain the value?) → validators → **confidence fusion**: features (model certainty, grounding match, validator pass, memory agreement, QR agreement) → logistic calibration fitted on the benchmark → auto-approve threshold configurable.

**Validation & auto-fix (M4)** — deterministic rules; when arithmetic proves one value wrong (e.g. CGST ≠ SGST but taxable×rate/2 matches one) the validator proposes a fix with evidence; the human sees it. GSTIN with a broken checksum is checked against single-character substitutions that yield a valid checksum and a known supplier → suggested fix.

**Matching (M7)** — normalise invoice numbers (case, separators, leading zeros, FY tokens like `24-25`/`2024-25`), candidates by supplier GSTIN (plus near-GSTIN on same PAN), features (number similarity, date gap, taxable/tax deltas, total delta), weighted score, **Hungarian assignment per supplier** to prevent double matching, then split/merge (one invoice ↔ multiple records) search; every match stores the per-feature contributions → human-readable explanation.

**Risk ledger (M9)** — rule engine evaluates each invoice/record under rule versions effective on the relevant dates; produces risk items with ₹ amount and due date; re-runs on payment, new 2B, rule change.

**Forecaster (N1)** — for each supplier, history of (invoice month → months until it appeared in 2B, or censored). Discrete-time hazard per lag, Beta prior from the firm-wide population, empirical-Bayes shrinkage so new suppliers borrow strength. Outputs P(appears in next 2B), P(not before §16(4) deadline). Evaluated by backtesting on synthetic history (Brier score).

**Graph (N3)** — directed multigraph of GSTINs (clients + suppliers) with invoice-value edges within one firm; bounded simple-cycle search (length ≤ 5, value-weighted); shell score from first-seen recency, value spikes, round amounts, many buyers + no inbound supply, and **template reuse** (perceptual hash of invoice layout shared by different GSTINs).

**Table 4 (N6)** — 4A(5) eligible ITC from accepted 2B records matched to approved invoices; 4B(1) permanent reversals (§17(5), Rule 38/42/43 hooks); 4B(2) temporary reversals (Rule 37, 37A, not-received); 4D(1) reclaimed after payment / later appearance; every cell stores its source ids.

**Time-travel (N4)** — `rule_versions` with effective ranges; evaluation takes `as_of` date; a draft version triggers an impact job that re-evaluates affected invoices against draft vs published and stores a diff.

---

## 6. Screens

Sign in / sign up · Firm dashboard (client readiness board) · Client overview (ITC claimed/at-risk/recovered, working capital trapped, deadlines) · Inbox (documents & processing status) · Review workstation · Invoices / purchase register · Payments · GSTR-2B imports · Reconciliation (filters by mismatch type, explanation drawer) · IMS board · Risk ledger · Forecasts · Suppliers (list, scorecard, follow-ups) · Supplier self-service page (public, token) · Client magic upload page (public, token) · Fraud & graph signals (graph view) · Notices & defense packs · Table 4 builder · Monthly close (step checklist) · Copilot panel · Rules & impact simulator · Exports · Benchmark report · Settings (team, roles, API keys, tolerances) · Audit log.

---

## 7. Quality bar

- **Tests**: unit + property tests (hypothesis) for every engine (GSTIN, tax math, rules by date, matching, risk, forecast, table 4, Tally XML); API integration tests on real Postgres incl. **cross-tenant isolation tests**; job-queue tests; Playwright E2E for the main path (upload → review → 2B import → reconcile → IMS → Table 4 → export). Target ≥ 85% line coverage on `engines/`.
- **Static checks**: ruff, mypy (strict on engines), eslint, tsc — all in CI, all green before deploy.
- **Security**: Argon2, httpOnly+SameSite cookies, CSRF token on mutations, rate limiting on auth and public links, signed short-lived file URLs, secrets only in env, upload type/size limits, audit log.
- **Observability**: structured JSON logs with request ids, job metrics, AI cost/latency per extraction stored and shown.
- **Docs**: README (run in 3 commands), ARCHITECTURE, ADRs, USER_GUIDE, API (OpenAPI), BENCHMARK report.

---

## 8. Build schedule (3 days)

**Day 1 — Foundation and the document pipeline**
1. Repo, tooling, Docker (Postgres + MinIO), CI skeleton
2. DB models + migrations + RLS; auth, firms, clients, GSTINs, invites, audit log
3. Engines: GSTIN, tax/place-of-supply, rules + HSN rates with effective dates (N4 core), validation
4. Job queue + worker
5. Intake (upload, camera, magic link, dedupe) → OCR → Claude extraction → grounding → confidence
6. Web: shell, auth, dashboard skeleton, inbox, **review workstation**
7. Synthetic invoice generator (needed for tests and demo)
✅ Checkpoint: you upload a real bill on your phone and review its extracted fields.

**Day 2 — Money engines**
1. Purchase register, payments, imports
2. GSTR-2B parser + generator; reconciliation engine + explanations
3. IMS assistant, risk ledger, Table 4 builder
4. Supplier hub, follow-up drafts, supplier self-service page
5. Forecaster (N1), graph & fraud signals (N3), anomaly checks (M11)
6. Web screens for all of the above
✅ Checkpoint: demo workspace with 12 months of data; you run a full reconciliation.

**Day 3 — Intelligence, polish, ship**
1. Monthly close workflow (N5), notice defense pack (N2), rules impact simulator UI (N4)
2. Copilot (M13), dashboards (M14), exports (M12), public API + keys
3. Benchmark run + report (M15), confidence calibration
4. E2E tests, security pass, performance pass, docs
5. Deploy: Supabase, Render, Vercel; smoke test in production
✅ Checkpoint: live URL; you sign up as a new user and run the whole flow.

---

## 9. Tasks for Krishna (only what Claude cannot do)

**Now (needed on Day 1 for extraction):**
1. Create an Anthropic API key: https://console.anthropic.com → sign up → *Billing* → add credit (US$10 covers all development and the benchmark at current prices) → *API Keys* → *Create key*.
2. Put it in `C:\Users\Krishna\lekha\apps\api\.env` as `ANTHROPIC_API_KEY=...` (the file will exist after the first setup step; don't paste the key in chat).

**Day 3 (deployment) — exact steps will be given then:**
3. GitHub account + install GitHub CLI and sign in (`winget install GitHub.cli`, `gh auth login`)
4. Supabase account (free) — one project
5. Render account (free) — sign in with GitHub
6. Vercel account (free) — sign in with GitHub

**Each day:** test the checkpoint flow and report anything that feels wrong.

---

## 10. Costs

Hosting: free tiers. AI: default model `claude-opus-5-5` (US$4 / US$20 per million input/output tokens) ≈ US$0.02–0.04 per invoice. The model is one environment variable (`LEKHA_AI_MODEL`); switching to a cheaper model such as `claude-haiku-5-5` is your call and needs no code change — the benchmark will show the accuracy difference.

## 11. Disclaimers built into the product

Lekha recommends; humans decide. Not tax advice. Rule tables carry effective dates and sources and must be kept current. Data is per-firm isolated and deletable on request.
