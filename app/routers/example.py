from fastapi import APIRouter
from app.schemas.example import Example

router = APIRouter()

@router.get("/example")
def example_endpoint():
    return {"message": "This is an example endpoint"}

@router.post("/schemasExample")
def schemas_example(data: Example):
    return {"example_name": data.name, "example_price": data.price}