# Clipper

A Python + FastAPI app for downloading full videos, separate clips, or MP3 audio from a browser UI.

## Run locally

Requires Python 3.11+. On Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe scripts/setup_tools.py
.\.venv\Scripts\python.exe -m uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. On subsequent runs, use `./scripts/start.ps1` or the last command above.

On macOS/Linux, use `.venv/bin/python` instead and install **FFmpeg (including ffprobe)** and **Deno** through your package manager. Put them on `PATH`. The Windows setup script downloads portable tools into `.tools/`; it does not install them system-wide.

## Use

1. Paste a single video URL. **Preview** optionally reads its title and duration.
2. Choose **Video / MP4** or **Audio / MP3** and the output quality.
3. Choose **Full video** or **Trim clips**. Add a From/To row for each clip, for example `01:00–02:00` and `10:00–15:00`. Up to 20 ranges are supported, in your listed order.
4. Start the download. One range produces an MP4/MP3; multiple ranges produce separate clips inside one ZIP. Click **Save file** or **Save ZIP** when ready.

Times accept `2:30`, `01:02:30`, `2m`, or `150s`. Plain numbers mean seconds. Leave **To** blank to continue to the end. Quality options are under **Quality**.

The queue runs one download at a time and supports cancellation and retries. There is no download history or shared download list. Only the current download appears on the page; starting another replaces it, and refreshing clears it. A browser session cookie restricts status, cancellation, and file access to the browser that created the download. Save your file before refreshing or restarting. Job state stays in server memory; files remain in `data/downloads/` until you remove them locally, and temporary files are cleaned after each job. Existing `data/jobs.sqlite3` files from older versions are no longer used.

## Site support and clipping

- Native TikTok and Instagram adapters read embedded player data, metadata, and public media URLs. yt-dlp is the fallback for those sites and provides YouTube and other supported-site extraction.
- Public, prerecorded, single-video links are supported. Login-only media, DRM, live streams, playlists, and multi-video posts are outside this version's scope. Site changes or request restrictions can still prevent a download.
- Accurate trimming downloads the source once and uses FFmpeg to encode each selected interval. A short clip from a long video can therefore require downloading the entire source. Every clip is verified before the ZIP is published; failed batches do not expose partial archives.
- Video output is H.264/AAC MP4. The resolution setting caps the short edge and preserves portrait/landscape shape without upscaling. MP3 defaults to 192 kbps; increasing bitrate does not improve the original source quality.

Keep the server bound to localhost and use **one Uvicorn worker**. This is a personal local app with browser-session download isolation, without user accounts or a distributed queue. Public deployment requires a separate security and hosting design.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/time.test.mjs
```

Node is only needed for frontend tests; the app itself needs no frontend build or npm install. Media tests use generated fixtures and skip when FFmpeg/ffprobe are missing. Network access is not required for the test suite.

```text
app/domain/          Download rules, models, and URL validation
app/application/     Job use cases and dependency protocols
app/infrastructure/  Native extractors, yt-dlp, FFmpeg, session queue, processes, HTTP
app/api.py           FastAPI routes and dependency composition
app/web/             HTML, CSS, and JavaScript UI
tests/               Domain, extractor, API, job, and real-media tests
```

Application code depends on domain rules and protocols. Adapters implement the protocols and are connected in `create_app`; the UI uses the same API documented at **/docs**.

Optional environment variables:

| Variable | Default |
| --- | --- |
| `DOWNLOADER_DATA_DIR` | `./data` |
| `FFMPEG_PATH`, `FFPROBE_PATH`, `DENO_PATH` | `.tools/`, then `PATH` |
| `MAX_DOWNLOAD_BYTES` | 2 GiB source and combined output limit per job |
| `JOB_TIMEOUT_SECONDS` | 3600 per download or processing operation |

If a supported site stops working, update yt-dlp and its dependencies with `python -m pip install -U "yt-dlp[default]"` inside the virtual environment. Re-run tests after dependency changes. `requirements.lock` records the tested Python dependency versions.
