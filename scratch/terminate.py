from src.db.session import create_session
from sqlalchemy import text
session = create_session()
session.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'utraffic_db' AND pid != pg_backend_pid()"))
print("Done")
