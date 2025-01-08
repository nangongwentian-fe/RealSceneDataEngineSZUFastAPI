# app/routers/project.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.project import Project as ProjectModel
from app.models.static_file import StaticFile as StaticFileModel
from app.schemas.project import ProjectCreate, Project
from app.routers.upload import create_three_dgs
from app.models.processed_file import ProcessedFile as ProcessedFileModel

router = APIRouter()

@router.post("/projects/add", response_model=Project)
async def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    # 检查 static_file 是否存在
    static_file = db.query(StaticFileModel).filter(StaticFileModel.id == project.static_file_id).first()
    if not static_file:
        raise HTTPException(status_code=404, detail="Static file not found")

    # 执行 create_three_dgs 并获取 processed_file_id
    processed_file = await create_three_dgs(file_id=project.static_file_id, db=db)
    processed_file_id = processed_file.id

    # 创建项目
    new_project = ProjectModel(
        name=project.name,
        processed_file_id=processed_file_id,
        static_file_id=project.static_file_id
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return new_project

@router.get("/projects/list")
def list_projects(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db)
):
    # 计算跳过的记录数
    skip = (page - 1) * page_size
    projects = db.query(ProjectModel).offset(skip).limit(page_size).all()

    # 构建返回结果
    result = []
    for project in projects:
        static_file = db.query(StaticFileModel).filter(StaticFileModel.id == project.static_file_id).first()
        processed_file = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == project.processed_file_id).first()

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
                "original_filename": static_file.original_filename if static_file else None
            } if static_file else {}
        })

    return {
        "code": 200,
        "data": result,
        "msg": "请求成功"
    }

@router.delete("/projects/{project_id}", response_model=bool)
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 删除项目
    db.delete(project)
    db.commit()
    return True