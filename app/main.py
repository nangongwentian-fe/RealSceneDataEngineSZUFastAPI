from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import users, upload, data_resource, project, sse, three_d_gs, tag  # 导入新的路由
from app.models.database import engine, Base

app = FastAPI(
    title="Real Scene Data Engine API",
    description="API for Real Scene Data Engine",
    version="1.0.0",
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(three_d_gs.router) # 3dgs相关接口
app.include_router(sse.router) # sse相关接口
app.include_router(users.router) # 用户相关接口
app.include_router(upload.router) # 上传相关接口
app.include_router(data_resource.router) # 数据资源相关接口
app.include_router(project.router) # 项目相关接口
app.include_router(tag.router) # 标签相关接口

# 初始化数据库表
Base.metadata.create_all(bind=engine)

@app.get("/")
async def root():
    return {"message": "Welcome to Real Scene Data Engine API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
