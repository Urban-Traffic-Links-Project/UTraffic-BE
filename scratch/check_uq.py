import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from sqlmodel import Session, text
    from src.storage.database import engine
    
    with Session(engine) as session:
        # Query pg_constraint to see if uq_incidents_tomtom_id exists
        q = text("""
            SELECT conname, contype 
            FROM pg_constraint 
            WHERE conname = 'uq_incidents_tomtom_id';
        """)
        row = session.execute(q).first()
        if row:
            print(f"Constraint found: Name={row[0]}, Type={row[1]}")
        else:
            print("Constraint uq_incidents_tomtom_id NOT found (dropped or table doesn't exist).")
            
except Exception as e:
    print("Error checking constraint:")
    import traceback
    traceback.print_exc()
