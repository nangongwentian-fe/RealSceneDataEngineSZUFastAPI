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
GAUSSIAN_SPLATTING_DIRECTORY = "/workspace/gaussian-splatting/"
GAUSTUDIO_DIRECTORY = "/workspace/gaustudio/"

# 创建线程池
thread_pool = ThreadPoolExecutor(max_workers=1)

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

    # 检查当前是否有正在运行的任务 (pending, imaged, converted)
    active_task_exists = db.query(ProcessedFileModel).filter(
        ProcessedFileModel.status.in_(["pending", "imaged", "converted"])
    ).first() is not None

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
        status="queued" if active_task_exists else "pending",
        result_url=None
    )
    db.add(new_processed_file)
    db.commit()
    db.refresh(new_processed_file)

    # 将 output_folder 转换为绝对路径
    absolute_output_folder = os.path.abspath(output_folder)

    # 构建输出模式
    output_pattern = os.path.join(absolute_output_folder, 'input', "%04d.jpg")

    # 如果没有其他活动任务，则启动当前任务
    if new_processed_file.status == "pending":
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
        # 任务结束后，检查是否有排队的任务并启动
        try:
            next_db_session = SessionLocal()
            queued_task = next_db_session.query(ProcessedFileModel).filter(
                ProcessedFileModel.status == "queued"
            ).order_by(ProcessedFileModel.id.asc()).first()

            if queued_task:
                print(f"找到排队任务: {queued_task.id}, 准备启动...")
                queued_task.status = "pending"
                next_db_session.commit()
                
                # 重新获取文件和路径信息，因为它们不在 queued_task 对象中，或者确保它们被正确传递
                # 这里假设我们可以从 queued_task.file_id 重新获取 static_file
                # 并且 queued_task.folder_path 已经是正确的绝对路径 (或者需要重新构建)
                # 这里的实现需要仔细核对原始 create_three_dgs 的逻辑
                static_file_for_queued_task = next_db_session.query(StaticFileModel).filter(StaticFileModel.id == queued_task.file_id).first()
                if static_file_for_queued_task:
                    # 确保 folder_path 是绝对路径，如果之前存的是相对路径，需要转换
                    # 从 create_three_dgs 逻辑看, folder_path 是类似 uploads/foldername
                    # run_task_in_thread 期望 absolute_output_folder
                    absolute_output_folder_for_queued_task = os.path.abspath(queued_task.folder_path)
                    output_pattern_for_queued_task = os.path.join(absolute_output_folder_for_queued_task, 'input', "%04d.jpg")

                    thread_pool.submit(
                        run_task_in_thread,
                        queued_task.id,
                        absolute_output_folder_for_queued_task,
                        static_file_for_queued_task.path, # 这是原始视频/文件的路径
                        output_pattern_for_queued_task
                    )
                    print(f"排队任务 {queued_task.id} 已提交执行。")
                else:
                    print(f"错误：无法为排队任务 {queued_task.id} 找到关联的 StaticFileModel。")
                    queued_task.status = "failed" # 或者其他错误状态
                    next_db_session.commit()
                    # 可能需要发送一个失败通知

        except Exception as e:
            print(f"检查并启动排队任务时出错: {str(e)}")
            print(f"错误堆栈: ", traceback.format_exc())
        finally:
            if 'next_db_session' in locals() and next_db_session:
                next_db_session.close()


@router.post("/threeDGS/toObj")
def to_obj(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    processed_file = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == project.processed_file_id).first()
    if not processed_file:
        raise HTTPException(status_code=404, detail="Processed file not found")

    # 定义结果目录和相机文件路径
    result_dir = os.path.join(processed_file.folder_path, "results")
    result_dir_abs = os.path.abspath(result_dir)
    
    # 检查 results 目录是否存在，如果不存在，可能是导入的项目
    if not os.path.exists(result_dir):
        # 对于导入的项目，直接使用处理文件的文件夹路径
        result_dir = processed_file.folder_path
        result_dir_abs = os.path.abspath(result_dir)
    
    # 尝试找到 cameras.json 文件
    # 首先检查 results 目录下
    result_camera_json = os.path.join(result_dir, "cameras.json")
    
    # 如果 results 目录下没有，则检查项目根目录
    if not os.path.exists(result_camera_json):
        root_camera_json = os.path.join(processed_file.folder_path, "cameras.json")
        if os.path.exists(root_camera_json):
            result_camera_json = root_camera_json
    
    result_camera_json_abs = os.path.abspath(result_camera_json)
    
    # 如果仍然找不到 cameras.json，抛出异常
    if not os.path.exists(result_camera_json_abs):
        raise HTTPException(status_code=404, detail="cameras.json not found in project")
    
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
    # 如果 cameras.json 在项目根目录，复制到 results 目录
    if os.path.exists(result_dir) and not os.path.exists(os.path.join(result_dir, "cameras.json")):
        src_cameras = os.path.join(processed_file.folder_path, "cameras.json")
        dest_cameras = os.path.join(result_dir_abs, "cameras.json")
        if os.path.exists(src_cameras):
            shutil.copy2(src_cameras, dest_cameras)
            result_camera_json_abs = os.path.abspath(dest_cameras)
    
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