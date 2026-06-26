# Quran Video Platform

Private local Quran video editor plus unattended daily YouTube automation.

The implementation uses Python 3.12, FastAPI, Redis/RQ, SQLite job records, FFmpeg/libass, Pillow with RAQM, React/TypeScript/Vite, Docker Compose, and GitHub Actions.

## Local Setup

1. Install system packages.
   - Debian/Ubuntu: `sudo apt-get update && sudo apt-get install -y ffmpeg fontconfig fonts-hosny-amiri fonts-liberation2 fonts-open-sans libfribidi0 libharfbuzz0b libass9 redis-server unzip`
   - Arch: `sudo pacman -S --needed --noconfirm ffmpeg fontconfig ttf-amiri ttf-liberation redis unzip`
   - macOS: `brew install ffmpeg fontconfig redis && brew install --cask font-amiri font-liberation`
2. Run `make bootstrap`.
3. Copy `.env.example` to `.env`. The default fixture mode works offline; fill Quran.Foundation credentials before using live Quran data.
4. Put user-owned background media in `media/backgrounds/`.
5. Run `make doctor`.
6. Run `make dev`, then open `http://127.0.0.1:3000`.

`make dev` starts the FastAPI server and Vite dev server locally, which is the fastest path while editing. Use `make dev-docker` only when you specifically want the full Docker Compose stack, or `make dev-auto` when you want Docker if it is available and the local path otherwise.

Docker builds are optimized for repeated use: `.dockerignore` excludes local virtualenvs, `node_modules`, renders, media, and caches from the build context; Python dependency layers are cached separately from source edits; and the web container runs `npm ci` only when `apps/web/package-lock.json` changes.

The local default `QURAN_VIDEO_DATA_MODE=fixture` works offline with bundled test data. Set `QURAN_VIDEO_DATA_MODE=quranfoundation` after adding Quran.Foundation credentials to use the official Content API for Uthmani Arabic text, Saheeh International translation, surah metadata, reciter selection, complete-surah audio, ayah timestamps, and word timing segments. `QURAN_VIDEO_DATA_MODE=mp3quran` uses the temporary legacy MP3Quran plus Al Quran Cloud path, and `QURAN_VIDEO_DATA_MODE=alqurancloud` uses the older all-Al-Quran-Cloud path. The app does not silently fall back when Quran.Foundation credentials are missing.

## Quran Foundation Credentials

1. Request Quran Foundation Content API credentials from Quran Foundation.
2. Set `QF_CLIENT_ID`, `QF_CLIENT_SECRET`, and `QF_ENV=production`.
3. The server performs OAuth2 client-credentials authentication with scope `content` at `https://oauth2.quran.foundation`, calls `https://apis.quran.foundation`, caches tokens until 60 seconds before expiry, sends `x-auth-token` and `x-client-id` on API calls, and retries temporary failures without logging secrets or access tokens.
4. Optional advanced settings are available as `QF_AUTH_BASE`, `QF_API_BASE`, `QF_CONNECT_TIMEOUT`, `QF_READ_TIMEOUT`, `QF_RETRIES`, `QF_TOKEN_REFRESH_MARGIN_SECONDS`, `QF_MAX_TIMING_OVERFLOW_MS`, and `QF_MIN_TIMING_COVERAGE`.

## YouTube One-Time OAuth Setup

1. Create a Google Cloud project.
2. Enable YouTube Data API v3.
3. Create OAuth desktop credentials.
4. Set `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` locally.
5. Run `python scripts/generate_youtube_refresh_token.py`.
6. Save the printed `YOUTUBE_REFRESH_TOKEN` as a local env var and GitHub Actions secret.
7. Obtain the target channel ID from YouTube Studio or `channels.list(mine=true)` and set `YOUTUBE_CHANNEL_ID`.

The automation verifies that the authenticated channel exactly matches `YOUTUBE_CHANNEL_ID` before upload. If Google forces uploads private because the API project is unaudited, the workflow leaves them private and reports the compliance-audit requirement.

## Telegram Setup

1. Create a Telegram bot with BotFather.
2. Send a message to the bot from the target chat.
3. Read the chat ID with Telegram’s `getUpdates`.
4. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` locally and as GitHub Actions secrets.

## Background Release

Place only user-owned media in `media/backgrounds/`, then run:

```bash
python scripts/publish_background_release.py
python scripts/publish_background_release.py --publish
```

The release tag is `backgrounds-latest`. Assets are public and downloadable. The helper packages only files under `media/backgrounds/`, writes `backgrounds-manifest.json`, splits ZIPs under 1.9 GiB, records SHA-256, dimensions, media type, and duration, and rejects corrupt or unsupported files.

## GitHub Actions

Add these secrets:

- `QF_CLIENT_ID`
- `QF_CLIENT_SECRET`
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`
- `YOUTUBE_CHANNEL_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The daily workflow has UTC cron entries for 03:00 and 04:00 and gates them to exactly 05:00 Europe/Berlin for CET/CEST. Manual dispatch defaults to `dry_run=true`; dry runs do not advance `automation/state.json` unless `advance_state=true`.

## Commands

```bash
make bootstrap
make doctor
make dev
make test
make render-sample
make qf-smoke
make stop
```

Required individual checks:

```bash
python -m compileall apps packages worker scripts
ruff check .
ruff format --check .
mypy apps packages worker scripts
pytest -q
npm ci --prefix apps/web
npm run lint --prefix apps/web
npm run typecheck --prefix apps/web
npm run test --prefix apps/web
npm run build --prefix apps/web
docker compose config
python scripts/validate_workflow.py
python scripts/doctor.py --local
python scripts/render_sample.py
python scripts/qf_smoke.py
```

## Data and Security

The repo is safe to publish. `.gitignore` excludes credentials, OAuth files, private fonts, downloaded audio, background media, thumbnails, rendered videos, and SQLite databases. The browser never receives Quran Foundation secrets or tokens. FFmpeg is invoked with argument arrays. Background uploads are stored only under `media/backgrounds/` with server-generated filenames and probed before use.
