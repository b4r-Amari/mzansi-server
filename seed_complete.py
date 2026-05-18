"""
Complete database seeding script
Seeds ALL collections with sample data for testing
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

async def seed_complete():
    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
    db_name = os.environ.get('DB_NAME', 'test_database')
    
    print(f"Connecting to MongoDB: {mongo_url}")
    print(f"Database: {db_name}")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    try:
        # Test connection
        await client.admin.command('ping')
        print("✓ Connected to MongoDB\n")
        
        # ==================== COMPANY ====================
        print("📦 Seeding Company...")
        company = await db.companies.find_one({"phone": "0767862760"})
        if not company:
            result = await db.companies.insert_one({
                "name": "Mzansi Distribution",
                "contact_person": "Admin",
                "phone": "0767862760",
                "email": "admin@mzansi.co.za",
                "address": "Johannesburg, Gauteng",
                "province": "Gauteng",
                "created_at": datetime.utcnow()
            })
            company_id = str(result.inserted_id)
            print(f"  ✓ Created company: {company_id}")
        else:
            company_id = str(company["_id"])
            print(f"  ✓ Company exists: {company_id}")
        
        # ==================== USERS ====================
        print("\n👥 Seeding Users...")
        users_data = [
            {"name": "Admin User", "phone": "0813081833", "pin": "1984", "role": "admin"},
            {"name": "Admin", "phone": "0767862760", "pin": "1984", "role": "admin"},
            {"name": "Manager John", "phone": "0821234567", "pin": "1234", "role": "manager"},
            {"name": "Driver Sipho", "phone": "0831234567", "pin": "1234", "role": "driver"},
            {"name": "Driver Thabo", "phone": "0841234567", "pin": "1234", "role": "driver"},
        ]
        
        for user_data in users_data:
            existing = await db.users.find_one({"phone": user_data["phone"]})
            if not existing:
                await db.users.insert_one({
                    "name": user_data["name"],
                    "phone": user_data["phone"],
                    "pin_hash": hash_pin(user_data["pin"]),
                    "role": user_data["role"],
                    "is_active": True,
                    "company_id": company_id,
                    "created_at": datetime.utcnow()
                })
                print(f"  ✓ Created user: {user_data['name']} ({user_data['phone']})")
            else:
                print(f"  - User exists: {user_data['name']} ({user_data['phone']})")
        
        # ==================== PRODUCTS ====================
        print("\n🛍️ Seeding Products...")
        products_data = [
            {"name": "White Bread", "category": "Bakery", "unit_type": "loaf", "price": 18.50, "vat_applicable": True},
            {"name": "Brown Bread", "category": "Bakery", "unit_type": "loaf", "price": 20.00, "vat_applicable": True},
            {"name": "Whole Wheat Bread", "category": "Bakery", "unit_type": "loaf", "price": 22.00, "vat_applicable": True},
            {"name": "Full Cream Milk 2L", "category": "Dairy", "unit_type": "bottle", "price": 32.00, "vat_applicable": True},
            {"name": "Amasi 1L", "category": "Dairy", "unit_type": "bottle", "price": 22.50, "vat_applicable": True},
            {"name": "Maas 500ml", "category": "Dairy", "unit_type": "bottle", "price": 15.00, "vat_applicable": True},
            {"name": "Large Eggs (30)", "category": "Eggs", "unit_type": "tray", "price": 65.00, "vat_applicable": True},
            {"name": "Medium Eggs (18)", "category": "Eggs", "unit_type": "tray", "price": 45.00, "vat_applicable": True},
            {"name": "Sunflower Oil 750ml", "category": "Cooking", "unit_type": "bottle", "price": 45.00, "vat_applicable": True},
            {"name": "Cooking Oil 2L", "category": "Cooking", "unit_type": "bottle", "price": 85.00, "vat_applicable": True},
            {"name": "Maize Meal 5kg", "category": "Staples", "unit_type": "bag", "price": 55.00, "vat_applicable": True},
            {"name": "Maize Meal 12.5kg", "category": "Staples", "unit_type": "bag", "price": 125.00, "vat_applicable": True},
            {"name": "Sugar 2kg", "category": "Staples", "unit_type": "bag", "price": 38.50, "vat_applicable": True},
            {"name": "Rice 2kg", "category": "Staples", "unit_type": "bag", "price": 42.00, "vat_applicable": True},
            {"name": "Coca Cola 2L", "category": "Beverages", "unit_type": "bottle", "price": 24.00, "vat_applicable": True},
            {"name": "Fanta Orange 2L", "category": "Beverages", "unit_type": "bottle", "price": 22.00, "vat_applicable": True},
        ]
        
        existing_products = await db.products.count_documents({"company_id": company_id})
        if existing_products == 0:
            for prod in products_data:
                prod["company_id"] = company_id
                prod["is_active"] = True
                prod["created_at"] = datetime.utcnow()
            await db.products.insert_many(products_data)
            print(f"  ✓ Created {len(products_data)} products")
        else:
            print(f"  - Products exist: {existing_products} products")
        
        # ==================== VEHICLES ====================
        print("\n🚚 Seeding Vehicles...")
        vehicles_data = [
            {"registration": "GP 123 ABC", "name": "Truck 1 - Toyota Dyna", "vehicle_type": "truck", "capacity_crates": 120},
            {"registration": "GP 456 DEF", "name": "Truck 2 - Isuzu NPR", "vehicle_type": "truck", "capacity_crates": 150},
            {"registration": "GP 789 GHI", "name": "Van 1 - Hyundai H100", "vehicle_type": "van", "capacity_crates": 80},
            {"registration": "GP 321 JKL", "name": "Bakkie 1 - Toyota Hilux", "vehicle_type": "bakkie", "capacity_crates": 50},
        ]
        
        existing_vehicles = await db.vehicles.count_documents({"company_id": company_id})
        if existing_vehicles == 0:
            for veh in vehicles_data:
                veh["company_id"] = company_id
                veh["is_active"] = True
                veh["created_at"] = datetime.utcnow()
            await db.vehicles.insert_many(vehicles_data)
            print(f"  ✓ Created {len(vehicles_data)} vehicles")
        else:
            print(f"  - Vehicles exist: {existing_vehicles} vehicles")
        
        # ==================== ROUTES ====================
        print("\n🗺️ Seeding Routes...")
        routes_data = [
            {
                "name": "Soweto North Route",
                "description": "Covers Meadowlands, Orlando, Diepkloof",
                "province": "Gauteng",
                "district": "City of Johannesburg",
                "areas_covered": ["Soweto", "Meadowlands", "Orlando", "Diepkloof"],
                "delivery_schedule": {
                    "delivery_days": ["Monday", "Wednesday", "Friday"],
                    "cut_off_hours_before": 16,
                    "cut_off_time": "16:00"
                }
            },
            {
                "name": "Soweto South Route",
                "description": "Covers Dobsonville, Protea Glen, Orange Farm",
                "province": "Gauteng",
                "district": "City of Johannesburg",
                "areas_covered": ["Dobsonville", "Protea Glen", "Orange Farm"],
                "delivery_schedule": {
                    "delivery_days": ["Tuesday", "Thursday"],
                    "cut_off_hours_before": 16,
                    "cut_off_time": "16:00"
                }
            },
            {
                "name": "Alexandra Route",
                "description": "Alexandra township and surrounds",
                "province": "Gauteng",
                "district": "City of Johannesburg",
                "areas_covered": ["Alexandra", "Wynberg", "Sandton"],
                "delivery_schedule": {
                    "delivery_days": ["Monday", "Thursday"],
                    "cut_off_hours_before": 14,
                    "cut_off_time": "14:00"
                }
            },
            {
                "name": "Pretoria Route",
                "description": "Pretoria CBD, Mamelodi, Atteridgeville",
                "province": "Gauteng",
                "district": "City of Tshwane",
                "areas_covered": ["Pretoria CBD", "Mamelodi", "Atteridgeville", "Soshanguve"],
                "delivery_schedule": {
                    "delivery_days": ["Wednesday", "Saturday"],
                    "cut_off_hours_before": 18,
                    "cut_off_time": "18:00"
                }
            },
        ]
        
        existing_routes = await db.routes.count_documents({"company_id": company_id})
        if existing_routes == 0:
            route_ids = []
            for route in routes_data:
                route["company_id"] = company_id
                route["assigned_driver_id"] = None
                route["assigned_driver_name"] = None
                route["created_at"] = datetime.utcnow()
                result = await db.routes.insert_one(route)
                route_ids.append(str(result.inserted_id))
            print(f"  ✓ Created {len(routes_data)} routes")
        else:
            print(f"  - Routes exist: {existing_routes} routes")
            routes = await db.routes.find({"company_id": company_id}).to_list(10)
            route_ids = [str(r["_id"]) for r in routes]
        
        # ==================== CUSTOMERS ====================
        print("\n👥 Seeding Customers...")
        customers_data = [
            {"name": "Shoprite Soweto", "contact": "011-555-0101", "location": "Soweto Mall", "payment_terms": "credit", "credit_limit": 5000, "route_idx": 0},
            {"name": "Pick n Pay Diepkloof", "contact": "011-555-0102", "location": "Diepkloof Square", "payment_terms": "credit", "credit_limit": 10000, "route_idx": 0},
            {"name": "Spaza Shop - Mama Joy", "contact": "078-555-0103", "location": "Orlando West", "payment_terms": "cash", "route_idx": 0},
            {"name": "Corner Cafe Meadowlands", "contact": "079-555-0104", "location": "Meadowlands Zone 1", "payment_terms": "cash", "route_idx": 0},
            {"name": "Spar Orange Farm", "contact": "011-555-0105", "location": "Orange Farm Mall", "payment_terms": "credit", "credit_limit": 8000, "route_idx": 1},
            {"name": "Tuckshop - Mr Dlamini", "contact": "082-555-0106", "location": "Protea Glen", "payment_terms": "cash", "route_idx": 1},
            {"name": "OK Foods Alexandra", "contact": "011-555-0107", "location": "Alexandra Mall", "payment_terms": "credit", "credit_limit": 15000, "route_idx": 2},
            {"name": "Kasi Superette", "contact": "083-555-0108", "location": "Alexandra East Bank", "payment_terms": "mixed", "credit_limit": 3000, "route_idx": 2},
            {"name": "Boxer Mamelodi", "contact": "012-555-0109", "location": "Mamelodi", "payment_terms": "credit", "credit_limit": 12000, "route_idx": 3},
            {"name": "Spaza - Gogo Thandi", "contact": "073-555-0110", "location": "Atteridgeville", "payment_terms": "cash", "route_idx": 3},
        ]
        
        existing_customers = await db.customers.count_documents({"company_id": company_id})
        if existing_customers == 0:
            for cust in customers_data:
                route_idx = cust.pop("route_idx")
                if route_idx < len(route_ids):
                    cust["route_id"] = route_ids[route_idx]
                cust["company_id"] = company_id
                cust["is_active"] = True
                cust["balance"] = 0.0
                cust["created_at"] = datetime.utcnow()
            await db.customers.insert_many(customers_data)
            print(f"  ✓ Created {len(customers_data)} customers")
        else:
            print(f"  - Customers exist: {existing_customers} customers")
        
        # ==================== STOCK ====================
        print("\n📦 Seeding Stock...")
        products = await db.products.find({"company_id": company_id}).to_list(100)
        existing_stock = await db.stock.count_documents({"company_id": company_id})
        
        if existing_stock == 0:
            stock_data = []
            for product in products:
                stock_data.append({
                    "product_id": str(product["_id"]),
                    "product_name": product["name"],
                    "quantity": 500,  # Starting stock
                    "company_id": company_id,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                })
            if stock_data:
                await db.stock.insert_many(stock_data)
                print(f"  ✓ Created stock for {len(stock_data)} products (500 units each)")
        else:
            print(f"  - Stock exists: {existing_stock} items")
        
        # ==================== SUMMARY ====================
        print("\n" + "="*60)
        print("✅ DATABASE SEEDING COMPLETE!")
        print("="*60)
        
        # Count all collections
        users_count = await db.users.count_documents({"company_id": company_id})
        products_count = await db.products.count_documents({"company_id": company_id})
        vehicles_count = await db.vehicles.count_documents({"company_id": company_id})
        routes_count = await db.routes.count_documents({"company_id": company_id})
        customers_count = await db.customers.count_documents({"company_id": company_id})
        stock_count = await db.stock.count_documents({"company_id": company_id})
        
        print(f"\n📊 Database Summary:")
        print(f"  • Companies: 1")
        print(f"  • Users: {users_count}")
        print(f"  • Products: {products_count}")
        print(f"  • Vehicles: {vehicles_count}")
        print(f"  • Routes: {routes_count}")
        print(f"  • Customers: {customers_count}")
        print(f"  • Stock Items: {stock_count}")
        
        print(f"\n🔐 Login Credentials:")
        print(f"  Admin 1: 0813081833 / PIN: 1984")
        print(f"  Admin 2: 0767862760 / PIN: 1984")
        print(f"  Manager: 0821234567 / PIN: 1234")
        print(f"  Driver 1: 0831234567 / PIN: 1234")
        print(f"  Driver 2: 0841234567 / PIN: 1234")
        
        print("\n✅ Your application is now ready to use!")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(seed_complete())
