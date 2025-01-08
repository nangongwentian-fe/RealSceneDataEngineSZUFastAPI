from sqlalchemy import Column, Integer, String
from app.models.database import Base

class User(Base):
    __tablename__ = "users"  # 数据库中的表名
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, index=True)
    password = Column(String(255), nullable=False)
