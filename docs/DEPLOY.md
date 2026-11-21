# Deploying Lekha on free tiers

| Piece | Service (free plan) | Notes |
|---|---|---|
| Website | **Vercel** (Hobby) | Next.js; proxies `/api/*` to the API so cookies stay first-party |
| API + worker | **Render** (Free web service, Docker) | Sleeps after ~15 min idle; the first request after that takes ~1 minute to wake |
| Database | **Supabase** (Free) | PostgreSQL 16 with row-level security |
| File storage | **Supabase Storage** (S3-compatible) | Private bucket; files are served only via Lekha's signed links |
| AI | **Google AI Studio** (Gemini free tier) | Optional; offline extraction works without it |

No credit card is needed for any of these.

## 1. Supabase (database + storage)

1. Create a project (choose the **Mumbai** region for Indian users). Save the database password.
2. **Database URL:** Project → **Connect** → **Session pooler** → copy the URI. Change `postgresql://` to `postgresql+asyncpg://`. This is `DATABASE_URL`.
3. **Storage:** Storage → **New bucket** → name `lekha`, **private**.
4. **S3 keys:** Storage → Settings → **S3 Connection** → copy the *Endpoint* (`S3_ENDPOINT`) and *Region* (`S3_REGION`), then **New access key** → copy *Access key ID* (`S3_ACCESS_KEY`) and *Secret access key* (`S3_SECRET_KEY`).

Lekha creates its tables, the restricted `lekha_app` role and all row-level security policies itself on first start (`lekha migrate`).

## 2. Render (API)

1. New → **Blueprint** → connect the GitHub repository. Render reads `render.yaml`.
2. Fill the variables marked *sync: false*: `DATABASE_URL`, `S3_ENDPOINT`, `S3_REGION`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `GEMINI_API_KEY`, and — after step 3 — `LEKHA_WEB_ORIGIN` and `LEKHA_PUBLIC_API_URL` (both the Vercel URL).
3. Deploy. Check `https://<service>.onrender.com/health` returns `{"ok": true}`.

## 3. Vercel (website)

1. **Add New → Project** → import the repository. **Root Directory:** `apps/web`.
2. Environment variable `LEKHA_API_URL` = the Render URL (e.g. `https://lekha-api.onrender.com`).
3. Deploy. Put the resulting URL into Render's `LEKHA_WEB_ORIGIN` and `LEKHA_PUBLIC_API_URL` and redeploy the API.

## 4. Smoke test

Open the Vercel URL → **Try the live demo**. If the API was asleep the first click can take a minute. Then upload a bill in the Inbox and import a GSTR-2B.

## Limits of the free setup

- The Render free instance has 512 MB RAM and a fraction of a CPU: reading a phone photo takes longer than on a laptop. `LEKHA_WORKER_CONCURRENCY=1` and `LEKHA_OCR_PROCESSES=1` keep memory in bounds.
- Gemini free-tier quotas are small and change; Lekha fails over between models and falls back to offline extraction when the quota is used up.
- For production use: a paid instance (or separate worker process with `LEKHA_WORKER_IN_API=false` + `lekha worker`), daily database backups, and an email provider (`RESEND_API_KEY`) for invites and follow-ups.
