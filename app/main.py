from datetime import datetime
import socket

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# FastAPI app setup for the prototype SMP shell.
app = FastAPI(title="Seeker Management Platform", version="0.1.0")

# Template rendering is configured so route handlers can return HTML pages.
templates = Jinja2Templates(directory="templates")

# Static file mounting allows CSS and JavaScript files to be served by FastAPI.
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    # Route definition for the homepage that renders the main UI shell template.
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"page_title": "Seeker Management Platform"},
    )


@app.get("/api/status")
async def status() -> dict[str, str]:
    # Route definition for a simple status payload used by the prototype shell.
    return {
        "app": "Seeker Management Platform",
        "version": "0.1.0",
        "hostname": socket.gethostname(),
        "time": datetime.now().isoformat(),
    }
