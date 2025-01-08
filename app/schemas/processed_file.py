from pydantic import BaseModel
from typing import Optional

class ProcessedFileCreate(BaseModel):
    file_id: int
    folder_path: str

class ProcessedFile(BaseModel):
    id: int
    file_id: int
    folder_path: str
    status: str
    result_url: Optional[str] = None

    class Config:
        from_attributes = True