import sys
import os

# Thêm thư mục src vào PYTHONPATH
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from sqlmodel import SQLModel, create_engine, Session, text
    from src.storage.database import engine
    import src.storage.models
    
    print("Dropping problematic tables to force recreation with new columns...")
    with Session(engine) as session:
        # Danh sách các bảng cần cập nhật schema sau khi merge
        tables_to_drop = ["users", "refresh_tokens", "user_sessions", "correlation_snapshots", "model_versions", "predictions"]
        
        for table in tables_to_drop:
            print(f"Dropping table: {table}")
            session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        
        session.commit()
    
    print("Recreating tables with updated schema...")
    SQLModel.metadata.create_all(engine)
    print("Schema updated successfully!")
    
except Exception as e:
    print("Error during schema update:")
    import traceback
    traceback.print_exc()
    sys.exit(1)
