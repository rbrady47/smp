"""System routes — /api/status."""

import socket
from datetime import datetime

from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/status")
async def status_view() -> dict[str, str]:
    return {
        "app": "Seeker Management Platform",
        "version": "0.1.0",
        "hostname": socket.gethostname(),
        "time": datetime.now().isoformat(),
    }
