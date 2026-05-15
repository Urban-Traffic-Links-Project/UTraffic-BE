import sys
import os
import uuid

# Thêm thư mục src vào PYTHONPATH
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from sqlmodel import Session, select
    from src.storage.database import engine
    from src.modules.auth.service import create_user, login
    from src.storage.models.auth import User
    
    with Session(engine) as session:
        # Check if test user exists
        test_email = "test@example.com"
        user = session.exec(select(User).where(User.email == test_email)).first()
        if not user:
            print(f"Creating test user: {test_email}")
            user = create_user(session, test_email, "password123", "Test User")
            print(f"User created: {user.id}")
        else:
            print(f"User already exists: {user.id}")
            
        print("Attempting login...")
        user, access, refresh = login(session, test_email, "password123", ip="127.0.0.1", device="test-script")
        print("Login successful!")
        print(f"Access Token: {access[:20]}...")
except Exception as e:
    print("Error during test:")
    import traceback
    traceback.print_exc()
    sys.exit(1)
