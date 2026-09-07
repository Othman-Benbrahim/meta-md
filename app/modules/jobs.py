"""Registry de jobs longue duree pour l'API HTTP.

Le serveur MD-RAG est mono-processus (ThreadingHTTPServer). Les operations
qui prennent >5s (telechargement d'un lot de 300 PDFs, conversion Vision
de 300 documents) sont lancees en background : le client POST recoit
immediatement un `job_id`, puis poll `GET /api/rag/jobs/<id>` pour obtenir
la progression + le resultat final.

Structure d'un job :

    {
        "id": "8f1c...",
        "kind": "lot-download" | "conversion" | ...
        "state": "pending" | "running" | "done" | "error",
        "processed": 12,
        "total": 337,
        "current": "some-file.pdf",
        "started_at": 1_720_000_000.0,
        "finished_at": None,
        "result": None | dict,
        "error": None | str,
    }
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger("atelier.jobs")

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

# Nombre max de jobs terminaux conservees en memoire (purge FIFO).
MAX_JOBS = 50


def deja_en_cours(kind: str) -> str | None:
    """Id de la tache active portant exactement ce `kind`, s'il y en a une.

    Le garde-fou des conversions ne regardait que le disque : il refusait un
    document DEJA converti, pas un document EN COURS de conversion. Or
    l'identifiant du job ne vit que dans la page ; en revenant dessus, on ne
    voit plus rien tourner et on relance. Deux threads ecrivaient alors le
    meme fichier, en consommant deux fois le quota Albert.
    """
    with _LOCK:
        for job in _JOBS.values():
            if job["kind"] == kind and job["state"] in ("pending", "running"):
                return str(job["id"])
    return None


def create_job(kind: str) -> str:
    """Cree un job en etat `pending`. Retourne son id."""
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "state": "pending",
            "processed": 0,
            "total": 0,
            "current": None,
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
        }
        _purge_if_needed()
    return job_id


def update(job_id: str, **fields: Any) -> None:
    """Met a jour les champs d'un job existant. No-op si job inconnu."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(fields)


def get(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def active() -> list[dict[str, Any]]:
    """Instantane des taches qui ne doivent pas etre coupees par une MAJ."""
    with _LOCK:
        return [dict(job) for job in _JOBS.values()
                if job["state"] in ("pending", "running")]


def make_progress_callback(job_id: str) -> Callable[..., None]:
    """Genere un callback prone a etre passe aux fonctions longue duree.

    Signature : `cb(processed=int, total=int, current=str|None, **extra)`.
    """
    def _cb(processed: int | None = None, total: int | None = None,
            current: str | None = None, **extra: Any) -> None:
        fields: dict[str, Any] = {}
        if processed is not None:
            fields["processed"] = processed
        if total is not None:
            fields["total"] = total
        if current is not None:
            fields["current"] = current
        if extra:
            fields.update(extra)
        if fields:
            update(job_id, **fields)
    return _cb


def run_in_background(
    job_id: str,
    target: Callable[..., Any],
    args: tuple = (),
    kwargs: dict[str, Any] | None = None,
) -> threading.Thread:
    """Lance `target(*args, **kwargs)` dans un thread daemon, met a jour le
    job avec le resultat OU l'exception, puis flag `finished_at`.
    """
    kwargs = kwargs or {}

    def _runner() -> None:
        update(job_id, state="running")
        try:
            result = target(*args, **kwargs)
            update(job_id, state="done", result=result, finished_at=time.time())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s : exception", job_id)
            update(job_id, state="error", error=str(exc), finished_at=time.time())

    thread = threading.Thread(target=_runner, daemon=True, name=f"job-{job_id}")
    thread.start()
    return thread


def _purge_if_needed() -> None:
    """Purge FIFO des jobs termines les plus anciens si MAX_JOBS depasse.
    A appeler sous _LOCK."""
    if len(_JOBS) <= MAX_JOBS:
        return
    # On garde les jobs actifs + les MAX_JOBS - actifs les plus recents parmi
    # les termines.
    active_ids = [jid for jid, j in _JOBS.items() if j["state"] in ("pending", "running")]
    finished = sorted(
        [(jid, j) for jid, j in _JOBS.items() if j["state"] in ("done", "error")],
        key=lambda kv: kv[1].get("finished_at") or 0,
        reverse=True,
    )
    keep_finished = [jid for jid, _ in finished[: max(0, MAX_JOBS - len(active_ids))]]
    keep = set(active_ids) | set(keep_finished)
    for jid in list(_JOBS.keys()):
        if jid not in keep:
            _JOBS.pop(jid, None)
