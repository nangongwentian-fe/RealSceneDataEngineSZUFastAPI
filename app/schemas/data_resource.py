from pydantic import BaseModel

class DataResourceCreate(BaseModel):
    name: str
    static_file_id: int

class DataResource(BaseModel):
    id: int
    name: str
    static_file_id: int

    class Config:
        from_attributes = True