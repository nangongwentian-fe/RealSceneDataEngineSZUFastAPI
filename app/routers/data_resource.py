from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.data_resource import DataResource as DataResourceModel
from app.models.static_file import StaticFile as StaticFileModel
from app.schemas.data_resource import DataResourceCreate, DataResource
import os
import subprocess

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

    # 创建视频预览帧
    try:
        # 获取视频文件路径
        video_path = static_file.path
        
        # 检查文件是否存在
        if not os.path.exists(video_path):
            print(f"视频文件不存在: {video_path}")
            return new_data_resource
        
        # 获取视频文件名(不带扩展名)
        video_basename = os.path.splitext(os.path.basename(video_path))[0]
        
        # 创建预览文件夹
        preview_folder = os.path.join("uploads", f"{video_basename}-video-preview")
        os.makedirs(preview_folder, exist_ok=True)
        
        # 获取视频总时长
        duration_cmd = [
            "ffprobe", 
            "-v", "error", 
            "-show_entries", "format=duration", 
            "-of", "default=noprint_wrappers=1:nokey=1", 
            video_path
        ]
        duration = float(subprocess.check_output(duration_cmd).decode("utf-8").strip())
        
        # 明确只抽取4帧
        num_frames = 5
        # 修正计算逻辑，确保均匀分布包括起始和结束位置
        frame_times = [duration * i / num_frames for i in range(num_frames)] if num_frames > 1 else [0]
        
        # 存储预览帧的ID列表
        preview_frame_ids = []
        
        # 抽取并保存每一帧
        for i, time_point in enumerate(frame_times):
            output_file = os.path.join(preview_folder, f"frame_{i+1}.jpg")
            
            cmd = [
                "ffmpeg", 
                "-ss", str(time_point), 
                "-i", video_path, 
                "-frames:v", "1", 
                "-q:v", "2", 
                output_file,
                "-y"  # 覆盖已存在的文件
            ]
            
            subprocess.run(cmd, check=True)
            
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                # 将帧图片添加到static_files表
                frame_filename = f"{video_basename}-video-preview/frame_{i+1}.jpg"
                new_static_file = StaticFileModel(
                    path=output_file,
                    filename=frame_filename,
                    original_filename=f"frame_{i+1}.jpg"
                )
                db.add(new_static_file)
                db.flush()  # 刷新会话以获取ID
                preview_frame_ids.append(str(new_static_file.id))
            
        # 循环结束后更新数据资源
        new_data_resource.preview_frame_ids = ",".join(preview_frame_ids)
        db.commit()
            
        print(f"成功抽取视频预览帧到: {preview_folder}")
        
    except Exception as e:
        print(f"抽取视频预览帧时出错: {str(e)}")
        # 这里我们不中断API请求，即使预览帧提取失败

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
    
    # 删除预览帧static_file记录
    if data_resource.preview_frame_ids:
        preview_frame_ids = [int(id) for id in data_resource.preview_frame_ids.split(',')]
        preview_frames = db.query(StaticFileModel).filter(
            StaticFileModel.id.in_(preview_frame_ids)
        ).all()
        
        for frame in preview_frames:
            # 删除预览帧文件
            if os.path.exists(frame.path):
                try:
                    os.remove(frame.path)
                except Exception as e:
                    print(f"删除预览帧文件失败: {frame.path}, 错误: {str(e)}")
            
            # 删除预览帧记录
            db.delete(frame)
    
    # 删除 data_resource
    db.delete(data_resource)
    
    if static_file:
        # 删除 uploads 文件夹中的静态文件
        if os.path.exists(static_file.path):
            os.remove(static_file.path)
        
        # 删除 static_file
        db.delete(static_file)
    
    # 尝试删除预览文件夹
    if static_file:
        video_basename = os.path.splitext(os.path.basename(static_file.path))[0]
        preview_folder = os.path.join("uploads", f"{video_basename}-video-preview")
        if os.path.exists(preview_folder) and os.path.isdir(preview_folder):
            try:
                os.rmdir(preview_folder)
            except Exception as e:
                print(f"删除预览文件夹失败: {preview_folder}, 错误: {str(e)}")
    
    db.commit()
    return True

@router.get("/data_resources/{data_id}/preview-images")
def get_data_resource_preview_images(data_id: int, db: Session = Depends(get_db)):
    # 查找数据资源
    data_resource = db.query(DataResourceModel).filter(DataResourceModel.id == data_id).first()
    if data_resource is None:
        raise HTTPException(status_code=404, detail="数据资源不存在")
    
    # 如果没有预览帧ID列表，返回空列表
    if not data_resource.preview_frame_ids:
        return []
    
    # 从预览帧ID列表中获取静态文件
    preview_frame_ids = [int(id) for id in data_resource.preview_frame_ids.split(',')]
    preview_frames = db.query(StaticFileModel).filter(
        StaticFileModel.id.in_(preview_frame_ids)
    ).all()
    
    # 转换为响应格式
    image_files = [
        {
            "id": frame.id,
            "filename": frame.filename,
            "path": frame.path,
            "original_filename": frame.original_filename
        }
        for frame in preview_frames
    ]
    
    return image_files