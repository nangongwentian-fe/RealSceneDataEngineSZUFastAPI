from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.data_resource import DataResource as DataResourceModel
from app.models.static_file import StaticFile as StaticFileModel
from app.schemas.data_resource import DataResourceCreate, DataResource
import os

router = APIRouter()

@router.post("/data_resources/add", response_model=DataResource)
def create_data_resource(data_resource: DataResourceCreate, db: Session = Depends(get_db)):
    # 检查 static_file 是否存在
    static_file = db.query(StaticFileModel).filter(StaticFileModel.id == data_resource.static_file_id).first()
    if not static_file:
        raise HTTPException(status_code=404, detail="Static file not found")

    # 创建数据资源
    new_data_resource = DataResourceModel(name=data_resource.name, static_file_id=data_resource.static_file_id)
    db.add(new_data_resource)
    db.commit()
    db.refresh(new_data_resource)
    return new_data_resource

@router.get("/data_resources/list")
def list_data_resources(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db)
):
    # 计算跳过的记录数
    skip = (page - 1) * page_size
    data_resources = db.query(DataResourceModel).offset(skip).limit(page_size).all()

    # 构建返回结果
    result = []
    for data_resource in data_resources:
        static_file = db.query(StaticFileModel).filter(StaticFileModel.id == data_resource.static_file_id).first()
        result.append({
            "id": data_resource.id,
            "name": data_resource.name,
            "static_file": {
                "id": static_file.id,
                "path": static_file.path,
                "filename": static_file.filename,
                "original_filename": static_file.original_filename
            }
        })

    return {
        "code": 200,
        "data": result,
        "msg": "请求成功"
    }

@router.get("/data_resources/listAll")
def list_all_data_resources(db: Session = Depends(get_db)):
    data_resources = db.query(DataResourceModel).all()

    # 构建返回结果
    result = []
    for data_resource in data_resources:
        static_file = db.query(StaticFileModel).filter(StaticFileModel.id == data_resource.static_file_id).first()
        result.append({
            "id": data_resource.id,
            "name": data_resource.name,
            "static_file": {
                "id": static_file.id,
                "path": static_file.path,
                "filename": static_file.filename,
                "original_filename": static_file.original_filename
            }
        })

    return {
        "code": 200,
        "data": result,
        "msg": "请求成功"
    }


@router.delete("/data_resources/{data_id}", response_model=bool)
def delete_data_resource(data_id: int, db: Session = Depends(get_db)):
    data_resource = db.query(DataResourceModel).filter(DataResourceModel.id == data_id).first()
    if data_resource is None:
        raise HTTPException(status_code=404, detail="Data resource not found")
    
    # 获取关联的 static_file
    static_file = db.query(StaticFileModel).filter(StaticFileModel.id == data_resource.static_file_id).first()
    
    # 删除 data_resource
    db.delete(data_resource)
    
    if static_file:
        # 删除 uploads 文件夹中的静态文件
        if os.path.exists(static_file.path):
            os.remove(static_file.path)
        
        # 删除 static_file
        db.delete(static_file)
    
    db.commit()
    return True