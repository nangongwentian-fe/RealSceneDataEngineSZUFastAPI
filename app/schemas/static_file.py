from pydantic import BaseModel

class StaticFileCreate(BaseModel):
    path: str
    filename: str

class StaticFile(BaseModel):
    id: int
    path: str
    filename: str

    class Config:
        from_attributes = True