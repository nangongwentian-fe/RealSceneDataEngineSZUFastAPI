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
from threading import Event, Lock
import uuid
import datetime
from typing import Optional, Dict, List
import signal

router = APIRouter()

UPLOAD_DIRECTORY = "uploads/"
GAUSSIAN_SPLATTING_DIRECTORY = "/workspace/gaussian-splatting/"
GAUSTUDIO_DIRECTORY = "/workspace/gaustudio/"

# 任务取消标志字典
task_cancel_events = {}

# 创建线程池
thread_pool = ThreadPoolExecutor(max_workers=1)

# 任务运行中的子进程记录（用于快速终止）
# 注意：子进程以新的会话启动（start_new_session=True），便于通过进程组一次性杀死孙子进程
task_processes: Dict[int, List[subprocess.Popen]] = {}
task_proc_lock = Lock()


def _register_process(task_id: int, process: subprocess.Popen) -> None:
    with task_proc_lock:
        task_processes.setdefault(task_id, []).append(process)


def _unregister_process(task_id: int, process: subprocess.Popen) -> None:
    with task_proc_lock:
        processes = task_processes.get(task_id)
        if processes and process in processes:
            processes.remove(process)
            if not processes:
                task_processes.pop(task_id, None)


def _terminate_task_processes(task_id: int, grace_seconds: float = 2.0) -> None:
    """向任务的进程组发送终止信号，尽快结束正在进行的阶段。

    先发 SIGTERM 给予优雅退出时间，随后用 SIGKILL 强制终止。
    """
    with task_proc_lock:
        procs = list(task_processes.get(task_id, []))
    if not procs:
        return
    # 先尝试 SIGTERM 到整个进程组
    for p in procs:
        try:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        except Exception:
            pass
    # 等待一个很短的宽限期
    try:
        import time
        time.sleep(max(0.0, min(grace_seconds, 5.0)))
    except Exception:
        pass
    # 仍未退出则 SIGKILL
    for p in procs:
        try:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass

# 确保上传目录存在
if not os.path.exists(UPLOAD_DIRECTORY):
    os.makedirs(UPLOAD_DIRECTORY)


def _find_latest_point_cloud_ply(absolute_output_folder: str) -> Optional[str]:
    """在输出目录内查找最新一次迭代的 point_cloud.ply，并返回相对 uploads 的 URL 路径。

    返回值示例："<folder_name>/results/point_cloud/iteration_30000/point_cloud.ply"
    若找不到则返回 None。
    """
    try:
        folder_name = os.path.basename(absolute_output_folder.rstrip(os.sep))
        # 候选基础目录：优先 results，其次根目录（兼容不同算法的 model_path 约定）
        base_candidates = [
            os.path.join(absolute_output_folder, "results"),
            absolute_output_folder,
        ]
        for base_dir in base_candidates:
            point_cloud_root = os.path.join(base_dir, "point_cloud")
            if not os.path.isdir(point_cloud_root):
                continue
            # 查找 iteration_* 目录，取最新（数值最大的迭代）
            iterations = []
            for name in os.listdir(point_cloud_root):
                if name.startswith("iteration_"):
                    try:
                        iter_id = int(name.split("iteration_")[-1])
                        iterations.append((iter_id, name))
                    except Exception:
                        continue
            if not iterations:
                continue
            iterations.sort(key=lambda x: x[0], reverse=True)
            latest_dir = iterations[0][1]
            ply_path_abs = os.path.join(point_cloud_root, latest_dir, "point_cloud.ply")
            if os.path.isfile(ply_path_abs):
                # 构造相对 uploads 的 URL
                rel_from_output = os.path.relpath(ply_path_abs, start=os.path.join(absolute_output_folder))
                return f"{folder_name}/{rel_from_output}"
        return None
    except Exception:
        return None

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
async def create_three_dgs(file_id: int, algorithm: str = "3dgs", db: Session = Depends(get_db)):
    # 获取文件信息
    static_file = db.query(StaticFileModel).filter(StaticFileModel.id == file_id).first()
    if not static_file:
        raise HTTPException(status_code=404, detail="File not found")
    # 检查文件关联的所有任务
    processed_files = db.query(ProcessedFileModel).filter(ProcessedFileModel.file_id == file_id).all()
    # 检查是否有已完成的任务
    completed_task = next((task for task in processed_files if task.status == "trained" and task.algorithm == algorithm), None)
    if completed_task:
        return completed_task
    # 检查是否有正在处理的任务
    running_task = next((task for task in processed_files if task.status not in ["failed", "trained"] and task.algorithm == algorithm), None)
    if running_task:
        return running_task
    # 清理失败任务的结果并删除失败任务记录
    for failed_task in processed_files:
        if failed_task.status == "failed" and failed_task.algorithm == algorithm:
            clean_failed_task_results(failed_task.folder_path)
    db.commit()
    # 检查当前是否有正在运行的任务 (pending, imaged, converted)
    active_task_exists = db.query(ProcessedFileModel).filter(
        ProcessedFileModel.status.in_(["pending", "imaged", "converted"]),
        ProcessedFileModel.algorithm == algorithm
    ).first() is not None
    # 创建新任务 - 修改目录命名逻辑，确保唯一性
    base_folder_name = os.path.splitext(os.path.basename(static_file.path))[0]
    # 生成唯一标识符
    unique_id = str(uuid.uuid4())[:8]
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    # 新的目录命名格式：原文件名_算法_时间戳_唯一ID
    folder_name = f"{base_folder_name}_{algorithm}_{timestamp}_{unique_id}"
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
        result_url=None,
        algorithm=algorithm
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
            output_pattern,
            algorithm
        )
    return new_processed_file


