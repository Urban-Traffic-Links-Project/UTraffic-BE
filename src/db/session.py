from sqlmodel import Session, create_engine

from src.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)


def create_session() -> Session:
    return Session(engine)