import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os
from pathlib import Path

# Load environment variables
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

async def test_connection():
    print("Testing MongoDB connection...")
    
    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
    db_name = os.environ.get('DB_NAME', 'test_database')
    
    print(f"Connecting to: {mongo_url}")
    print(f"Database: {db_name}")
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Test connection
        await client.admin.command('ping')
        print("✓ MongoDB connection successful!")
        
        # Check users collection
        user_count = await db.users.count_documents({})
        print(f"✓ Found {user_count} users in database")
        
        # Check for specific user
        test_phone = "0767862760"
        user = await db.users.find_one({"phone": test_phone})
        if user:
            print(f"✓ Found user with phone {test_phone}")
            print(f"  - Name: {user.get('name')}")
            print(f"  - Role: {user.get('role')}")
            print(f"  - Active: {user.get('is_active', True)}")
            print(f"  - Has PIN hash: {bool(user.get('pin_hash'))}")
        else:
            print(f"✗ User with phone {test_phone} not found")
            print("\nAvailable users:")
            users = await db.users.find({}).to_list(10)
            for u in users:
                print(f"  - {u.get('phone')} ({u.get('name')}) - {u.get('role')}")
        
        # Check companies
        company_count = await db.companies.count_documents({})
        print(f"\n✓ Found {company_count} companies in database")
        
        client.close()
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_connection())
