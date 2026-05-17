import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from sqlmodel import Session, select
    from src.storage.database import engine
    from src.storage.models.correlation import CorrelationSnapshot
    
    with Session(engine) as session:
        snapshots = session.exec(select(CorrelationSnapshot)).all()
        print(f"Total snapshots: {len(snapshots)}")
        for s in snapshots:
            print(f"ID: {s.id}, Active: {s.is_active}, Method: {s.method}")
            
except Exception as e:
    print("Error:")
    import traceback
    traceback.print_exc()
