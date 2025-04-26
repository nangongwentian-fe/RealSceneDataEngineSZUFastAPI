from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
import os
import ffmpeg
import subprocess
from fastapi.responses import FileResponse
from app.models.database import get_db, SessionLocal
from app.models.static_file import StaticFile as StaticFileModel
from app.models.processed_file import ProcessedFile as ProcessedFileModel
from app.schemas.processed_file import ProcessedFile
import traceback  # 添加这行
from concurrent.futures import ThreadPoolExecutor
import shutil
from app.models.project import Project as ProjectModel
import zipfile
from app.sse.connection_manager import manager

router = APIRouter()

UPLOAD_DIRECTORY = "uploads/"
GAUSSIAN_SPLATTING_DIRECTORY = "/workspace/gaussian-splatting"
GAUSTUDIO_DIRECTORY = "/workspace/gaustudio/"

# 创建线程池
thread_pool = ThreadPoolExecutor(max_workers=3)

# 确保上传目录存在
if not os.path.exists(UPLOAD_DIRECTORY):
    os.makedirs(UPLOAD_DIRECTORY)

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

    # 辅助函数：发送状态更新通知
    def send_status_update(db, task):
        # 查找与该处理文件关联的所有项目
        projects = db.query(ProjectModel).filter(ProjectModel.processed_file_id == task.id).all()
        project_ids = [project.id for project in projects]
        
        # 使用异步事件循环发送通知
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # 发送任务状态更新通知
            loop.run_until_complete(manager.broadcast({
                "type": "project_updated",
                "action": "status_changed",
                "task_id": task.id,
                "status": task.status,
                "project_ids": project_ids
            }))
        finally:
            loop.close()

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
            # 发送状态更新通知
            send_status_update(db, task)
        except Exception as e:
            print(f"FFmpeg处理失败: {str(e)}")
            task.status = "failed"
            db.commit()
            # 发送失败状态通知
            send_status_update(db, task)
            return

        # 2. 构建命令
        convert_command = f"python convert.py -s {absolute_output_folder}"
        train_command = f"python train.py -s {absolute_output_folder} --model_path {os.path.join(absolute_output_folder, 'results')}"

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
                # 发送失败状态通知
                send_status_update(db, task)
                return
            
            task.status = "converted"
            db.commit()
            # 发送状态更新通知
            send_status_update(db, task)
        except Exception as e:
            print(f"colmap命令执行错误: {str(e)}")
            task.status = "failed"
            db.commit()
            # 发送失败状态通知
            send_status_update(db, task)

        try:
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
            # 发送状态更新通知
            send_status_update(db, task)
        except Exception as e:
            print(f"train命令执行错误: {str(e)}")
            task.status = "failed"
            db.commit()
            # 发送失败状态通知
            send_status_update(db, task)
    except Exception as e:
        print(f"Process task 错误: {str(e)}")
        print(f"错误堆栈: ", traceback.format_exc())
        db = next(get_db_session())
        task = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == task_id).first()
        task.status = "failed"
        db.commit()
        # 发送失败状态通知
        send_status_update(db, task)
    finally:
        db.close()


@router.post("/threeDGS/toObj")
def to_obj(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    processed_file = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == project.processed_file_id).first()
    if not processed_file:
        raise HTTPException(status_code=404, detail="Processed file not found")

    result_dir = os.path.join(processed_file.folder_path, "results")
    result_dir_abs = os.path.abspath(result_dir)
    result_camera_json = os.path.join(result_dir, "cameras.json")
    result_camera_json_abs = os.path.abspath(result_camera_json)
    mesh_obj_dir = os.path.join(processed_file.folder_path, "mesh", "obj")
    mesh_obj_dir_abs = os.path.abspath(mesh_obj_dir)

    # 创建目标目录如果不存在
    os.makedirs(mesh_obj_dir_abs, exist_ok=True)

    # 定义ZIP文件名和路径
    zip_filename = f"{project.name}.zip"
    zip_filepath = os.path.join(mesh_obj_dir_abs, zip_filename)

    # 检查ZIP文件是否已存在
    if os.path.exists(zip_filepath):
        return FileResponse(zip_filepath, filename=zip_filename, media_type="application/octet-stream")

    # 确保 cameras.json 在 /results 下
    src_cameras = os.path.join(processed_file.folder_path, "cameras.json")
    dest_cameras = os.path.join(result_dir_abs, "cameras.json")
    if os.path.exists(src_cameras):
        shutil.copy2(src_cameras, dest_cameras)
    
    extract_cmd = f"gs-extract-mesh -m \"{result_dir_abs}\" -s \"{result_camera_json_abs}\" -o \"{mesh_obj_dir_abs}\""
    print(extract_cmd)
    subprocess.run(extract_cmd, shell=True, cwd=GAUSTUDIO_DIRECTORY)
    
    texrecon_cmd = (
        "texrecon ./images ./fused_mesh.ply ./textured_mesh "
        "--outlier_removal=gauss_clamping --data_term=area --no_intermediate_results"
    )
    subprocess.run(texrecon_cmd, shell=True, cwd=mesh_obj_dir_abs)
    
    # 创建ZIP文件
    with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(mesh_obj_dir_abs):
            for file in files:
                if file.startswith("textured_mesh"):
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, mesh_obj_dir_abs)
                    zipf.write(file_path, arcname)
    
    return FileResponse(zip_filepath, filename=zip_filename, media_type="application/octet-stream")

@router.post("/threeDGS/toUrdf/{project_id}")
def to_urdf(
    project_id: int,
    db: Session = Depends(get_db),
):
    project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    processed_file = (
        db.query(ProcessedFileModel)
        .filter(ProcessedFileModel.id == project.processed_file_id)
        .first()
    )
    if not processed_file:
        raise HTTPException(status_code=404, detail="Processed file not found")

    # 获取 obj 文件目录（与上面的 obj 处理类似）
    mesh_obj_dir = os.path.join(processed_file.folder_path, "mesh", "obj")
    mesh_obj_dir_abs = os.path.abspath(mesh_obj_dir)

    if not os.path.exists(mesh_obj_dir_abs):
        raise HTTPException(status_code=404, detail="OBJ directory not found")

    obj_files = [f for f in os.listdir(mesh_obj_dir_abs) if f.endswith(".obj")]

    if not obj_files:
        raise HTTPException(status_code=404, detail="No .obj files found")

    # 假设返回第一个找到的 .obj 文件
    obj_file = obj_files[0]
    obj_file_path = os.path.join(mesh_obj_dir_abs, obj_file)

    # 将 obj 转换成 urdf 文件
    urdf_file = os.path.join(mesh_obj_dir_abs, f"{project.name}.urdf")

    try:
        # 调用 obj 转 urdf 转换函数
        convert_obj_to_urdf(obj_file_path, urdf_file)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error converting to URDF: {str(e)}")

    return FileResponse(
        urdf_file, filename=f"{project.name}.urdf", media_type="application/octet-stream"
    )