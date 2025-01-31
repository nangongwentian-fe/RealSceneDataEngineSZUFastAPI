# 项目启动步骤

1. 下载miniconda, 使用miniconda来方便管理环境
2. 从github上拉取项目代码
3. 使用```conda create --name sunhungkai python=3.10```创建当前项目的conda环境
4. 使用```conda activate sunhungkai```激活创建的python环境
5. 使用```pip install -r requirements.txt```安装当前项目所需的依赖
6. 使用```conda install -c conda-forge ffmpeg```安装ffmpeg否则ffmpeg就报错未找到
7. 修改```upload.py```中的```GAUSSIAN_SPLATTING_DIRECTORY```的地址为本机的3dgs项目文件夹地址
8. 使用```uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload```启动项目