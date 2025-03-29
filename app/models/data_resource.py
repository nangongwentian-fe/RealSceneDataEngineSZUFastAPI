from sqlalchemy import Column, Integer, String, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.models.database import Base

class DataResource(Base):
    __tablename__ = "data_resources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    static_file_id = Column(Integer, ForeignKey("static_files.id"))
    preview_frame_ids = Column(Text, nullable=True)  # 存储预览帧ID，以逗号分隔
    static_file = relationship("StaticFile", back_populates="data_resources")