import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from sqlmodel import Session, text
    from src.storage.database import engine
    
    print("Connecting to DB and checking blocking sessions...")
    with Session(engine) as session:
        # Set a short lock timeout (5 seconds) so we don't hang
        session.execute(text("SET lock_timeout = 5000"))
        
        try:
            print("Attempting to drop constraint uq_incidents_tomtom_id...")
            session.execute(text("ALTER TABLE incidents DROP CONSTRAINT IF EXISTS uq_incidents_tomtom_id CASCADE"))
            session.commit()
            print("✅ Constraint uq_incidents_tomtom_id dropped successfully!")
        except Exception as db_err:
            session.rollback()
            print(f"⚠️ Failed to drop constraint: {db_err}")
            print("\nDiagnosing active queries and locks:")
            # Find active queries that might be holding locks
            q = text("""
                SELECT pid, query, state, age(clock_timestamp(), query_start) AS age
                FROM pg_stat_activity 
                WHERE state != 'idle' AND pid != pg_backend_pid();
            """)
            active_queries = session.execute(q).mappings().all()
            for row in active_queries:
                print(f"PID: {row['pid']} | State: {row['state']} | Age: {row['age']} | Query: {row['query']}")
except Exception as e:
    print("General error:")
    import traceback
    traceback.print_exc()
