from fastapi import Request
from typing import List, Dict, Any
import asyncio
import json

class SSEConnectionManager:
    def __init__(self):
        self.active_connections: List[Request] = []
        self.connection_queues: Dict[Request, asyncio.Queue] = {}
    
    async def connect(self, request: Request) -> asyncio.Queue:
        # 为每个连接创建一个消息队列
        queue = asyncio.Queue()
        self.active_connections.append(request)
        self.connection_queues[request] = queue
        return queue
    
    def disconnect(self, request: Request):
        # 断开连接时清理资源
        if request in self.active_connections:
            self.active_connections.remove(request)
        if request in self.connection_queues:
            del self.connection_queues[request]
    
    async def broadcast(self, message: Dict[str, Any]):
        # 向所有活跃连接广播消息
        for request, queue in list(self.connection_queues.items()):
            await queue.put(json.dumps(message))
    
    async def send_event(self, queue: asyncio.Queue, request: Request):
        try:
            # 发送事件格式
            yield "data: connected\n\n"
            
            while True:
                # 等待有消息放入队列
                message = await queue.get()
                yield f"data: {message}\n\n"
        except asyncio.CancelledError:
            # 当请求被取消时，清理连接
            self.disconnect(request)
            raise

# 创建全局实例
manager = SSEConnectionManager()