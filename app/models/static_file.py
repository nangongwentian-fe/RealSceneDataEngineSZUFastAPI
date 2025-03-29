from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.models.database import Base

class StaticFile(Base):
    __tablename__ = "static_files"

    id = Column(Integer, primary_key=True, index=True)
    path = Column(String(255), unique=True, index=True)
    filename = Column(String(255), unique=True, index=True)
    original_filename = Column(String(255))
    processed_files = relationship("ProcessedFile", back_populates="static_file")
    data_resources = relationship("DataResource", back_populates="static_file")  # 添加这行
    projects = relationship("Project", back_populates="static_file")
    projects = relationship("Project", foreign_keys="[Project.static_file_id]", back_populates="static_file")