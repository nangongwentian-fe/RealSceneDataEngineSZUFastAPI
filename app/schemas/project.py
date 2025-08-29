# app/schemas/project.py
from pydantic import BaseModel
from fastapi import UploadFile, Form
from typing import Optional, List
from .tag import Tag

class ProjectCreate(BaseModel):
    name: str
    static_file_id: int
    project_cover_image_static_id: int
    algorithm: str = "3dgs"

class ProjectImport(BaseModel):
    name: str
    root_dir: str

class Project(BaseModel):
    id: int
    name: str
    processed_file_id: int
    static_file_id: int
    project_cover_image_static_id: int
    tags: List[Tag] = []  # 新增：标签列表

    class Config:
        from_attributes = True

class ProjectListResponse(BaseModel):
    data: List[Project]
    pagination: dict