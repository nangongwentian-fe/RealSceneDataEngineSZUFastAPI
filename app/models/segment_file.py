from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.database import Base

class SegmentFile(Base):
    __tablename__ = "segment_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    processed_file_id = Column(Integer, ForeignKey("processed_files.id"), nullable=False)
    segment_prompt_text = Column(String(255), nullable=False)
    result_url = Column(String(255), nullable=False)

    processed_file = relationship("ProcessedFile", back_populates="segment_files")

    # 联合唯一约束确保同一场景同一提示只存一条记录
    __table_args__ = (
        UniqueConstraint('processed_file_id', 'segment_prompt_text', name='uix_project_prompt'),
    )