import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from sqlmodel import Session, text
    from src.storage.database import engine
    
    print("Force terminating other database connections...")
    with Session(engine) as session:
        # Terminate other sessions on the same database
        session.execute(text("""
            SELECT pg_terminate_backend(pid) 
            FROM pg_stat_activity 
            WHERE datname = current_database() AND pid != pg_backend_pid()
        """))
        session.commit()
        
        print("Dropping tables incident_edges and incidents...")
        session.execute(text("DROP TABLE IF EXISTS incident_edges CASCADE"))
        session.execute(text("DROP TABLE IF EXISTS incidents CASCADE"))
        session.commit()
        
    print("✅ Tables dropped successfully!")
except Exception as e:
    print("Error force dropping tables:")
    import traceback
    traceback.print_exc()
