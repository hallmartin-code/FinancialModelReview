"""FastAPI front end: upload a deck, get both one-pagers.

A run makes one or two Claude calls over a whole deck and can take minutes, so
uploads are handled as background jobs: the browser is redirected to a job page
that polls until the artifacts are ready. Artifacts live in a per-job temporary
directory and are swept after ``JOB_TTL_SECONDS``.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from deckscan.config import load_config
from deckscan.extract.claude import api_key_present
from deckscan.models import canonical_json
from deckscan.pipeline import (
    DECK_SUFFIXES,
    MODEL_SUFFIXES,
    AnalysisRequest,
    render_outputs,
    run_analysis,
)

load_dotenv()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "3600"))
MAX_WORKERS = int(os.environ.get("WEB_MAX_WORKERS", "2"))

STAGE_READ = "Reading the deck"
STAGE_RULES = "Running the rule engine"
STAGE_RENDER = "Rendering the one-pagers"

ERROR_HEADINGS = {
    400: "That deck can't be processed",
    401: "Not authorized",
    404: "Nothing here",
    413: "That file is too large",
    503: "Service not configured",
}

TEMPLATE_DIR = Path(__file__).parent / "web_templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

app = FastAPI(title="deckscan", docs_url=None, redoc_url=None)
# Icons and other assets. Deliberately outside the password gate: a browser
# fetches the favicon before the user has authenticated.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="deck")
_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_basic = HTTPBasic(auto_error=False)


def require_access(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    """Gate the app behind HTTP Basic when APP_PASSWORD is set; open when it isn't.

    The deployment is open by default. Setting APP_PASSWORD is the one-variable
    switch to close it — worth doing before sharing the URL, since every upload
    spends API credit.
    """
    password = os.environ.get("APP_PASSWORD", "")
    if not password:
        return
    supplied = credentials.password if credentials else ""
    if not secrets.compare_digest(supplied, password):
        raise HTTPException(
            status_code=401,
            detail="Not authorized",
            headers={"WWW-Authenticate": 'Basic realm="deckscan"'},
        )


@dataclass
class Job:
    """One run: its inputs, its progress, and the artifacts it produced."""

    id: str
    deck_name: str
    model_name: str | None
    workdir: Path
    created_at: float = field(default_factory=time.monotonic)
    status: str = "queued"  # queued | running | done | error
    stage: str = "Queued"
    error: str | None = None
    warning: str | None = None
    """Set when the run completed but the source could not actually be read, so
    the PDFs are a report of gaps rather than a reading of the deck."""
    company: str = ""
    score: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    gaps: int = 0
    screen_path: Path | None = None
    narrative_path: Path | None = None
    json_path: Path | None = None

    def steps(self) -> list[dict[str, str]]:
        labels = [STAGE_READ, STAGE_RULES, STAGE_RENDER]
        if self.status == "done":
            return [{"label": label, "state": "done"} for label in labels]
        current = labels.index(self.stage) if self.stage in labels else -1
        out: list[dict[str, str]] = []
        for index, label in enumerate(labels):
            if current < 0:
                state = "pending"
            elif index < current:
                state = "done"
            elif index > current:
                state = "pending"
            else:
                state = "failed" if self.status == "error" else "active"
            out.append({"label": label, "state": state})
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "deck": self.deck_name,
            "model": self.model_name,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "warning": self.warning,
            "company": self.company,
            "score": self.score,
            "counts": self.counts,
            "gaps": self.gaps,
            "screen": f"/jobs/{self.id}/download/screen" if self.screen_path else None,
            "onepager": f"/jobs/{self.id}/download/onepager" if self.narrative_path else None,
            "json": f"/jobs/{self.id}/download/json" if self.json_path else None,
        }


def _sweep_expired() -> None:
    cutoff = time.monotonic() - JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [job for job in _jobs.values() if job.created_at < cutoff]
        for job in stale:
            _jobs.pop(job.id, None)
    for job in stale:
        shutil.rmtree(job.workdir, ignore_errors=True)


def _get_job(job_id: str) -> Job:
    _sweep_expired()
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return job


def _run_job(job: Job, deck_path: Path, model_path: Path | None) -> None:
    """Execute one run end to end. Runs on a worker thread; never raises."""
    try:
        settings = load_config()
        job.status, job.stage = "running", STAGE_READ
        analysis = run_analysis(
            AnalysisRequest(
                deck_path=deck_path,
                model_path=model_path,
                company_override=None,
                ocr_mode="auto",
                settings=settings,
            )
        )

        # The pipeline never fails on deck content — an unreadable source becomes a
        # gap. That is right for the report and wrong for the UI, which would
        # otherwise present an empty result as a clean one.
        if any(gap.field == "extraction_failure" for gap in analysis.gaps):
            job.warning = next(
                (note for note in analysis.methodology if note.startswith("Extraction failed")),
                "The deck could not be read, so this report is gaps only.",
            )

        job.stage = STAGE_RULES
        job.company = analysis.company or "Company"
        job.score = analysis.grounding_score
        job.counts = analysis.counts_by_severity()
        job.gaps = len(analysis.gaps)

        job.stage = STAGE_RENDER
        stem = "".join(c for c in job.company if c.isalnum() or c in " -_").strip() or "company"
        stem = "_".join(stem.split())
        screen = job.workdir / f"{stem}_screen.pdf"
        narrative = job.workdir / f"{stem}_one_pager.pdf"
        sources = [job.deck_name] + ([job.model_name] if job.model_name else [])
        render_outputs(analysis, settings, screen, narrative, sources)
        job.screen_path, job.narrative_path = screen, narrative

        payload = job.workdir / f"{stem}_analysis.json"
        payload.write_text(canonical_json(analysis), encoding="utf-8")
        job.json_path = payload

        job.stage, job.status = "Complete", "done"
    except Exception as exc:  # a failed run must still explain itself
        job.status, job.error = "error", f"{type(exc).__name__}: {exc}"


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Browsers ask for this path by name even when a <link> points elsewhere."""
    return FileResponse(STATIC_DIR / "favicon.png", media_type="image/png")


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness probe for Railway. Needs no auth and no API key."""
    return JSONResponse({"status": "ok", "api_key_configured": api_key_present()})


@app.exception_handler(StarletteHTTPException)
async def html_error_handler(request: Request, exc: StarletteHTTPException) -> Any:
    """Render errors in the site's design for browsers, as JSON for the API."""
    headers = getattr(exc, "headers", None)
    wants_html = "text/html" in request.headers.get("accept", "")
    if exc.status_code == 401 or not wants_html or request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exc.status_code,
            "heading": ERROR_HEADINGS.get(exc.status_code, "Something went wrong"),
            "detail": exc.detail,
        },
        status_code=exc.status_code,
        headers=headers,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: None = Depends(require_access)) -> Any:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "api_key_missing": not api_key_present(),
            "deck_extensions": sorted(DECK_SUFFIXES),
            "model_extensions": sorted(MODEL_SUFFIXES),
            "deck_accept": ",".join(sorted(DECK_SUFFIXES)),
            "model_accept": ",".join(sorted(MODEL_SUFFIXES)),
            "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "ttl_minutes": max(1, JOB_TTL_SECONDS // 60),
        },
    )


