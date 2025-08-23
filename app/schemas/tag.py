# app/schemas/tag.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class TagBase(BaseModel):
    name: str
    color: str
    description: Optional[str] = None

class TagCreate(TagBase):
    pass

class TagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None

class Tag(TagBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# 统一的响应格式
class BaseResponse(BaseModel):
    code: int = 200
    msg: str = "success"

class TagResponse(BaseResponse):
    data: Tag

class TagsResponse(BaseResponse):
    data: List[Tag]

class CreateTagResponse(BaseResponse):
    data: Tag

class UpdateTagResponse(BaseResponse):
    data: Tag

class DeleteTagResponse(BaseResponse):
    message: str

class AddTagToProjectResponse(BaseResponse):
    message: str

class RemoveTagFromProjectResponse(BaseResponse):
    message: str