def run_task_in_thread(task_id: int, absolute_output_folder: str, input_video_path: str, output_pattern: str, algorithm: str = "3dgs"):
    def get_db_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
    def send_status_update(db, task):
        projects = db.query(ProjectModel).filter(ProjectModel.processed_file_id == task.id).all()
        project_ids = [project.id for project in projects]
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(manager.broadcast({
                "type": "project_updated",
                "action": "status_changed",
                "task_id": task.id,
                "status": task.status,
                "project_ids": project_ids
            }))
        finally:
            loop.close()
    # 创建/获取取消事件
    cancel_event = task_cancel_events.setdefault(task_id, Event())
    try:
        db = next(get_db_session())
        task = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == task_id).first()
        # 1. FFmpeg处理视频（可中断）
        try:
            if cancel_event.is_set() or (task and task.status == "failed"):
                print(f"任务{task_id}已被取消，终止执行。")
                return
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                input_video_path,
                "-qscale",
                "1",
                "-qmin",
                "1",
                "-vf",
                "fps=2",
                output_pattern,
            ]
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
            _register_process(task_id, ffmpeg_proc)
            ffmpeg_stdout, ffmpeg_stderr = ffmpeg_proc.communicate()
            _unregister_process(task_id, ffmpeg_proc)
            if cancel_event.is_set() or (task and task.status == "failed"):
                print(f"任务{task_id}已被取消，终止执行。")
                return
            if ffmpeg_proc.returncode != 0:
                print(f"[threeDGS] ffmpeg failed rc={ffmpeg_proc.returncode}")
                if ffmpeg_stderr:
                    print(f"[threeDGS] ffmpeg stderr (tail):\n{ffmpeg_stderr[-2000:]}\n--- end stderr ---")
                if ffmpeg_stdout:
                    print(f"[threeDGS] ffmpeg stdout (tail):\n{ffmpeg_stdout[-2000:]}\n--- end stdout ---")
                task.status = "failed"
                db.commit()
                send_status_update(db, task)
                return
            task.status = "imaged"
            db.commit()
            send_status_update(db, task)
        except Exception as e:
            print(f"FFmpeg处理失败: {str(e)}")
            task.status = "failed"
            db.commit()
            send_status_update(db, task)
            return
        # 2. 构建命令和目录
        if algorithm == "3dgs":
            work_dir = GAUSSIAN_SPLATTING_DIRECTORY
            convert_py = os.path.join(work_dir, 'convert.py')
            train_py = os.path.join(work_dir, 'train.py')
            convert_command = f"python {convert_py} -s {absolute_output_folder}" if os.path.exists(convert_py) else None
            train_command = f"python {train_py} -s {absolute_output_folder} --model_path {os.path.join(absolute_output_folder, 'results')}"
        elif algorithm == "lp-3dgs":
            work_dir = "/workspace/LP-3DGS/"
            convert_py = os.path.join(work_dir, 'convert.py')
            train_py = os.path.join(work_dir, 'train.py')
            convert_command = f"python {convert_py} -s {absolute_output_folder}" if os.path.exists(convert_py) else None
            train_command = f"python {train_py} -s {absolute_output_folder} --model_path {os.path.join(absolute_output_folder, 'results')} --prune_method rad_splat"
        elif algorithm == "gaussianpro":
            work_dir = "/workspace/GaussianPro/"
            work_dir_3dgs = GAUSSIAN_SPLATTING_DIRECTORY
            convert_py = os.path.join(work_dir_3dgs, 'convert.py')
            train_py = os.path.join(work_dir, 'train.py')
            convert_command = f"python {convert_py} -s {absolute_output_folder}" if os.path.exists(convert_py) else None
            train_command = f"python {train_py} -s {absolute_output_folder} --model_path {os.path.join(absolute_output_folder, 'results')}"
        elif algorithm == "dashgaussian":
            work_dir = "/workspace/DashGaussian/"
            convert_py = os.path.join(work_dir, 'convert.py')
            train_dash_py = os.path.join(work_dir, 'train_dash.py')
            train_fallback_py = os.path.join(work_dir, 'train.py')
            convert_command = f"python {convert_py} -s {absolute_output_folder}" if os.path.exists(convert_py) else None
            if os.path.exists(train_dash_py):
                train_command = f"python {train_dash_py} -s {absolute_output_folder} --model_path {os.path.join(absolute_output_folder, 'results')} --disable_viewer"
            else:
                train_command = f"python {train_fallback_py} -s {absolute_output_folder} --model_path {os.path.join(absolute_output_folder, 'results')} --dash --disable_viewer"
        else:
            print(f"未知算法类型: {algorithm}")
            task.status = "failed"
            db.commit()
            send_status_update(db, task)
            return
        # 3. 执行转换命令（如有）
        run_cwd = work_dir if os.path.isdir(work_dir) else None
        if convert_command:
            try:
                # 特殊处理：GaussianPro 的 convert 需要 mask 目录
                if algorithm == "gaussianpro":
                    os.makedirs(os.path.join(absolute_output_folder, "mask"), exist_ok=True)
                if cancel_event.is_set() or (task and task.status == "failed"):
                    print(f"任务{task_id}已被取消，终止执行。")
                    return
                convert_proc = subprocess.Popen(
                    convert_command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    cwd=run_cwd,
                    start_new_session=True,
                )
                _register_process(task_id, convert_proc)
                convert_stdout, convert_stderr = convert_proc.communicate()
                _unregister_process(task_id, convert_proc)
                if cancel_event.is_set() or (task and task.status == "failed"):
                    print(f"任务{task_id}已被取消，终止执行。")
                    return
                if convert_proc.returncode != 0:
                    print(f"[threeDGS] convert failed (algorithm={algorithm}) rc={convert_result.returncode}")
                    if convert_stderr:
                        print(f"[threeDGS] convert stderr (tail):\n{convert_stderr[-2000:]}\n--- end stderr ---")
                    if convert_stdout:
                        print(f"[threeDGS] convert stdout (tail):\n{convert_stdout[-2000:]}\n--- end stdout ---")
                    task.status = "failed"
                    db.commit()
                    send_status_update(db, task)
                    return
                task.status = "converted"
                db.commit()
                send_status_update(db, task)
            except Exception as e:
                print(f"convert命令执行错误: {str(e)}")
                task.status = "failed"
                db.commit()
                send_status_update(db, task)
                return
        # 4. 执行训练命令
        try:
            if cancel_event.is_set() or (task and task.status == "failed"):
                print(f"任务{task_id}已被取消，终止执行。")
                return
            train_proc = subprocess.Popen(
                train_command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=run_cwd,
                start_new_session=True,
            )
            _register_process(task_id, train_proc)
            train_stdout, train_stderr = train_proc.communicate()
            _unregister_process(task_id, train_proc)
            if cancel_event.is_set() or (task and task.status == "failed"):
                print(f"任务{task_id}已被取消，终止执行。")
                return
            if train_proc.returncode != 0:
                print(f"[threeDGS] train failed (algorithm={algorithm}) rc={train_result.returncode}")
                if train_stderr:
                    print(f"[threeDGS] train stderr (tail):\n{train_stderr[-2000:]}\n--- end stderr ---")
                if train_stdout:
                    print(f"[threeDGS] train stdout (tail):\n{train_stdout[-2000:]}\n--- end stdout ---")
                task.status = "failed"
            else:
                task.status = "trained"
                # 动态查找最新的 point_cloud.ply
                dynamic_result_url = _find_latest_point_cloud_ply(absolute_output_folder)
                if dynamic_result_url:
                    task.result_url = dynamic_result_url
                else:
                    # 兜底：保持旧逻辑（可能不存在，但能帮助排查）
                    folder_name = os.path.basename(absolute_output_folder)
                    task.result_url = f"{folder_name}/results/point_cloud/iteration_30000/point_cloud.ply"
            db.commit()
            send_status_update(db, task)
        except Exception as e:
            print(f"train命令执行错误: {str(e)}")
            task.status = "failed"
            db.commit()
            send_status_update(db, task)
    except Exception as e:
        print(f"Process task 错误: {str(e)}")
        print(f"错误堆栈: ", traceback.format_exc())
        db = next(get_db_session())
        task = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == task_id).first()
        task.status = "failed"
        db.commit()
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
                static_file_for_queued_task = next_db_session.query(StaticFileModel).filter(StaticFileModel.id == queued_task.file_id).first()
                if static_file_for_queued_task:
                    absolute_output_folder_for_queued_task = os.path.abspath(queued_task.folder_path)
                    output_pattern_for_queued_task = os.path.join(absolute_output_folder_for_queued_task, 'input', "%04d.jpg")
                    queued_task.status = "pending"
                    next_db_session.commit()
                    thread_pool.submit(
                        run_task_in_thread,
                        queued_task.id,
                        absolute_output_folder_for_queued_task,
                        static_file_for_queued_task.path,
                        output_pattern_for_queued_task,
                        queued_task.algorithm
                    )
                    print(f"排队任务 {queued_task.id} 已提交执行。")
                else:
                    print(f"错误：无法为排队任务 {queued_task.id} 找到关联的 StaticFileModel。")
                    queued_task.status = "failed"
                    next_db_session.commit()
                    # 可发送失败通知
        except Exception as e:
            print(f"启动排队任务时出错: {str(e)}")
        finally:
            next_db_session.close()
    # 任务结束后清理进程与取消事件
    _terminate_task_processes(task_id, grace_seconds=0.0)
    if task_id in task_cancel_events:
        del task_cancel_events[task_id]