@app.post("/jobs")
async def create_job(
    deck: UploadFile = File(...),
    model: UploadFile | None = File(None),
    _: None = Depends(require_access),
) -> RedirectResponse:
    if not api_key_present():
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured.")

    deck_suffix = Path(deck.filename or "").suffix.lower()
    if deck_suffix not in DECK_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported deck type '{deck_suffix or deck.filename}'. "
            f"Supported: {', '.join(sorted(DECK_SUFFIXES))}",
        )
    payload = await deck.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded deck is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Deck exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB upload limit.",
        )

    model_bytes: bytes | None = None
    model_suffix = ""
    if model is not None and model.filename:
        model_suffix = Path(model.filename).suffix.lower()
        if model_suffix not in MODEL_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported model type '{model_suffix}'. "
                f"Supported: {', '.join(sorted(MODEL_SUFFIXES))}",
            )
        model_bytes = await model.read()
        if len(model_bytes or b"") > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="The model file is too large.")

    _sweep_expired()
    job_id = uuid.uuid4().hex
    workdir = Path(tempfile.mkdtemp(prefix=f"deckscan-{job_id[:8]}-"))
    deck_path = workdir / f"deck{deck_suffix}"
    deck_path.write_bytes(payload)

    model_path: Path | None = None
    if model_bytes:
        model_path = workdir / f"model{model_suffix}"
        model_path.write_bytes(model_bytes)

    job = Job(
        id=job_id,
        deck_name=deck.filename or f"deck{deck_suffix}",
        model_name=(model.filename if model and model.filename else None),
        workdir=workdir,
    )
    with _jobs_lock:
        _jobs[job_id] = job
    _executor.submit(_run_job, job, deck_path, model_path)

    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str, _: None = Depends(require_access)) -> Any:
    job = _get_job(job_id)
    return templates.TemplateResponse(
        request,
        "job.html",
        {"job": job, "ttl_minutes": max(1, JOB_TTL_SECONDS // 60)},
    )


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, _: None = Depends(require_access)) -> JSONResponse:
    return JSONResponse(_get_job(job_id).as_dict())


@app.get("/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str, _: None = Depends(require_access)) -> FileResponse:
    job = _get_job(job_id)
    path = {
        "screen": job.screen_path,
        "onepager": job.narrative_path,
        "json": job.json_path,
    }.get(kind)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="That artifact is not available.")
    media = "application/json" if kind == "json" else "application/pdf"
    return FileResponse(path, media_type=media, filename=path.name)
