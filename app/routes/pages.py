"""HTML page routes — serves Jinja2 templates for the SMP frontend."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OperationalMapView

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="main_dashboard.html",
        context={"page_title": "Seeker Management Platform"},
    )


@router.get("/nodes/dashboard", response_class=HTMLResponse)
async def node_dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"page_title": "Node Dashboard | Seeker Management Platform"},
    )


@router.get("/services/dashboard", response_class=HTMLResponse)
async def services_dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="services_dashboard.html",
        context={"page_title": "Services Dashboard | Seeker Management Platform"},
    )


@router.get("/topology", response_class=HTMLResponse)
async def topology_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="topology.html",
        context={"page_title": "Division C2 Information Network | Seeker Management Platform", "map_view_id": None},
    )


@router.get("/topology/maps/{map_view_id}", response_class=HTMLResponse)
async def topology_submap_page(request: Request, map_view_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    view = db.get(OperationalMapView, map_view_id)
    if not view:
        raise HTTPException(status_code=404, detail="Map view not found")
    page_title = f"{view.name} | Seeker Management Platform"
    return templates.TemplateResponse(
        request=request,
        name="topology.html",
        context={"page_title": page_title, "map_view_id": map_view_id, "map_view_name": view.name},
    )


@router.get("/discovery", response_class=HTMLResponse)
async def discovery_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="discovery.html",
        context={"page_title": "Network Discovery | Seeker Management Platform"},
    )


@router.get("/nodes", response_class=HTMLResponse)
async def nodes_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="nodes.html",
        context={"page_title": "Node Inventory | Seeker Management Platform"},
    )


@router.get("/services", response_class=HTMLResponse)
async def services_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="services.html",
        context={"page_title": "Services | Seeker Management Platform"},
    )


@router.get("/nodes/{node_id}", response_class=HTMLResponse)
async def node_detail_page(node_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    from app.main import get_node_or_404
    node = get_node_or_404(node_id, db)
    embedded = request.query_params.get("embedded") == "1"
    return templates.TemplateResponse(
        request=request,
        name="node_detail.html",
        context={
            "page_title": f"{node.name} | Anchor Node Detail",
            "node_id": node.id,
            "node_name": node.name,
            "detail_endpoint": f"/api/nodes/{node.id}/detail",
            "detail_kind": "anchor",
            "embedded": embedded,
        },
    )


@router.get("/node/{node_id}", response_class=HTMLResponse)
async def node_detail_alias(node_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return await node_detail_page(node_id, request, db)


@router.get("/nodes/discovered/{site_id}", response_class=HTMLResponse)
async def discovered_node_detail_page(site_id: str, request: Request) -> HTMLResponse:
    from app.main import node_dashboard_backend
    cached = node_dashboard_backend.get_cached_discovered_node(site_id) or {}
    node_name = str(cached.get("site_name") or f"Discovered Site {site_id}")
    embedded = request.query_params.get("embedded") == "1"
    return templates.TemplateResponse(
        request=request,
        name="node_detail.html",
        context={
            "page_title": f"{node_name} | Discovered Node Detail",
            "node_id": site_id,
            "node_name": node_name,
            "detail_endpoint": f"/api/discovered-nodes/{site_id}/detail",
            "detail_kind": "discovered",
            "embedded": embedded,
        },
    )
