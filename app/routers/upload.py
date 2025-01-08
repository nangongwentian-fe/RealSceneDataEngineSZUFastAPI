from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Query
from sqlalchemy.orm import Session
import os
import ffmpeg
import subprocess
from fastapi.responses import FileResponse
from app.models.database import get_db, SessionLocal
from app.models.static_file import StaticFile as StaticFileModel
from app.models.processed_file import ProcessedFile as ProcessedFileModel
from app.schemas.static_file import StaticFile
from app.schemas.processed_file import ProcessedFile
import traceback  # 添加这行
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import uuid
from typing import List

router = APIRouter()

UPLOAD_DIRECTORY = "uploads/"
GAUSSIAN_SPLATTING_DIRECTORY = "E:/Code/Python/SZU/gaussian-splatting"

# 创建线程池
thread_pool = ThreadPoolExecutor(max_workers=3)

# 确保上传目录存在
if not os.path.exists(UPLOAD_DIRECTORY):
    os.makedirs(UPLOAD_DIRECTORY)






@router.post("/upload/", response_model=StaticFile)
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)): 
    # 获取原始文件扩展名
    file_extension = Path(file.filename).suffix
    # 生成唯一文件名
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    # 构建文件保存路径
    file_location = os.path.join(UPLOAD_DIRECTORY, unique_filename)
    
    # 保存文件
    with open(file_location, "wb") as f:
        f.write(file.file.read())
    
    # 保存文件信息到数据库，保存原始文件名和新文件名
    static_file = StaticFileModel(
        path=file_location, 
        filename=unique_filename,
        original_filename=file.filename  # 需要在数据库模型中添加此字段
    )
    db.add(static_file)
    db.commit()
    db.refresh(static_file)
    
    return static_file



@router.get("/files/list", response_model=List[StaticFile])
async def list_files(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db)
):
    """
    获取已上传文件列表
    - page: 页码，从1开始
    - page_size: 每页数量，最大100条
    """
    # 计算跳过的记录数
    skip = (page - 1) * page_size
    files = db.query(StaticFileModel).offset(skip).limit(page_size).all()
    return files


@router.get("/files/{file_path:path}")
async def get_file(file_path: str):
    # 构建完整文件路径
    file_location = Path(UPLOAD_DIRECTORY) / file_path
    
    try:
        # 规范化路径
        file_location = file_location.resolve()
        upload_dir = Path(UPLOAD_DIRECTORY).resolve()
        
        # 安全检查：确保请求的文件在上传目录内
        if not str(file_location).startswith(str(upload_dir)):
            raise HTTPException(status_code=403, detail="Access denied")
            
        # 检查文件是否存在
        if not file_location.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        
        # 获取文件名    
        filename = file_location.name
        
        # 返回文件响应，设置为下载模式
        return FileResponse(
            str(file_location),
            filename=filename,  # 指定下载时的文件名
            media_type="application/octet-stream"  # 强制浏览器下载而不是显示
        )
        
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Error accessing file: {str(e)}")





@router.get("/threeDGS/status/{task_id}")
async def get_task_status(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task.id, "status": task.status, "result_url": task.result_url}








def clean_failed_task_results(folder_path: str):
    """清理失败任务的结果文件"""
    try:
        results_path = os.path.join(folder_path, "results")
        if os.path.exists(results_path):
            shutil.rmtree(results_path)
    except Exception as e:
        print(f"清理失败任务结果时出错: {str(e)}")

@router.post("/threeDGS/createThreeDGS", response_model=ProcessedFile)
async def create_three_dgs(file_id: int, db: Session = Depends(get_db)):
    # 获取文件信息
    static_file = db.query(StaticFileModel).filter(StaticFileModel.id == file_id).first()
    if not static_file:
        raise HTTPException(status_code=404, detail="File not found")

    # 检查文件关联的所有任务
    processed_files = db.query(ProcessedFileModel).filter(ProcessedFileModel.file_id == file_id).all()
    
    # 检查是否有已完成的任务
    completed_task = next((task for task in processed_files if task.status == "trained"), None)
    if completed_task:
        return completed_task

    # 检查是否有正在处理的任务
    running_task = next((task for task in processed_files if task.status not in ["failed", "trained"]), None)
    if running_task:
        return running_task

    # 清理失败任务的结果并删除失败任务记录
    for failed_task in processed_files:
        if failed_task.status == "failed":
            clean_failed_task_results(failed_task.folder_path)
            
    db.commit()

    # 创建新任务
    folder_name = os.path.splitext(os.path.basename(static_file.path))[0]
    output_folder = os.path.join(UPLOAD_DIRECTORY, folder_name)
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    if not os.path.exists(os.path.join(output_folder, 'input')):
        os.makedirs(os.path.join(output_folder, 'input'))

    # 存储处理结果
    new_processed_file = ProcessedFileModel(
        file_id=file_id, 
        folder_path=output_folder, 
        status="pending", 
        result_url=None
    )
    db.add(new_processed_file)
    db.commit()
    db.refresh(new_processed_file)

    # 将 output_folder 转换为绝对路径
    absolute_output_folder = os.path.abspath(output_folder)

    # 构建输出模式
    output_pattern = os.path.join(absolute_output_folder, 'input', "%04d.jpg")

    # 在线程池中执行任务
    thread_pool.submit(
        run_task_in_thread, 
        new_processed_file.id, 
        absolute_output_folder, 
        static_file.path,
        output_pattern
    )
    
    return new_processed_file

def run_task_in_thread(task_id: int, absolute_output_folder: str, input_video_path: str, output_pattern: str):
    def get_db_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    try:
        db = next(get_db_session())
        task = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == task_id).first()
        
        # 1. FFmpeg处理视频
        try:
            ffmpeg.input(input_video_path).output(
                output_pattern, 
                qscale=1, 
                qmin=1, 
                vf='fps=2'
            ).run()
            task.status = "imaged"
            db.commit()
        except Exception as e:
            print(f"FFmpeg处理失败: {str(e)}")
            task.status = "failed"
            db.commit()
            return

        # 2. 构建命令
        conda_env = "gaussian_splatting"
        convert_command = f"conda run -n {conda_env} python convert.py -s {absolute_output_folder}"
        train_command = f"conda run -n {conda_env} python train.py -s {absolute_output_folder} --model_path {os.path.join(absolute_output_folder, 'results')}"

        # 3. 执行转换命令
        try:
            convert_result = subprocess.run(
                convert_command,
                shell=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=GAUSSIAN_SPLATTING_DIRECTORY
            )
            
            if convert_result.returncode != 0:
                task.status = "failed"
                db.commit()
                return
            
            task.status = "converted"
            db.commit()

            # 4. 执行训练命令
            train_result = subprocess.run(
                train_command,
                shell=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=GAUSSIAN_SPLATTING_DIRECTORY
            )
            
            if train_result.returncode != 0:
                task.status = "failed"
            else:
                task.status = "trained"
                folder_name = os.path.basename(absolute_output_folder)
                task.result_url = f"{folder_name}/results/point_cloud/iteration_30000/point_cloud.ply"
            db.commit()
            
        except Exception as e:
            print(f"命令执行错误: {str(e)}")
            task.status = "failed"
            db.commit()
            
    except Exception as e:
        print(f"Process task 错误: {str(e)}")
        print(f"错误堆栈: ", traceback.format_exc())
        db = next(get_db_session())
        task = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == task_id).first()
        task.status = "failed"
        db.commit()
    finally:
        db.close()