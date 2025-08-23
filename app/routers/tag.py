# app/routers/tag.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.models.database import get_db
from app.models.tag import Tag as TagModel
from app.models.project import Project as ProjectModel
from app.schemas.tag import TagCreate, TagUpdate, Tag, TagsResponse, CreateTagResponse, UpdateTagResponse, DeleteTagResponse, AddTagToProjectResponse, RemoveTagFromProjectResponse

# 添加请求体模型
class AddTagRequest(BaseModel):
    tag_id: int

router = APIRouter()

@router.get("/tags", response_model=TagsResponse)
def get_tags(db: Session = Depends(get_db)):
    """获取所有标签"""
    tags = db.query(TagModel).all()
    return TagsResponse(data=tags)

@router.post("/tags", response_model=CreateTagResponse)
def create_tag(tag: TagCreate, db: Session = Depends(get_db)):
    """创建新标签"""
    # 检查标签名是否已存在
    existing_tag = db.query(TagModel).filter(TagModel.name == tag.name).first()
    if existing_tag:
        raise HTTPException(status_code=400, detail="Tag name already exists")
    
    new_tag = TagModel(
        name=tag.name, 
        color=tag.color, 
        description=tag.description
    )
    db.add(new_tag)
    db.commit()
    db.refresh(new_tag)
    
    return CreateTagResponse(data=new_tag)

@router.put("/tags/{tag_id}", response_model=UpdateTagResponse)
def update_tag(tag_id: int, tag: TagUpdate, db: Session = Depends(get_db)):
    """更新标签"""
    db_tag = db.query(TagModel).filter(TagModel.id == tag_id).first()
    if not db_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    if tag.name is not None:
        # 检查新名称是否与其他标签冲突
        existing_tag = db.query(TagModel).filter(TagModel.name == tag.name, TagModel.id != tag_id).first()
        if existing_tag:
            raise HTTPException(status_code=400, detail="Tag name already exists")
        db_tag.name = tag.name
    
    if tag.color is not None:
        db_tag.color = tag.color
    
    if tag.description is not None:
        db_tag.description = tag.description
    
    db.commit()
    db.refresh(db_tag)
    
    return UpdateTagResponse(data=db_tag)

@router.delete("/tags/{tag_id}", response_model=DeleteTagResponse)
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    """删除标签"""
    db_tag = db.query(TagModel).filter(TagModel.id == tag_id).first()
    if not db_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    db.delete(db_tag)
    db.commit()
    
    return DeleteTagResponse(message="Tag deleted successfully")

@router.post("/projects/{project_id}/tags", response_model=AddTagToProjectResponse)
def add_tag_to_project(project_id: int, request: AddTagRequest, db: Session = Depends(get_db)):
    """为项目添加标签"""
    project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    tag = db.query(TagModel).filter(TagModel.id == request.tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # 检查标签是否已经添加到项目
    if tag in project.tags:
        raise HTTPException(status_code=400, detail="Tag already added to project")
    
    project.tags.append(tag)
    db.commit()
    
    return AddTagToProjectResponse(message="Tag added to project successfully")

@router.delete("/projects/{project_id}/tags/{tag_id}", response_model=RemoveTagFromProjectResponse)
def remove_tag_from_project(project_id: int, tag_id: int, db: Session = Depends(get_db)):
    """从项目移除标签"""
    project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    tag = db.query(TagModel).filter(TagModel.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    if tag not in project.tags:
        raise HTTPException(status_code=400, detail="Tag not found in project")
    
    project.tags.remove(tag)
    db.commit()
    
    return RemoveTagFromProjectResponse(message="Tag removed from project successfully")
