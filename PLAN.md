# Implementation plan

## Stack and scope

Python + FastAPI serve the API and a build-free HTML/CSS/JavaScript UI. FFmpeg/ffprobe handle accurate clipping, encoding, and verification. Native TikTok and Instagram extractors resolve public page media; yt-dlp provides fallback extraction and broader platform support.

The first release runs locally with one worker. It supports single prerecorded links, full media or up to 20 start/end intervals, MP4 video with audio where available, and MP3 audio. Multiple intervals produce separate clips in one ZIP, with the source downloaded once. Site availability and access restrictions remain platform-dependent.

## Architecture

- **Domain:** format/range rules, source/output models, and URL validation.
- **Application:** job orchestration, cancellation, publication of completed artifacts, and protocols for external dependencies.
- **Infrastructure:** extraction, bounded HTTP downloads, subprocess management, FFmpeg, and SQLite history.
- **Delivery:** FastAPI routes and a responsive browser UI.

Dependencies point toward domain rules and application protocols. FastAPI composes the concrete adapters. A site extractor can change without changing the job workflow or UI.

## Delivery checklist

- [x] Blue gradient browser UI with URL, output type, quality, and editable time-range rows.
- [x] Separate clip output in a ZIP for multiple selected ranges.
- [x] Public metadata inspection and native social extractors with fallback.
- [x] Persistent download queue, stage progress, cancellation, and retry.
- [x] Source download followed by accurate local MP4/MP3 processing.
- [x] Output verification and publication only after successful processing.
- [x] Portable Windows tools and concise setup/development documentation.
- [x] Automated domain, extractor, API, job, process, and real-media tests.
- [x] Frontend time-input tests and GitHub Actions test workflow.

## Later improvements

Partial source downloads, optional fast keyframe cuts, authenticated sources, playlists, and distributed workers can be added as separate features. Public hosting requires authentication, user isolation, and enforced network egress controls before deployment.
