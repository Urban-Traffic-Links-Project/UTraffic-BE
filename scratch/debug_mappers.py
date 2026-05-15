import sys
import os

# Thêm thư mục src vào PYTHONPATH
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from sqlmodel import SQLModel, create_engine
    import src.storage.models  # Import tất cả models
    from sqlalchemy.orm import configure_mappers
    
    print("Attempting to configure mappers...")
    configure_mappers()
    print("Mappers configured successfully!")
except Exception as e:
    print("Error during mapper configuration:")
    import traceback
    traceback.print_exc()
    sys.exit(1)
