"""
Quick script to add/update the admin user for testing
Run this before starting the server
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os
from pathlib import Path
import hashlib
from datetime import datetime

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

async def seed_admin():
    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
    db_name = os.environ.get('DB_NAME', 'test_database')
    
    print(f"Connecting to MongoDB: {mongo_url}")
    print(f"Database: {db_name}")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    try:
        # Test connection
        await client.admin.command('ping')
        print("✓ Connected to MongoDB")
        
        # Create company
        company = await db.companies.find_one({"phone": "0767862760"})
        if not company:
            result = await db.companies.insert_one({
                "name": "Mzansi Distribution",
                "contact_person": "Admin",
                "phone": "0767862760",
                "email": "admin@mzansi.co.za",
                "address": "Johannesburg, Gauteng",
                "created_at": datetime.utcnow()
            })
            company_id = str(result.inserted_id)
            print(f"✓ Created company: {company_id}")
        else:
            company_id = str(company["_id"])
            print(f"✓ Company exists: {company_id}")
        
        # Create/update admin user with phone 0813081833
        phone = "0813081833"
        pin = "1984"
        
        existing = await db.users.find_one({"phone": phone})
        if existing:
            await db.users.update_one(
                {"phone": phone},
                {"$set": {
                    "pin_hash": hash_pin(pin),
                    "is_active": True,
                    "company_id": company_id
                }}
            )
            print(f"✓ Updated user: {phone}")
        else:
            await db.users.insert_one({
                "name": "Admin User",
                "phone": phone,
                "pin_hash": hash_pin(pin),
                "role": "admin",
                "is_active": True,
                "company_id": company_id,
                "created_at": datetime.utcnow()
            })
            print(f"✓ Created user: {phone}")
        
        # Also ensure the original admin exists
        phone2 = "0767862760"
        existing2 = await db.users.find_one({"phone": phone2})
        if not existing2:
            await db.users.insert_one({
                "name": "Admin",
                "phone": phone2,
                "pin_hash": hash_pin(pin),
                "role": "admin",
                "is_active": True,
                "company_id": company_id,
                "created_at": datetime.utcnow()
            })
            print(f"✓ Created user: {phone2}")
        
        print("\n✓ Database seeded successfully!")
        print(f"\nYou can now login with:")
        print(f"  Phone: {phone} or {phone2}")
        print(f"  PIN: {pin}")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(seed_admin())
