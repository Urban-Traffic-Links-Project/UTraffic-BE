import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from sqlmodel import Session, text
    from src.storage.database import engine
    
    print("Dropping constraint uq_incidents_tomtom_id from incidents table...")
    with Session(engine) as session:
        # Drop unique constraint on incidents table
        session.execute(text("ALTER TABLE incidents DROP CONSTRAINT IF EXISTS uq_incidents_tomtom_id CASCADE"))
        session.commit()
    print("Constraint dropped successfully!")
except Exception as e:
    print("Error dropping constraint:")
    import traceback
    traceback.print_exc()
