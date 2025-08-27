from pydantic import BaseModel
from typing import Optional

class ProcessedFileCreate(BaseModel):
    file_id: int
    folder_path: str
    algorithm: str = "3dgs"

class ProcessedFile(BaseModel):
    id: int
    file_id: int
    folder_path: str
    status: str
    result_url: Optional[str] = None
    algorithm: str = "3dgs"

    class Config:
        from_attributes = True