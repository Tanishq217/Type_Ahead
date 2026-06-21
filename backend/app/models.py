from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from .database import Base

class SearchQuery(Base):
    """
    Primary table storing the overall frequency count of searched queries.
    Used for general typeahead suggestion matches.
    """
    __tablename__ = "search_queries"

    id = Column(Integer, primary_key=True, index=True)
    query_text = Column(String, unique=True, index=True, nullable=False)
    total_count = Column(Integer, default=1, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class SearchActivity(Base):
    """
    Event table to track recent searches for trending calculations.
    Entries are processed to score queries based on recency.
    """
    __tablename__ = "search_activity"

    id = Column(Integer, primary_key=True, index=True)
    query_text = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
