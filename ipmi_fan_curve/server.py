"""FastAPI web server — serves the UI and exposes API routes."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ipmi_fan_curve import config
from ipmi_fan_curve.models import CurvesPayload, FanNamesPayload, FanOverridesPayload

# Backend module — set in __main__ before uvicorn starts
backend = None

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# ── App lifecycle ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(backend.start())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/sensors")
async def api_sensors():
    sensors = backend.get_cached_sensors()
    temps = [s for s in sensors if s["unit"] == "C"]
    fans = [s for s in sensors if s["unit"] == "RPM"]
    return {"temps": temps, "fans": fans}

@app.get("/api/curves")
async def api_curves():
    return {"curves": [c.model_dump() for c in config.load_curves()]}

@app.post("/api/curves")
async def api_save_curves(payload: CurvesPayload):
    for c in payload.curves:
        c.points = sorted(c.points, key=lambda p: p.temp)
    config.save_curves(payload.curves)
    return {"ok": True}

@app.get("/api/fan-names")
async def api_fan_names():
    return {"names": config.load_fan_names()}

@app.post("/api/fan-names")
async def api_save_fan_names(payload: FanNamesPayload):
    cleaned = {k: v for k, v in payload.names.items() if v.strip()}
    config.save_fan_names(cleaned)
    return {"ok": True}

@app.get("/metrics")
async def metrics():
    return Response(content=backend.metrics_output(), media_type=backend.METRICS_CONTENT_TYPE)

@app.get("/api/fan-overrides")
async def api_fan_overrides():
    return {"overrides": config.load_fan_overrides()}

@app.post("/api/fan-overrides")
async def api_save_fan_overrides(payload: FanOverridesPayload):
    cleaned = {k: max(0, min(100, v)) for k, v in payload.overrides.items()}
    config.save_fan_overrides(cleaned)
    return {"ok": True}

@app.delete("/api/fan-overrides/{fan_id}")
async def api_delete_fan_override(fan_id: str):
    overrides = config.load_fan_overrides()
    overrides.pop(fan_id, None)
    config.save_fan_overrides(overrides)
    return {"ok": True}

@app.delete("/api/curves/{curve_id}")
async def api_delete_curve(curve_id: str):
    curves = [c for c in config.load_curves() if c.id != curve_id]
    config.save_curves(curves)
    return {"ok": True}
