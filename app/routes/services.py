"""Service API routes — /api/services CRUD and dashboard."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ServiceCheck
from app.schemas import ServiceCheckCreate

router = APIRouter(prefix="/api")


@router.get("/services")
async def list_services(db: AsyncSession = Depends(get_db)) -> list[dict[str, object]]:
    from app.main import merge_service_payload
    services = (await db.scalars(select(ServiceCheck).order_by(ServiceCheck.service_type, ServiceCheck.name, ServiceCheck.id))).all()
    return [merge_service_payload(service) for service in services]


@router.get("/dashboard/services")
async def dashboard_services(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import merge_service_payload, summarize_service_statuses
    services = (await db.scalars(select(ServiceCheck).order_by(ServiceCheck.service_type, ServiceCheck.name, ServiceCheck.id))).all()
    payload = [merge_service_payload(service) for service in services]
    return {
        "summary": summarize_service_statuses(payload),
        "services": payload,
    }


@router.post("/services", status_code=status.HTTP_201_CREATED)
async def create_service(service_data: ServiceCheckCreate, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    from app.main import check_service, merge_service_payload, service_status_cache
    service = ServiceCheck(**service_data.model_dump())
    db.add(service)
    await db.commit()
    await db.refresh(service)
    service_status_cache[service.id] = await check_service(service)
    return merge_service_payload(service)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(service_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    from app.main import service_status_cache
    service = await db.get(ServiceCheck, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service check not found")
    await db.delete(service)
    await db.commit()
    service_status_cache.pop(service_id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
