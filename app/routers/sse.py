from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from app.sse.connection_manager import manager

router = APIRouter()

@router.get("/sse/projects")
async def sse_projects(request: Request):
    """建立 SSE 连接，以接收项目更新通知"""
    queue = await manager.connect(request)
    
    return StreamingResponse(
        manager.send_event(queue, request),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'  # Nginx 特殊设置
        }
    )