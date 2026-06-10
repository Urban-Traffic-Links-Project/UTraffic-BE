import sys
from src.db.session import create_session
from sqlalchemy import text

print("Connecting to DB...")
try:
    session = create_session()
    res = session.execute(text("SELECT count(*) FROM incidents")).scalar()
    print(f"Connection successful! Total incidents: {res}")
except Exception as e:
    print(f"Error connecting to DB: {e}")
    sys.exit(1)
