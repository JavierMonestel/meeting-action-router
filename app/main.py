"""Meeting Action Router — FastAPI app (HTML UI with HTMX + JSON API)."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from .connectors import CONNECTORS, DispatchResult, build_ics
from .extract_claude import ai_enabled
from .models import DESTINATION_LABEL, ActionItem, AnalysisResult
from .pipeline import analyze
from .transcript import SAMPLE_TITLES, TranscriptError, load_sample

logging.basicConfig(level=logging.INFO)

BASE = Path(__file__).parent
REPO_URL = "https://github.com/JavierMonestel/meeting-action-router"

app = FastAPI(
    title="Meeting Action Router",
    description=(
        "Turns meeting transcripts (e.g. Grain recordings) into owned, dated follow-ups and routes them to "
        "Linear, Attio, Notion and Calendar — with evidence for every item and a human review step."
    ),
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals.update(repo_url=REPO_URL, destination_label=DESTINATION_LABEL)

ItemList = TypeAdapter(list[ActionItem])


def routing_plan(result: AnalysisResult) -> list[dict]:
    """Each item plus the exact (redacted) request its connector would send."""
    plan = []
    for idx, item in enumerate(result.analysis.action_items):
        connector = CONNECTORS[item.destination]
        req = connector.build(item, result.transcript.title).redacted()
        body = req.body if isinstance(req.body, str) else json.dumps(req.body, indent=2, ensure_ascii=False)
        plan.append(
            {
                "idx": idx,
                "item": item,
                "connector": connector.label,
                "live": connector.configured(),
                "request": req,
                "request_body": body,
            }
        )
    return plan


# ---------------------------------------------------------------- HTML UI


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request, sample: str = "northfield-pilot-scoping"):
    if sample not in SAMPLE_TITLES:
        sample = "northfield-pilot-scoping"
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "samples": SAMPLE_TITLES,
            "active_sample": sample,
            "transcript": load_sample(sample),
            "ai": ai_enabled(),
            "connectors": CONNECTORS.values(),
        },
    )


@app.post("/analyze", response_class=HTMLResponse, include_in_schema=False)
def analyze_page(request: Request, transcript: Annotated[str, Form(max_length=60_000)]):
    try:
        result = analyze(transcript)
    except TranscriptError as error:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "samples": SAMPLE_TITLES,
                "active_sample": None,
                "transcript": transcript,
                "ai": ai_enabled(),
                "connectors": CONNECTORS.values(),
                "error": str(error),
            },
            status_code=422,
        )
    plan = routing_plan(result)
    by_dest = {d: [p for p in plan if p["item"].destination == d] for d in DESTINATION_LABEL}
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "result": result,
            "plan": plan,
            "by_dest": by_dest,
            "items_json": ItemList.dump_json(result.analysis.action_items).decode(),
        },
    )


@app.post("/dispatch", response_class=HTMLResponse, include_in_schema=False)
def dispatch_page(
    request: Request,
    items_json: Annotated[str, Form()],
    meeting_title: Annotated[str, Form()],
    approved: Annotated[list[int] | None, Form()] = None,
):
    try:
        items = ItemList.validate_json(items_json)
    except ValidationError as error:
        raise HTTPException(400, "Invalid items payload") from error
    results = dispatch([items[i] for i in (approved or []) if 0 <= i < len(items)], meeting_title)
    return templates.TemplateResponse(request, "_dispatch_results.html", {"results": results})


@app.post("/ics", include_in_schema=False)
def ics_download(
    items_json: Annotated[str, Form()],
    meeting_title: Annotated[str, Form()],
    ics_index: Annotated[int, Form()],
):
    try:
        item = ItemList.validate_json(items_json)[ics_index]
    except (ValidationError, IndexError) as error:
        raise HTTPException(400, "Invalid item") from error
    filename = "".join(c if c.isalnum() else "-" for c in item.title.lower())[:50].strip("-") or "invite"
    return Response(
        build_ics(item, meeting_title),
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{filename}.ics"'},
    )


@app.get("/evals", response_class=HTMLResponse, include_in_schema=False)
def evals_page(request: Request):
    from evals.run import run_eval

    report = run_eval(engine="rules")
    return templates.TemplateResponse(request, "evals.html", {"report": report})


# ---------------------------------------------------------------- JSON API


def dispatch(items: list[ActionItem], meeting_title: str) -> list[DispatchResult]:
    results = []
    for item in items:
        connector = CONNECTORS[item.destination]
        try:
            results.append(connector.send(item, meeting_title) if connector.configured() else connector.dry_run(item, meeting_title))
        except Exception as error:  # noqa: BLE001 - surface connector failures per item
            results.append(DispatchResult(destination=connector.name, title=item.title, status="error", detail=str(error)[:300]))
    return results


class AnalyzeRequest(BaseModel):
    transcript: str = Field(min_length=10, max_length=60_000, description="Plain-text transcript: '[00:01:02] Name: text' lines")
    meeting_date: date | None = Field(default=None, description="Used to resolve relative dates; defaults to today")


class AnalyzeResponse(AnalysisResult):
    routing: list[dict]


@app.post("/api/analyze", response_model=AnalyzeResponse, tags=["api"])
def api_analyze(body: AnalyzeRequest):
    """Extract summary, decisions, open questions and routed action items from a transcript."""
    try:
        result = analyze(body.transcript, body.meeting_date)
    except TranscriptError as error:
        raise HTTPException(422, str(error)) from error
    routing = [
        {"item_index": p["idx"], "destination": p["item"].destination, "live": p["live"], "request": p["request"].model_dump()}
        for p in routing_plan(result)
    ]
    return AnalyzeResponse(**result.model_dump(), routing=routing)


class GrainParticipant(BaseModel):
    name: str
    role: str | None = None
    organization: str | None = None


class GrainUtterance(BaseModel):
    start: str = Field(description="Timestamp, e.g. 00:04:05")
    speaker: str
    text: str


class GrainWebhook(BaseModel):
    """Shape modeled on a meeting-recorder 'recording ready' webhook."""

    title: str
    date: date
    participants: list[GrainParticipant]
    transcript: list[GrainUtterance]
    auto_dispatch: bool = Field(default=False, description="Send items immediately instead of returning a plan for review")


@app.post("/api/webhooks/grain", tags=["api"])
def grain_webhook(payload: GrainWebhook):
    """Webhook target for a meeting recorder: converts the payload, analyzes it, and (optionally) dispatches."""
    people = ", ".join(
        p.name + (f" ({p.role}{', ' + p.organization if p.organization else ''})" if p.role else "") for p in payload.participants
    )
    text = "\n".join(
        [f"Title: {payload.title}", f"Date: {payload.date.isoformat()}", f"Participants: {people}", ""]
        + [f"[{u.start}] {u.speaker}: {u.text}" for u in payload.transcript]
    )
    result = analyze(text, payload.date)
    response: dict = {"engine": result.engine, "analysis": result.analysis.model_dump(mode="json")}
    if payload.auto_dispatch:
        response["dispatch"] = [r.model_dump() for r in dispatch(result.analysis.action_items, payload.title)]
    return response


@app.get("/api/samples/{slug}", response_class=PlainTextResponse, tags=["api"])
def api_sample(slug: str):
    """Fictional sample transcripts used by the demo."""
    try:
        return load_sample(slug)
    except KeyError as error:
        raise HTTPException(404, "Unknown sample") from error


@app.get("/api/health", tags=["api"])
def health():
    return {
        "status": "ok",
        "engine": "claude" if ai_enabled() else "rules",
        "connectors": {name: ("live" if c.configured() else "dry-run") for name, c in CONNECTORS.items()},
    }
