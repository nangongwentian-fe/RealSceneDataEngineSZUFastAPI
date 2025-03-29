# app/schemas/project.py
from pydantic import BaseModel

class ProjectCreate(BaseModel):
    name: str
    static_file_id: int
    project_cover_image_static_id: int

class Project(BaseModel):
    id: int
    name: str
    processed_file_id: int
    static_file_id: int
    project_cover_image_static_id: int

    class Config:
        from_attributes = True