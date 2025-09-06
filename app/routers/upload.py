from fastapi import APIRouter, File, UploadFile, HTTPException, Depends
from sqlalchemy.orm import Session
import os
from fastapi.responses import FileResponse
from app.models.database import get_db
from app.models.static_file import StaticFile as StaticFileModel
from app.schemas.static_file import StaticFile
from pathlib import Path
import uuid
import aiofiles  # 新增: 异步文件操作库

router = APIRouter()

UPLOAD_DIRECTORY = "uploads/"

# 确保上传目录存在
if not os.path.exists(UPLOAD_DIRECTORY):
    os.makedirs(UPLOAD_DIRECTORY)

@router.post("/upload/", response_model=StaticFile)
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)): 
    # 获取原始文件扩展名
    file_extension = Path(file.filename).suffix
    # 生成唯一文件名
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    # 构建文件保存路径
    file_location = os.path.join(UPLOAD_DIRECTORY, unique_filename)
    
    # 以异步方式分块保存文件，避免一次性读入大文件导致内存暴涨
    try:
        async with aiofiles.open(file_location, "wb") as f:
            chunk_size = 1024 * 1024  # 1MB
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                await f.write(chunk)
    except Exception as err:
        # 写文件失败时，返回 500 并中断后续数据库操作
        raise HTTPException(status_code=500, detail=f"Failed to save file: {err}")
    
    # 保存文件信息到数据库，保存原始文件名和新文件名
    static_file = StaticFileModel(
        path=file_location, 
        filename=unique_filename,
        original_filename=file.filename  # 需要在数据库模型中添加此字段
    )
    db.add(static_file)
    db.commit()
    db.refresh(static_file)
    
    return static_file

@router.get("/files/{file_path:path}")
async def get_file(file_path: str):
    # 构建完整文件路径
    file_location = Path(UPLOAD_DIRECTORY) / file_path
    
    try:
        # 规范化路径
        file_location = file_location.resolve()
        upload_dir = Path(UPLOAD_DIRECTORY).resolve()
        
        # 安全检查：确保请求的文件在上传目录内
        if not str(file_location).startswith(str(upload_dir)):
            raise HTTPException(status_code=403, detail="Access denied")
            
        # 检查文件是否存在
        if not file_location.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        
        # 获取文件名    
        filename = file_location.name
        
        # 返回文件响应，设置为下载模式
        return FileResponse(
            str(file_location),
            filename=filename,  # 指定下载时的文件名
            media_type="application/octet-stream"  # 强制浏览器下载而不是显示
        )
        
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Error accessing file: {str(e)}")
