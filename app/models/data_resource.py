from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.models.database import Base

class DataResource(Base):
    __tablename__ = "data_resources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    static_file_id = Column(Integer, ForeignKey("static_files.id"))
    static_file = relationship("StaticFile", back_populates="data_resources")