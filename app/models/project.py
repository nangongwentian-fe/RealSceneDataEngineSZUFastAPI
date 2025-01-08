# app/models/project.py
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.models.database import Base

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    processed_file_id = Column(Integer, ForeignKey("processed_files.id"))
    static_file_id = Column(Integer, ForeignKey("static_files.id"))

    processed_file = relationship("ProcessedFile", back_populates="projects")
    static_file = relationship("StaticFile", back_populates="projects")