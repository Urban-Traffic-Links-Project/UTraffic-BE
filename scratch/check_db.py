from sqlalchemy import create_engine, inspect
from src.core.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url)
inspector = inspect(engine)
tables = inspector.get_table_names()
print("Tables in DB:", tables)
