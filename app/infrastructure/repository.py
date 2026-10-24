import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.domain.models import DownloadRequest


class SQLiteJobRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=15)

    @staticmethod
    def _decode(payload: str) -> dict:
        job = json.loads(payload)
        request = job["request"]
        if "ranges" not in request:
            start, end = request.pop("start", 0), request.pop("end", None)
            request["ranges"] = (
                [{"start": start, "end": end}] if start or end is not None else []
            )
        return job

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
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?)", (job_id, json.dumps(job))
            )
        return job

    def get(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._decode(row[0]) if row else None

    def list(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
        return [self._decode(row[0]) for row in rows]

    def update(self, job_id: str, **changes) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row:
                job = self._decode(row[0])
                job.update(changes)
                connection.execute(
                    "UPDATE jobs SET payload = ? WHERE id = ?",
                    (json.dumps(job), job_id),
                )

    def interrupt_unfinished(self) -> None:
        with self._connect() as connection:
            rows = connection.execute("SELECT id, payload FROM jobs").fetchall()
            for job_id, payload in rows:
                job = self._decode(payload)
                if job["status"] not in {"completed", "failed", "cancelled"}:
                    job.update(
                        status="failed",
                        stage="Interrupted",
                        error="The app stopped before this job finished. Please try again.",
                    )
                    connection.execute(
                        "UPDATE jobs SET payload = ? WHERE id = ?",
                        (json.dumps(job), job_id),
                    )
