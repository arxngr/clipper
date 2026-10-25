from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from threading import RLock

from app.domain.models import DownloadRequest


class InMemoryJobRepository:
    """Keep queue state for this server session without writing history to disk."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = RLock()

    def create(self, job_id: str, request: DownloadRequest) -> dict:
        job = {
            "id": job_id,
            "request": asdict(request),
            "title": "Preparing your download",
            "platform": "",
            "status": "queued",
            "stage": "Waiting in queue",
            "progress": 0,
            "error": None,
            "filename": None,
            "size": None,
            "duration": None,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._jobs[job_id] = job
            return deepcopy(job)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return deepcopy(self._jobs.get(job_id))

    def update(self, job_id: str, **changes) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(deepcopy(changes))
