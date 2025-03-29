from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

# MySQL 数据库连接 URL
DATABASE_URL = "mysql+mysqlconnector://root:RealScene%402025%21@172.26.226.64:3306/real_scene_data_engine"

# 创建数据库引擎，添加连接池配置
engine = create_engine(
    DATABASE_URL,
    pool_size=5,  # 连接池大小
    max_overflow=10,  # 超过pool_size后最多可以创建的连接数
    pool_timeout=30,  # 连接池中没有可用连接的等待时间
    pool_recycle=3600,  # 连接在连接池中的最大生存时间
    pool_pre_ping=True,  # 每次从连接池获取连接时ping一下
    poolclass=QueuePool
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基础类，用于定义 ORM 模型
Base = declarative_base()

# 获取数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()