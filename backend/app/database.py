import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://findback:findback@localhost:5432/findback")
# sync engine for MVP simplicity; async can be added later
SYNC_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://")

engine = create_engine(SYNC_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
