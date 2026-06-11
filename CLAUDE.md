# reels-service

Flask service that edits Instagram Reels server-side. Receives a raw video URL, returns a finished 1080x1920 reel with hook, zoom cuts, Sora cutaways, Gemini overlays, karaoke captions and a 3-track audio mix.

## Stack

Python 3.12+, Flask, Gunicorn, FFmpeg, PIL, `auto-editor`. External: OpenAI (Whisper / GPT-4o-mini / Sora 2), Gemini (images), Supabase (Postgres + Storage).

## Layout

- `app.py` — Flask app, 4 endpoints (`/health`, `POST /edit`, `GET /status/:id`, `GET /jobs`). Jobs run in a background `threading.Thread`; in-memory dict is the fast cache, Supabase is the source of truth.
- `lib/pipeline.py` — the actual editing pipeline (~1100 lines). Driven by a `progress_callback(progress, step)`.
- `lib/supabase_client.py` — thin REST wrapper around Supabase (`user_settings`, `reels_jobs`, Storage upload). No `supabase-py` dependency, just `requests`.
- `fonts/Impact.ttf`, `music/epic_games.mp3` — bundled assets required by the pipeline. Don't remove.
- `Dockerfile` — production image (installs ffmpeg + auto-editor on top of `python:3.12-slim`).
- `DOCUMENTACAO.md` — long-form Portuguese docs (architecture, endpoints, pipeline internals, troubleshooting). Read it before changing the pipeline.

## Run locally

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install auto-editor
cp .env.example .env   # then edit
.venv/bin/python app.py
```

System dep: `ffmpeg` on PATH (`sudo apt install ffmpeg`).

## Environment variables

Required: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. Optional: `SERVICE_SECRET` (alternative bearer token for the API), `PORT` (default 3001).

API keys for OpenAI and Gemini are **not** env vars — they're fetched per-request from `user_settings` in Supabase, keyed by `user_id`.

## Auth model

All endpoints except `/health` require `Authorization: Bearer <token>`. The token must match either `SUPABASE_SERVICE_ROLE_KEY` or `SERVICE_SECRET`.

## Supabase schema

Two tables — `reels_jobs` (job state) and `user_settings` (per-user API keys + Instagram metadata). Schema in `DOCUMENTACAO.md`. Storage bucket `user-uploads` must exist.

## Gotchas

- `--workers 1` in gunicorn is intentional. Each job is CPU/RAM heavy; don't scale workers.
- `--timeout 600` because pipelines can take ~10 min.
- Without `auto-editor` the pipeline skips silence removal but still works.
- HDR→SDR is not handled; HDR inputs come out washed.
- Sora generation has a 5-min timeout; on failure the pipeline continues without cutaways.