@router.post("/threeDGS/cancel/{task_id}")
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ProcessedFileModel).filter(ProcessedFileModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ["trained", "failed"]:
        task.status = "failed"
        db.commit()
        # 设置取消事件并立即终止正在运行的进程
        if task_id in task_cancel_events:
            task_cancel_events[task_id].set()
        _terminate_task_processes(task_id)
        # 删除任务目录及其中所有数据
        try:
            folder_abs = os.path.abspath(task.folder_path) if task.folder_path else None
            if folder_abs and os.path.exists(folder_abs):
                shutil.rmtree(folder_abs, ignore_errors=False)
        except Exception as e:
            print(f"删除任务目录失败(task_id={task_id}): {str(e)}")
        # 清理进程登记映射
        with task_proc_lock:
            task_processes.pop(task_id, None)
        # 删除关联的 projects 记录并广播
        try:
            related_projects = db.query(ProjectModel).filter(ProjectModel.processed_file_id == task_id).all()
            if related_projects:
                deleted_ids = [p.id for p in related_projects]
                for p in related_projects:
                    db.delete(p)
                db.commit()
                for pid in deleted_ids:
                    await manager.broadcast({
                        "type": "project_updated",
                        "action": "delete",
                        "project_id": pid
                    })
        except Exception as e:
            print(f"删除关联项目记录失败(task_id={task_id}): {str(e)}")
    return {"msg": "任务已取消"}


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