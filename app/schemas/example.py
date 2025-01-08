from pydantic import BaseModel

class Example(BaseModel):
    name: str
    description: str = None
    price: float
    tax: float = None
