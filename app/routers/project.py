# app/routers/project.py
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from typing import Optional
from sqlalchemy.orm import Session
import os
import shutil
import zipfile
import tempfile
import uuid
import datetime
from app.models.database import get_db
from app.models.project import Project as ProjectModel
from app.models.static_file import StaticFile as StaticFileModel
from app.models.processed_file import ProcessedFile as ProcessedFileModel
from app.schemas.project import ProjectCreate, Project, ProjectImport
from app.routers.three_d_gs import create_three_dgs
from app.sse.connection_manager import manager

router = APIRouter()

@router.post("/projects/add", response_model=Project)
async def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    # 检查 static_file 是否存在
    static_file = db.query(StaticFileModel).filter(StaticFileModel.id == project.static_file_id).first()
    if not static_file:
        raise HTTPException(status_code=502, detail="Static file not found")
    
    # 检查 project_cover_image_static_id 是否存在
    cover_image = db.query(StaticFileModel).filter(StaticFileModel.id == project.project_cover_image_static_id).first()
    if not cover_image:
        # 如果 cover_image 不存在，则使用 static_file 的第一个文件作为封面
        raise HTTPException(status_code=502, detail="Cover image static file not found")

    # 执行 create_three_dgs 并获取 processed_file_id
    processed_file = await create_three_dgs(file_id=project.static_file_id, db=db)
    processed_file_id = processed_file.id

    # 创建项目
    new_project = ProjectModel(
        name=project.name,
        processed_file_id=processed_file_id,
        static_file_id=project.static_file_id,
        project_cover_image_static_id=project.project_cover_image_static_id
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    # 发送通知
    await manager.broadcast({
        "type": "project_updated",
        "action": "create",
        "project_id": new_project.id
    })

    return new_project

@router.get("/projects/list")
def list_projects(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页数量"),
    tag_id: Optional[int] = Query(default=None, description="标签ID筛选"),
    db: Session = Depends(get_db)
):
    query = db.query(ProjectModel)
    
    # 如果指定了标签ID，则筛选包含该标签的项目
    if tag_id is not None:
        from app.models.tag import Tag as TagModel
        query = query.join(ProjectModel.tags).filter(TagModel.id == tag_id)
    
    # 获取项目总数
    total = query.count()
    
    # 计算跳过的记录数
    skip = (page - 1) * page_size
    projects = query.offset(skip).limit(page_size).all()

    # 构建返回结果
    result = []
    for project in projects:
        static_file = project.static_file
        processed_file = project.processed_file
        cover_image = project.cover_image

        result.append({
            "id": project.id,
            "name": project.name,
            "processed_file": {
                "id": processed_file.id if processed_file else None,
                "file_id": processed_file.file_id if processed_file else None,
                "folder_path": processed_file.folder_path if processed_file else None,
                "status": processed_file.status if processed_file else None,
                "result_url": processed_file.result_url if processed_file else None
            } if processed_file else {},
            "static_file": {
                "id": static_file.id if static_file else None,
                "path": static_file.path if static_file else None,
                "filename": static_file.filename if static_file else None,
                "original_filename": static_file.original_filename if static_file else None,
            } if static_file else {},
            "cover_image": {
                "id": cover_image.id if cover_image else None,
                "path": cover_image.path if cover_image else None,
                "filename": cover_image.filename if cover_image else None,
                "original_filename": cover_image.original_filename if cover_image else None
            } if cover_image else {},
            "tags": [
                {
                    "id": tag.id,
                    "name": tag.name,
                    "color": tag.color
                } for tag in project.tags
            ]
        })

    # 计算总页数
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return {
        "code": 200,
        "data": result,
        "pagination": {
            "total": total,           # 总项目数
            "page": page,             # 当前页码
            "page_size": page_size,   # 每页数量
            "total_pages": total_pages, # 总页数
            "has_next": page < total_pages,  # 是否有下一页
            "has_prev": page > 1      # 是否有上一页
        },
        "msg": "请求成功"
    }

@router.get("/projects/count")
def get_project_count(db: Session = Depends(get_db)):
    """
    获取项目总数统计
    
    返回:
    - total: 项目总数
    - msg: 响应消息
    """
    total = db.query(ProjectModel).count()
    return {
        "code": 200,
        "total": total,
        "msg": "获取项目总数成功"
    }

@router.get("/projects/statistics")
def get_project_statistics(db: Session = Depends(get_db)):
    """
    获取项目详细统计信息
    
    返回:
    - total: 项目总数
    - status_stats: 按处理状态统计的项目数量
    - msg: 响应消息
    """
    # 获取项目总数
    total = db.query(ProjectModel).count()
    
    # 按处理状态统计
    status_stats = {}
    if total > 0:
        # 获取所有项目的处理状态
        projects_with_status = db.query(ProjectModel, ProcessedFileModel.status).join(
            ProcessedFileModel, ProjectModel.processed_file_id == ProcessedFileModel.id
        ).all()
        
        # 统计各状态数量
        for _, status in projects_with_status:
            if status:
                status_stats[status] = status_stats.get(status, 0) + 1
    
    return {
        "code": 200,
        "total": total,
        "status_stats": status_stats,
        "msg": "获取项目统计信息成功"
    }

@router.delete("/projects/{project_id}", response_model=bool)
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 删除项目
    db.delete(project)
    db.commit()
    await manager.broadcast({
        "type": "project_updated",
        "action": "delete",
        "project_id": project_id
    })
    return True


@router.post("/projects/import", response_model=Project)
async def import_project(
    name: str = Form(...),
    root_dir: str = Form(...),
    cover_image: UploadFile = File(...),
    zip_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    导入3DGS项目
    
    参数:
    - name: 项目名称
    - root_dir: 压缩包中符合项目结构要求的文件夹名称
    - cover_image: 项目封面图
    - zip_file: 项目压缩包
    
    返回:
    - 创建的项目信息
    """
    # 生成唯一标识符
    unique_id = str(uuid.uuid4())[:8]
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    unique_prefix = f"{timestamp}_{unique_id}"
    
    # 1. 保存封面图
    cover_image_ext = os.path.splitext(cover_image.filename)[1]
    cover_image_filename = f"{unique_prefix}_cover{cover_image_ext}"
    cover_image_path = os.path.join("uploads", cover_image_filename)
    
    # 确保上传目录存在
    os.makedirs("uploads", exist_ok=True)
    
    # 保存封面图文件
    with open(cover_image_path, "wb") as f:
        shutil.copyfileobj(cover_image.file, f)
    
    # 创建封面图的静态文件记录
    cover_image_static = StaticFileModel(
        path=cover_image_path,
        filename=cover_image_filename,
        original_filename=cover_image.filename
    )
    db.add(cover_image_static)
    db.flush()
    
    # 2. 保存并解压ZIP文件
    zip_ext = os.path.splitext(zip_file.filename)[1]
    zip_filename = f"{unique_prefix}_project{zip_ext}"
    zip_path = os.path.join("uploads", zip_filename)
    
    # 保存ZIP文件
    with open(zip_path, "wb") as f:
        shutil.copyfileobj(zip_file.file, f)
    
    # 创建ZIP文件的静态文件记录
    zip_static = StaticFileModel(
        path=zip_path,
        filename=zip_filename,
        original_filename=zip_file.filename
    )
    db.add(zip_static)
    db.flush()
    
    # 3. 解压并验证项目结构
    extract_dir = os.path.join("uploads", f"{unique_prefix}_extracted")
    os.makedirs(extract_dir, exist_ok=True)
    
    # 解压ZIP文件
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
    
    # 验证项目结构
    project_dir = os.path.join(extract_dir, root_dir)
    cameras_json_path = os.path.join(project_dir, "cameras.json")
    point_cloud_dir = os.path.join(project_dir, "point_cloud")
    iteration_30000_dir = os.path.join(point_cloud_dir, "iteration_30000")
    point_cloud_ply = os.path.join(iteration_30000_dir, "point_cloud.ply")
    
    # 检查必要的文件和目录是否存在
    if not os.path.exists(cameras_json_path):
        raise HTTPException(status_code=400, detail="项目结构无效：缺少 cameras.json 文件")
    
    if not os.path.exists(point_cloud_dir):
        raise HTTPException(status_code=400, detail="项目结构无效：缺少 point_cloud 目录")
    
    if not os.path.exists(iteration_30000_dir):
        raise HTTPException(status_code=400, detail="项目结构无效：缺少 iteration_30000 目录")
    
    if not os.path.exists(point_cloud_ply):
        raise HTTPException(status_code=400, detail="项目结构无效：缺少 point_cloud.ply 文件")
    
    # 构建相对路径的 result_url，用于前端访问
    relative_ply_path = os.path.join(
        os.path.basename(extract_dir),
        root_dir,
        "point_cloud",
        "iteration_30000",
        "point_cloud.ply"
    )
    
    # 4. 创建处理文件记录
    processed_file = ProcessedFileModel(
        file_id=zip_static.id,
        folder_path=project_dir,
        status="trained",  # 已经训练完成的项目
        result_url=relative_ply_path  # 设置正确的 PLY 文件路径
    )
    db.add(processed_file)
    db.flush()
    
    # 5. 创建项目
    new_project = ProjectModel(
        name=name,
        processed_file_id=processed_file.id,
        static_file_id=zip_static.id,
        project_cover_image_static_id=cover_image_static.id
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    
    # 6. 发送通知
    await manager.broadcast({
        "type": "project_updated",
        "action": "create",
        "project_id": new_project.id
    })
    
    return new_project