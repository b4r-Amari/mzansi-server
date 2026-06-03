from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, BackgroundTasks, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse, Response
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import io
import csv
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, date, timedelta
import hashlib
import jwt
from bson import ObjectId
import xlsxwriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# JWT Configuration
JWT_SECRET = os.environ.get('JWT_SECRET')
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable must be set")
JWT_ALGORITHM = "HS256"

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db_name = os.environ.get('DB_NAME')
if not db_name:
    raise RuntimeError("DB_NAME environment variable must be set")
db = client[db_name]

# Create the main app
app = FastAPI(title="Mzansi FMCG Tracker API")

# CORS Configuration
# Covers: localhost (dev), Vercel (browser), EAS/APK (native - no Origin header)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8081",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8081",
        "*"  # Allow all origins - necessary for mobile apps
    ],
    allow_credentials=False,  # Must be False when allow_origins includes "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Security
security = HTTPBearer(auto_error=False)

# NOTE: app.include_router(api_router) is called at the bottom of this file

# Register router (must be done after all routes are defined — see bottom of file)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Helper function for ObjectId
def str_id(doc: dict) -> dict:
    if doc and '_id' in doc:
        doc['id'] = str(doc['_id'])
        del doc['_id']
    return doc

# ==================== ROLE PERMISSIONS ====================
# admin: Full access to everything
# manager: Products, customers, routes, sales corrections (no user management)
# driver: Own routes, record sales, add customers, edit same-day entries
# conductor: View only, assist with delivery confirmation

ROLE_HIERARCHY = {
    'admin': 4,
    'manager': 3,
    'driver': 2,
    'conductor': 1,
    'customer': 0
}

def check_role(user: dict, required_roles: List[str]) -> bool:
    """Check if user has one of the required roles"""
    return user.get('role') in required_roles

def is_admin_or_manager(user: dict) -> bool:
    return user.get('role') in ['admin', 'manager']

def is_admin(user: dict) -> bool:
    return user.get('role') == 'admin'

def is_customer(user: dict) -> bool:
    return user.get('role') == 'customer'

# ==================== MODELS ====================

class CompanyCreate(BaseModel):
    name: str
    contact_person: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None

class CompanySetup(BaseModel):
    company: CompanyCreate
    admin_name: str
    admin_phone: str
    admin_pin: str  # 4-digit PIN

class CompanyResponse(BaseModel):
    id: str
    name: str
    contact_person: str
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None
    created_at: datetime

class UserCreate(BaseModel):
    name: str
    phone: str
    pin: str  # 4-digit PIN
    role: str = "driver"  # admin, manager, driver, conductor
    company_id: Optional[str] = None

class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

class UserResponse(BaseModel):
    id: str
    name: str
    phone: str
    role: str
    is_active: bool = True
    created_at: datetime
    company_id: Optional[str] = None
    customer_profile: Optional[Dict[str, Any]] = None

class LoginRequest(BaseModel):
    phone: str
    pin: str

class LoginResponse(BaseModel):
    token: str
    user: UserResponse
    company: Optional[CompanyResponse] = None

class ProductCreate(BaseModel):
    name: str
    category: str
    unit_type: str  # units, crates, liters
    price: float
    vat_applicable: bool = True  # True = has VAT, False = VAT exempt

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    unit_type: Optional[str] = None
    price: Optional[float] = None
    vat_applicable: Optional[bool] = None
    is_active: Optional[bool] = None

class ProductResponse(BaseModel):
    id: str
    name: str
    category: str
    unit_type: str
    price: float
    vat_applicable: bool = True
    is_active: bool = True

class CustomerCreate(BaseModel):
    name: str
    contact: Optional[str] = None
    location: Optional[str] = None
    payment_terms: str = "cash"  # cash, credit, mixed
    credit_limit: Optional[float] = None
    route_id: Optional[str] = None
    custom_prices: Optional[Dict[str, float]] = None  # product_id -> custom price

class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    location: Optional[str] = None
    payment_terms: Optional[str] = None
    credit_limit: Optional[float] = None
    route_id: Optional[str] = None
    custom_prices: Optional[Dict[str, float]] = None  # product_id -> custom price
    is_active: Optional[bool] = None

class CustomerResponse(BaseModel):
    id: str
    name: str
    contact: Optional[str]
    location: Optional[str]
    payment_terms: str
    credit_limit: Optional[float] = None
    route_id: Optional[str]
    is_active: bool = True
    balance: float = 0.0

class VehicleCreate(BaseModel):
    registration: str  # License plate
    name: str  # e.g., "Truck 1", "Van A"
    vehicle_type: str = "truck"  # truck, van, bakkie
    capacity_crates: int = 100

class VehicleUpdate(BaseModel):
    registration: Optional[str] = None
    name: Optional[str] = None
    vehicle_type: Optional[str] = None
    capacity_crates: Optional[int] = None
    is_active: Optional[bool] = None

class VehicleResponse(BaseModel):
    id: str
    registration: str
    name: str
    vehicle_type: str
    capacity_crates: int
    is_active: bool = True

class RouteCreate(BaseModel):
    name: str
    description: Optional[str] = None
    province: Optional[str] = None
    district: Optional[str] = None
    areas_covered: List[str] = []  # villages/towns/cities covered by this route
    assigned_driver_id: Optional[str] = None
    delivery_schedule: Optional[dict] = None

class RouteUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    assigned_driver_id: Optional[str] = None
    province: Optional[str] = None
    district: Optional[str] = None
    areas_covered: Optional[List[str]] = None
    delivery_schedule: Optional[dict] = None

class RouteResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    customer_count: int = 0
    assigned_driver_id: Optional[str] = None
    assigned_driver_name: Optional[str] = None
    province: Optional[str] = None
    district: Optional[str] = None
    areas_covered: List[str] = []
    delivery_schedule: Optional[dict] = None

class SaleItemCreate(BaseModel):
    product_id: str
    product_name: str
    quantity_delivered: int
    quantity_returned: int = 0
    damages: int = 0
    unit_price: float

class SplitPayment(BaseModel):
    method: str  # cash, eft, shop2shop, kazang
    amount: float
    reference: Optional[str] = None  # Reference number for EFT/Shop2Shop/Kazang

class SaleCreate(BaseModel):
    route_id: str
    customer_id: str
    customer_name: str
    items: List[SaleItemCreate]
    crates_dropped: int = 0  # Crates left with customer
    crates_collected: int = 0  # Crates collected back (empties)
    cash_collected: float  # Total collected (sum of all payment methods)
    payment_type: str = "cash"  # Primary payment type: cash, eft, shop2shop, kazang, split
    split_payments: Optional[List[SplitPayment]] = None  # For split payments
    notes: Optional[str] = None
    delivery_status: str = "delivered"  # delivered, partial, skipped

class SaleUpdate(BaseModel):
    items: Optional[List[SaleItemCreate]] = None
    cash_collected: Optional[float] = None
    payment_type: Optional[str] = None
    split_payments: Optional[List[SplitPayment]] = None
    notes: Optional[str] = None
    delivery_status: Optional[str] = None
    void_reason: Optional[str] = None

class SaleResponse(BaseModel):
    id: str
    invoice_number: Optional[str] = None
    route_id: str
    route_name: Optional[str] = None
    customer_id: str
    customer_name: str
    driver_id: str
    driver_name: str
    items: List[dict]
    total_amount: float
    cash_collected: float
    shortage_amount: float = 0
    crates_dropped: int = 0
    crates_collected: int = 0
    payment_type: str
    split_payments: Optional[List[dict]] = None
    delivery_status: str = "delivered"
    notes: Optional[str]
    is_voided: bool = False
    void_reason: Optional[str] = None
    created_at: datetime

# ==================== STOCK MANAGEMENT MODELS ====================

class StockReceiveCreate(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    supplier: Optional[str] = None
    batch_reference: Optional[str] = None
    damages_in_transit: int = 0  # Damaged during transport
    rejected_stock: int = 0  # Rejected due to quality issues
    spoilt_from_factory: int = 0  # Spoilt/expired from factory
    crates_received: int = 0  # Crates received from manufacturer
    crates_returned: int = 0  # Empty crates returned to manufacturer
    notes: Optional[str] = None

class StockTakeCreate(BaseModel):
    product_id: str
    product_name: str
    system_quantity: int
    physical_count: int
    variance_reason: Optional[str] = None

class StockAdjustmentCreate(BaseModel):
    product_id: str
    product_name: str
    adjustment_quantity: int  # Positive or negative
    reason: str  # damages, spoilage, theft, correction, other
    notes: Optional[str] = None

class StockMovementResponse(BaseModel):
    id: str
    movement_type: str  # receive, sale, adjustment, take
    product_id: str
    product_name: str
    quantity: int
    reference: Optional[str]
    personnel_id: str
    personnel_name: str
    created_at: datetime

# ==================== EMAIL RECIPIENT MANAGEMENT ====================

class EmailRecipientCreate(BaseModel):
    email: str
    name: Optional[str] = None
    report_types: List[str]  # sales, stock, finance, daily, weekly

class DailyRouteStart(BaseModel):
    route_id: str
    vehicle_id: str  # Required - which vehicle is being used
    opening_km: float
    crates_out: int
    vehicle_check: Optional[Dict[str, Any]] = None

class DailyRouteUpdate(BaseModel):
    opening_km: Optional[float] = None
    closing_km: Optional[float] = None
    crates_out: Optional[int] = None
    crates_in: Optional[int] = None
    damages_count: Optional[int] = None
    fuel_used: Optional[float] = None
    notes: Optional[str] = None

class DailyRouteEnd(BaseModel):
    closing_km: float
    crates_in: int
    damages_count: int = 0
    fuel_used: Optional[float] = None
    notes: Optional[str] = None

class DailyRouteResponse(BaseModel):
    id: str
    route_id: str
    route_name: str
    vehicle_id: Optional[str] = None
    vehicle_name: Optional[str] = None
    vehicle_registration: Optional[str] = None
    driver_id: str
    driver_name: str
    date: str
    opening_km: float
    closing_km: Optional[float] = None
    km_traveled: Optional[float] = None
    crates_out: int
    crates_in: Optional[int] = None
    damages_count: int = 0
    status: str  # active, completed
    sales_count: int = 0
    total_collected: float = 0.0
    total_expected: float = 0.0  # Total invoice amounts
    total_shortage: float = 0.0  # Total shortages (Expected - Collected)
    vehicle_check: Optional[Dict[str, Any]] = None  # Vehicle inspection data

# ==================== AUTH HELPERS ====================

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def create_token(user_id: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.utcnow().timestamp() + 86400 * 7  # 7 days
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = await db.users.find_one({"_id": ObjectId(payload["user_id"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if not user.get("is_active", True):
            raise HTTPException(status_code=401, detail="User account is deactivated")
        return str_id(user)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_company_filter(user: dict) -> dict:
    """Get a MongoDB filter to scope data by the user's company_id.
    Returns a filter that matches nothing if user has no company_id (prevents data leakage)."""
    company_id = user.get("company_id")
    if company_id:
        return {"company_id": company_id}
    # Prevent data leakage: no company_id means match nothing
    return {"company_id": {"$exists": True, "$eq": "__no_company__"}}

def verify_company_ownership(resource: dict, current_user: dict):
    """Verify the resource belongs to the same company as the user. Raises 403 if not."""
    user_company = current_user.get("company_id")
    resource_company = resource.get("company_id")
    if not user_company:
        raise HTTPException(status_code=403, detail="No company assigned to your account")
    if resource_company and resource_company != user_company:
        raise HTTPException(status_code=403, detail="Access denied: resource belongs to another company")

# ==================== AUTH ENDPOINTS ====================

@api_router.post("/auth/register", response_model=UserResponse)
async def register_user(user: UserCreate):
    # Check if phone already exists
    existing = await db.users.find_one({"phone": user.phone})
    if existing:
        raise HTTPException(status_code=400, detail="Phone number already registered")
    
    user_doc = {
        "name": user.name,
        "phone": user.phone,
        "pin_hash": hash_pin(user.pin),
        "role": user.role,
        "is_active": True,
        "company_id": user.company_id,
        "created_at": datetime.utcnow()
    }
    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return str_id(user_doc)

@api_router.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    # Trim whitespace from phone number
    phone = req.phone.strip()
    
    logger.info(f"Login attempt for phone: {phone}")
    
    user = await db.users.find_one({"phone": phone})
    if not user:
        logger.warning(f"User not found for phone: {phone}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not user.get("is_active", True):
        logger.warning(f"Inactive account for phone: {phone}")
        raise HTTPException(status_code=401, detail="Account is deactivated")
    
    if user["pin_hash"] != hash_pin(req.pin):
        logger.warning(f"Invalid PIN for phone: {phone}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_token(str(user["_id"]), user["role"])
    
    # Include company info in response
    company_info = None
    if user.get("company_id"):
        try:
            company = await db.companies.find_one({"_id": ObjectId(user["company_id"])})
            if company:
                company_info = str_id(company)
        except Exception as e:
            logger.error(f"Error fetching company: {e}")
    
    logger.info(f"Login successful for phone: {phone}, role: {user.get('role')}")
    
    return {
        "token": token,
        "user": str_id(user),
        "company": company_info
    }

# ==================== COMPANY SETUP ====================

@api_router.post("/companies/setup")
async def setup_company(setup: CompanySetup):
    """Register a new company with its admin user - clean slate with no pre-loaded data"""
    # Check if admin phone is already registered
    existing = await db.users.find_one({"phone": setup.admin_phone})
    if existing:
        raise HTTPException(status_code=400, detail="This phone number is already registered")
    
    # Create company
    company_doc = {
        "name": setup.company.name,
        "contact_person": setup.company.contact_person,
        "phone": setup.company.phone,
        "email": setup.company.email,
        "address": setup.company.address,
        "created_at": datetime.utcnow(),
    }
    result = await db.companies.insert_one(company_doc)
    company_id = str(result.inserted_id)
    
    # Create admin user for the company
    admin_doc = {
        "name": setup.admin_name,
        "phone": setup.admin_phone,
        "pin_hash": hash_pin(setup.admin_pin),
        "role": "admin",
        "is_active": True,
        "company_id": company_id,
        "created_at": datetime.utcnow(),
    }
    await db.users.insert_one(admin_doc)
    
    return {
        "message": "Company registered successfully",
        "company_id": company_id,
        "company_name": setup.company.name,
        "admin_phone": setup.admin_phone,
    }

@api_router.get("/companies/mine", response_model=CompanyResponse)
async def get_my_company(current_user: dict = Depends(get_current_user)):
    """Get the current user's company details"""
    company_id = current_user.get("company_id")
    if not company_id:
        raise HTTPException(status_code=404, detail="No company associated with this user")
    
    company = await db.companies.find_one({"_id": ObjectId(company_id)})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    return str_id(company)

@api_router.put("/companies/mine")
async def update_my_company(data: CompanyCreate, current_user: dict = Depends(get_current_user)):
    """Update the current user's company details"""
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    company_id = current_user.get("company_id")
    if not company_id:
        raise HTTPException(status_code=404, detail="No company associated")
    
    update_data = {k: v for k, v in data.dict().items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    
    await db.companies.update_one(
        {"_id": ObjectId(company_id)},
        {"$set": update_data}
    )
    
    company = await db.companies.find_one({"_id": ObjectId(company_id)})
    return str_id(company)

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

# ==================== USER MANAGEMENT (Admin Only) ====================

@api_router.get("/users", response_model=List[UserResponse])
async def get_users(current_user: dict = Depends(get_current_user)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Show only users from the same company
    query = get_company_filter(current_user)
    query["pin_hash"] = {"$exists": True}  # ensure it's a real user
    users = await db.users.find(query, {'pin_hash': 0}).to_list(500)
    return [str_id(u) for u in users]

@api_router.post("/users", response_model=UserResponse)
async def create_user(user: UserCreate, current_user: dict = Depends(get_current_user)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    existing = await db.users.find_one({"phone": user.phone})
    if existing:
        raise HTTPException(status_code=400, detail="Phone number already registered")
    
    user_doc = {
        "name": user.name,
        "phone": user.phone,
        "pin_hash": hash_pin(user.pin),
        "role": user.role,
        "is_active": True,
        "company_id": current_user.get("company_id"),
        "created_at": datetime.utcnow()
    }
    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return str_id(user_doc)

@api_router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: str, update: UserUpdate, current_user: dict = Depends(get_current_user)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    if update_data:
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update_data})
    
    updated = await db.users.find_one({"_id": ObjectId(user_id)})
    return str_id(updated)

@api_router.put("/users/{user_id}/reset-pin")
async def reset_user_pin(user_id: str, new_pin: str, current_user: dict = Depends(get_current_user)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    await db.users.update_one(
        {"_id": ObjectId(user_id)}, 
        {"$set": {"pin_hash": hash_pin(new_pin)}}
    )
    return {"message": "PIN reset successfully"}

@api_router.delete("/users/{user_id}")
async def deactivate_user(user_id: str, current_user: dict = Depends(get_current_user)):
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_active": False}})
    return {"message": "User deactivated successfully"}

# ==================== PRODUCT ENDPOINTS ====================

@api_router.get("/products", response_model=List[ProductResponse])
async def get_products(current_user: dict = Depends(get_current_user)):
    query = get_company_filter(current_user)
    products = await db.products.find(query).to_list(100)
    return [str_id(p) for p in products]

@api_router.post("/products", response_model=ProductResponse)
async def create_product(product: ProductCreate, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    product_doc = product.dict()
    product_doc["company_id"] = current_user.get("company_id")
    result = await db.products.insert_one(product_doc)
    product_doc["_id"] = result.inserted_id
    return str_id(product_doc)

@api_router.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(product_id: str, product: ProductUpdate, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    existing = await db.products.find_one({"_id": ObjectId(product_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found")
    verify_company_ownership(existing, current_user)
    
    update_data = {k: v for k, v in product.dict().items() if v is not None}
    if update_data:
        await db.products.update_one({"_id": ObjectId(product_id)}, {"$set": update_data})
    
    updated = await db.products.find_one({"_id": ObjectId(product_id)})
    return str_id(updated)

@api_router.delete("/products/{product_id}")
async def delete_product(product_id: str, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    existing = await db.products.find_one({"_id": ObjectId(product_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found")
    verify_company_ownership(existing, current_user)
    
    await db.products.delete_one({"_id": ObjectId(product_id)})
    return {"message": "Product deleted successfully"}

@api_router.post("/products/seed")
async def seed_products():
    """Seed default products"""
    default_products = [
        {"name": "White Bread", "category": "Bread", "unit_type": "units", "price": 18.00},
        {"name": "Brown Bread", "category": "Bread", "unit_type": "units", "price": 20.00},
        {"name": "Maas (500ml)", "category": "Dairy", "unit_type": "units", "price": 15.00},
        {"name": "Maas (1L)", "category": "Dairy", "unit_type": "units", "price": 25.00},
        {"name": "Mahewu (500ml)", "category": "Dairy", "unit_type": "units", "price": 12.00},
        {"name": "Mahewu (1L)", "category": "Dairy", "unit_type": "units", "price": 20.00},
        {"name": "Eggs (6 pack)", "category": "Eggs", "unit_type": "packs", "price": 35.00},
        {"name": "Eggs (12 pack)", "category": "Eggs", "unit_type": "packs", "price": 65.00},
        {"name": "Eggs (30 tray)", "category": "Eggs", "unit_type": "trays", "price": 150.00},
    ]
    
    await db.products.delete_many({})
    result = await db.products.insert_many(default_products)
    return {"message": f"Seeded {len(result.inserted_ids)} products"}

# ==================== VEHICLE ENDPOINTS ====================

@api_router.get("/vehicles", response_model=List[VehicleResponse])
async def get_vehicles(include_inactive: bool = False, current_user: dict = Depends(get_current_user)):
    query = get_company_filter(current_user)
    if not include_inactive:
        query["is_active"] = {"$ne": False}
    vehicles = await db.vehicles.find(query).to_list(100)
    return [str_id(v) for v in vehicles]

@api_router.get("/vehicles/available")
async def get_available_vehicles(current_user: dict = Depends(get_current_user)):
    """Get vehicles not currently in use on an active route"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    # Get all vehicles for this company
    query = get_company_filter(current_user)
    query["is_active"] = {"$ne": False}
    all_vehicles = await db.vehicles.find(query).to_list(100)
    
    # Get vehicles currently in use
    active_routes = await db.daily_routes.find({
        "date": today,
        "status": "active"
    }).to_list(100)
    
    in_use_vehicle_ids = {r.get("vehicle_id") for r in active_routes if r.get("vehicle_id")}
    
    available = []
    for v in all_vehicles:
        v = str_id(v)
        v["in_use"] = v["id"] in in_use_vehicle_ids
        available.append(v)
    
    return available

@api_router.post("/vehicles", response_model=VehicleResponse)
async def create_vehicle(vehicle: VehicleCreate, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    # Check if registration already exists
    existing = await db.vehicles.find_one({"registration": vehicle.registration})
    if existing:
        raise HTTPException(status_code=400, detail="Vehicle with this registration already exists")
    
    vehicle_doc = vehicle.dict()
    vehicle_doc["is_active"] = True
    vehicle_doc["company_id"] = current_user.get("company_id")
    vehicle_doc["created_at"] = datetime.utcnow()
    
    result = await db.vehicles.insert_one(vehicle_doc)
    vehicle_doc["_id"] = result.inserted_id
    return str_id(vehicle_doc)

@api_router.put("/vehicles/{vehicle_id}", response_model=VehicleResponse)
async def update_vehicle(vehicle_id: str, update: VehicleUpdate, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    vehicle = await db.vehicles.find_one({"_id": ObjectId(vehicle_id)})
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    verify_company_ownership(vehicle, current_user)
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    if update_data:
        await db.vehicles.update_one({"_id": ObjectId(vehicle_id)}, {"$set": update_data})
    
    updated = await db.vehicles.find_one({"_id": ObjectId(vehicle_id)})
    return str_id(updated)

@api_router.delete("/vehicles/{vehicle_id}")
async def deactivate_vehicle(vehicle_id: str, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    vehicle = await db.vehicles.find_one({"_id": ObjectId(vehicle_id)})
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    verify_company_ownership(vehicle, current_user)
    
    await db.vehicles.update_one({"_id": ObjectId(vehicle_id)}, {"$set": {"is_active": False}})
    return {"message": "Vehicle deactivated successfully"}

@api_router.post("/vehicles/seed")
async def seed_vehicles():
    """Seed sample vehicles"""
    sample_vehicles = [
        {"registration": "CA 123-456", "name": "Truck 1", "vehicle_type": "truck", "capacity_crates": 150},
        {"registration": "CA 234-567", "name": "Truck 2", "vehicle_type": "truck", "capacity_crates": 150},
        {"registration": "CA 345-678", "name": "Van A", "vehicle_type": "van", "capacity_crates": 80},
        {"registration": "CA 456-789", "name": "Bakkie 1", "vehicle_type": "bakkie", "capacity_crates": 50},
    ]
    
    for v in sample_vehicles:
        v["is_active"] = True
        v["created_at"] = datetime.utcnow()
    
    await db.vehicles.delete_many({})
    result = await db.vehicles.insert_many(sample_vehicles)
    return {"message": f"Seeded {len(result.inserted_ids)} vehicles"}

# ==================== CUSTOMER ENDPOINTS ====================

@api_router.get("/customers", response_model=List[CustomerResponse])
async def get_customers(route_id: Optional[str] = None, include_inactive: bool = False, current_user: dict = Depends(get_current_user)):
    company_id = current_user.get("company_id", "")
    
    if is_customer(current_user):
        # Customers can only see themselves
        query = {"_id": ObjectId(current_user["id"])}
    else:
        # Show company's own customers PLUS marketplace customers (empty company_id)
        query = {"$or": [
            {"company_id": company_id},
            {"company_id": {"$in": ["", None]}},
        ]}
    
    if route_id:
        query["route_id"] = route_id
    if not include_inactive:
        query["is_active"] = {"$ne": False}
    customers = await db.customers.find(query).to_list(500)
    return [str_id(c) for c in customers]

@api_router.get("/customers/{customer_id}", response_model=CustomerResponse)
async def get_customer(customer_id: str, current_user: dict = Depends(get_current_user)):
    customer = await db.customers.find_one({"_id": ObjectId(customer_id)})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return str_id(customer)

@api_router.post("/customers", response_model=CustomerResponse)
async def create_customer(customer: CustomerCreate, current_user: dict = Depends(get_current_user)):
    customer_doc = customer.dict()
    customer_doc["is_active"] = True
    customer_doc["balance"] = 0.0
    customer_doc["created_by"] = current_user["id"]
    customer_doc["company_id"] = current_user.get("company_id")
    customer_doc["created_at"] = datetime.utcnow()
    
    result = await db.customers.insert_one(customer_doc)
    customer_doc["_id"] = result.inserted_id
    return str_id(customer_doc)

@api_router.put("/customers/{customer_id}", response_model=CustomerResponse)
async def update_customer(customer_id: str, update: CustomerUpdate, current_user: dict = Depends(get_current_user)):
    # Only admin/manager can update customer details (except drivers can't change payment terms or credit limit)
    customer = await db.customers.find_one({"_id": ObjectId(customer_id)})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    verify_company_ownership(customer, current_user)
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    
    # Drivers can only update basic info, not payment terms or credit
    if not is_admin_or_manager(current_user):
        restricted_fields = ['payment_terms', 'credit_limit', 'is_active', 'route_id']
        for field in restricted_fields:
            if field in update_data:
                raise HTTPException(status_code=403, detail=f"Cannot update {field} - Admin/Manager access required")
    
    if update_data:
        update_data["updated_by"] = current_user["id"]
        update_data["updated_at"] = datetime.utcnow()
        await db.customers.update_one({"_id": ObjectId(customer_id)}, {"$set": update_data})
    
    updated = await db.customers.find_one({"_id": ObjectId(customer_id)})
    return str_id(updated)

@api_router.delete("/customers/{customer_id}")
async def deactivate_customer(customer_id: str, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    customer = await db.customers.find_one({"_id": ObjectId(customer_id)})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    verify_company_ownership(customer, current_user)
    
    await db.customers.update_one({"_id": ObjectId(customer_id)}, {"$set": {"is_active": False}})
    return {"message": "Customer deactivated successfully"}

@api_router.get("/customers/{customer_id}/history")
async def get_customer_history(customer_id: str, days: int = 30):
    """Get customer purchase history"""
    customer = await db.customers.find_one({"_id": ObjectId(customer_id)})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    start_date = datetime.utcnow() - timedelta(days=days)
    sales = await db.sales.find({
        "customer_id": customer_id,
        "created_at": {"$gte": start_date},
        "is_voided": {"$ne": True}
    }).sort("created_at", -1).to_list(100)
    
    total_purchases = sum(s.get("total_amount", 0) for s in sales)
    total_paid = sum(s.get("cash_collected", 0) for s in sales)
    
    return {
        "customer": str_id(customer),
        "period_days": days,
        "total_purchases": total_purchases,
        "total_paid": total_paid,
        "balance": total_purchases - total_paid,
        "transaction_count": len(sales),
        "transactions": [str_id(s) for s in sales]
    }

@api_router.post("/customers/seed")
async def seed_customers():
    """Seed sample customers"""
    routes = await db.routes.find().to_list(10)
    if not routes:
        return {"message": "Please seed routes first"}
    
    sample_customers = [
        {"name": "Shoprite Soweto", "contact": "011-555-0101", "location": "Soweto Mall", "payment_terms": "credit", "credit_limit": 5000},
        {"name": "Pick n Pay Diepkloof", "contact": "011-555-0102", "location": "Diepkloof Square", "payment_terms": "credit", "credit_limit": 10000},
        {"name": "Spaza Shop - Mama Joy", "contact": "078-555-0103", "location": "Orlando West", "payment_terms": "cash"},
        {"name": "Corner Cafe Meadowlands", "contact": "079-555-0104", "location": "Meadowlands Zone 1", "payment_terms": "cash"},
        {"name": "Spar Alexandra", "contact": "011-555-0105", "location": "Alex Mall", "payment_terms": "credit", "credit_limit": 8000},
        {"name": "Tuckshop - Mr Dlamini", "contact": "082-555-0106", "location": "Tembisa", "payment_terms": "cash"},
        {"name": "OK Foods Randburg", "contact": "011-555-0107", "location": "Randburg CBD", "payment_terms": "credit", "credit_limit": 15000},
        {"name": "Kasi Superette", "contact": "083-555-0108", "location": "Katlehong", "payment_terms": "mixed", "credit_limit": 3000},
    ]
    
    for i, customer in enumerate(sample_customers):
        customer["route_id"] = str(routes[i % len(routes)]["_id"])
        customer["is_active"] = True
        customer["balance"] = 0.0
        customer["created_at"] = datetime.utcnow()
    
    await db.customers.delete_many({})
    result = await db.customers.insert_many(sample_customers)
    return {"message": f"Seeded {len(result.inserted_ids)} customers"}

# ==================== ROUTE ENDPOINTS ====================

@api_router.get("/routes", response_model=List[RouteResponse])
async def get_routes(current_user: dict = Depends(get_current_user)):
    query = get_company_filter(current_user)
    routes = await db.routes.find(query).to_list(50)
    result = []
    for route in routes:
        route = str_id(route)
        customer_count = await db.customers.count_documents({"route_id": route["id"], "is_active": {"$ne": False}})
        route["customer_count"] = customer_count
        result.append(route)
    return result

@api_router.post("/routes", response_model=RouteResponse)
async def create_route(route: RouteCreate, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    route_doc = route.dict()
    route_doc["assigned_driver_id"] = None
    route_doc["assigned_driver_name"] = None
    route_doc["company_id"] = current_user.get("company_id")
    route_doc["created_at"] = datetime.utcnow()
    
    result = await db.routes.insert_one(route_doc)
    route_doc["_id"] = result.inserted_id
    route_doc["customer_count"] = 0
    return str_id(route_doc)

@api_router.put("/routes/{route_id}", response_model=RouteResponse)
async def update_route(route_id: str, update: RouteUpdate, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    route = await db.routes.find_one({"_id": ObjectId(route_id)})
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    verify_company_ownership(route, current_user)
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    
    # If assigning a driver, get their name
    if "assigned_driver_id" in update_data and update_data["assigned_driver_id"]:
        driver = await db.users.find_one({"_id": ObjectId(update_data["assigned_driver_id"])})
        if driver:
            update_data["assigned_driver_name"] = driver["name"]
    
    if update_data:
        await db.routes.update_one({"_id": ObjectId(route_id)}, {"$set": update_data})
    
    updated = await db.routes.find_one({"_id": ObjectId(route_id)})
    updated = str_id(updated)
    updated["customer_count"] = await db.customers.count_documents({"route_id": route_id, "is_active": {"$ne": False}})
    return updated

@api_router.delete("/routes/{route_id}")
async def delete_route(route_id: str, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    route = await db.routes.find_one({"_id": ObjectId(route_id)})
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    verify_company_ownership(route, current_user)
    
    # Check if route has customers
    customer_count = await db.customers.count_documents({"route_id": route_id})
    if customer_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete route with {customer_count} customers. Reassign customers first.")
    
    await db.routes.delete_one({"_id": ObjectId(route_id)})
    return {"message": "Route deleted successfully"}

@api_router.get("/routes/{route_id}/customers", response_model=List[CustomerResponse])
async def get_route_customers(route_id: str):
    customers = await db.customers.find({"route_id": route_id, "is_active": {"$ne": False}}).to_list(500)
    return [str_id(c) for c in customers]

@api_router.post("/routes/seed")
async def seed_routes():
    """Seed sample routes"""
    sample_routes = [
        {"name": "Soweto North", "description": "Covers Meadowlands, Orlando, Diepkloof areas"},
        {"name": "Soweto South", "description": "Covers Dobsonville, Protea Glen, Lenasia areas"},
        {"name": "Alexandra Route", "description": "Alexandra township and surrounds"},
        {"name": "East Rand", "description": "Tembisa, Katlehong, Vosloorus areas"},
    ]
    
    for route in sample_routes:
        route["assigned_driver_id"] = None
        route["assigned_driver_name"] = None
        route["created_at"] = datetime.utcnow()
    
    await db.routes.delete_many({})
    result = await db.routes.insert_many(sample_routes)
    return {"message": f"Seeded {len(result.inserted_ids)} routes"}

# ==================== SALES ENDPOINTS ====================

@api_router.post("/sales", response_model=SaleResponse)
async def create_sale(sale: SaleCreate, current_user: dict = Depends(get_current_user)):
    # Calculate total
    total = sum(
        (item.quantity_delivered - item.quantity_returned) * item.unit_price 
        for item in sale.items
    )
    
    # Calculate shortage (Invoice Total - Cash Collected)
    shortage_amount = max(0, total - sale.cash_collected)
    
    # Generate automatic invoice number: INV-YYYYMMDD-ROUTE-####
    today = datetime.utcnow()
    today_str = today.strftime("%Y%m%d")
    
    # Get route code (first 4 chars of route name or route_id)
    route = await db.routes.find_one({"_id": ObjectId(sale.route_id)})
    route_code = route["name"][:4].upper().replace(" ", "") if route else sale.route_id[:4].upper()
    
    # Count sales for today to generate sequence number
    start_of_day = today.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = today.replace(hour=23, minute=59, second=59, microsecond=999999)
    daily_sales_count = await db.sales.count_documents({
        "created_at": {"$gte": start_of_day, "$lte": end_of_day}
    })
    sequence_num = daily_sales_count + 1
    
    # Format: INV-YYYYMMDD-ROUTE-0001
    invoice_number = f"INV-{today_str}-{route_code}-{sequence_num:04d}"
    
    # ===== VEHICLE STOCK ENFORCEMENT =====
    # Drivers can only sell items that have been loaded onto their vehicle
    today_date_str_vs = today.strftime("%Y-%m-%d")
    vehicle_stock_records = await db.vehicle_stock.find({
        "driver_id": current_user["id"],
        "date": today_date_str_vs,
        "status": "active"
    }).to_list(500)
    
    if vehicle_stock_records:
        # Vehicle stock dispatch exists — enforce limits
        vs_lookup = {vs["product_id"]: vs.get("quantity_remaining", 0) for vs in vehicle_stock_records}
        for item in sale.items:
            net_sold = item.quantity_delivered - item.quantity_returned
            if net_sold > 0:
                available_on_vehicle = vs_lookup.get(item.product_id, 0)
                if available_on_vehicle <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{item.product_name} has not been loaded onto your vehicle. Contact admin to dispatch stock."
                    )
                if net_sold > available_on_vehicle:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient vehicle stock for {item.product_name}. Loaded: {available_on_vehicle}, Trying to sell: {net_sold}"
                    )
    
    sale_doc = {
        "invoice_number": invoice_number,
        "route_id": sale.route_id,
        "route_name": route["name"] if route else "Unknown",
        "customer_id": sale.customer_id,
        "customer_name": sale.customer_name,
        "driver_id": current_user["id"],
        "driver_name": current_user["name"],
        "items": [item.dict() for item in sale.items],
        "total_amount": total,
        "crates_dropped": sale.crates_dropped,
        "crates_collected": sale.crates_collected,
        "cash_collected": sale.cash_collected,
        "shortage_amount": shortage_amount,
        "payment_type": sale.payment_type,
        "split_payments": [sp.dict() for sp in sale.split_payments] if sale.split_payments else None,
        "delivery_status": sale.delivery_status,
        "notes": sale.notes,
        "is_voided": False,
        "company_id": current_user.get("company_id"),
        "created_at": datetime.utcnow()
    }
    
    result = await db.sales.insert_one(sale_doc)
    sale_doc["_id"] = result.inserted_id
    
    # Update daily route totals including crates and shortage tracking
    today_date_str = datetime.utcnow().strftime("%Y-%m-%d")
    await db.daily_routes.update_one(
        {"driver_id": current_user["id"], "date": today_date_str, "status": "active"},
        {"$inc": {
            "sales_count": 1, 
            "total_collected": sale.cash_collected,
            "total_expected": total,
            "total_shortage": shortage_amount,
            "total_crates_dropped": sale.crates_dropped,
            "total_crates_collected": sale.crates_collected
        }}
    )
    
    # Update customer balance if credit
    if sale.payment_type in ["credit", "mixed"]:
        balance_change = total - sale.cash_collected
        if balance_change > 0:
            await db.customers.update_one(
                {"_id": ObjectId(sale.customer_id)},
                {"$inc": {"balance": balance_change}}
            )
    
    # Deduct stock for each item sold (company-scoped)
    company_id = current_user.get("company_id", "")
    for item in sale.items:
        net_sold = item.quantity_delivered - item.quantity_returned
        if net_sold > 0:
            # Reduce stock (company-scoped)
            await db.stock.update_one(
                {"product_id": item.product_id, "company_id": company_id},
                {"$inc": {"quantity": -net_sold}}
            )
            # Also deduct from vehicle stock if dispatch exists
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            await db.vehicle_stock.update_one(
                {
                    "driver_id": current_user["id"],
                    "product_id": item.product_id,
                    "date": today_str,
                    "status": "active"
                },
                {"$inc": {"quantity_sold": net_sold, "quantity_remaining": -net_sold}}
            )
            # Log the movement
            await db.stock_movements.insert_one({
                "movement_type": "sale",
                "product_id": item.product_id,
                "product_name": item.product_name,
                "quantity": -net_sold,
                "sale_id": str(sale_doc["_id"]),
                "invoice_number": invoice_number,
                "customer_name": sale.customer_name,
                "driver_id": current_user["id"],
                "driver_name": current_user["name"],
                "company_id": company_id,
                "created_at": datetime.utcnow()
            })
    
    return str_id(sale_doc)

@api_router.get("/sales", response_model=List[SaleResponse])
async def get_sales(
    route_id: Optional[str] = None,
    date_str: Optional[str] = None,
    customer_id: Optional[str] = None,
    include_voided: bool = False,
    current_user: dict = Depends(get_current_user)
):
    query = get_company_filter(current_user)
    if current_user["role"] == "driver":
        query["driver_id"] = current_user["id"]
    if route_id:
        query["route_id"] = route_id
    if customer_id:
        query["customer_id"] = customer_id
    if date_str:
        start = datetime.strptime(date_str, "%Y-%m-%d")
        end = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        query["created_at"] = {"$gte": start, "$lte": end}
    if not include_voided:
        query["is_voided"] = {"$ne": True}
    
    sales = await db.sales.find(query).sort("created_at", -1).to_list(500)
    return [str_id(s) for s in sales]

@api_router.get("/sales/{sale_id}", response_model=SaleResponse)
async def get_sale(sale_id: str, current_user: dict = Depends(get_current_user)):
    sale = await db.sales.find_one({"_id": ObjectId(sale_id)})
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    
    # Drivers can only view their own sales
    if current_user["role"] == "driver" and sale["driver_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return str_id(sale)

@api_router.put("/sales/{sale_id}", response_model=SaleResponse)
async def update_sale(sale_id: str, update: SaleUpdate, current_user: dict = Depends(get_current_user)):
    sale = await db.sales.find_one({"_id": ObjectId(sale_id)})
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    
    # Check permissions
    is_same_day = sale["created_at"].date() == datetime.utcnow().date()
    is_own_sale = sale["driver_id"] == current_user["id"]
    
    # Drivers can only edit their own same-day sales
    if current_user["role"] == "driver":
        if not is_own_sale:
            raise HTTPException(status_code=403, detail="Cannot edit other driver's sales")
        if not is_same_day:
            raise HTTPException(status_code=403, detail="Cannot edit past sales - contact manager")
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    
    # Recalculate total if items changed
    if "items" in update_data:
        total = sum(
            (item["quantity_delivered"] - item.get("quantity_returned", 0)) * item["unit_price"]
            for item in update_data["items"]
        )
        update_data["total_amount"] = total
    
    update_data["updated_by"] = current_user["id"]
    update_data["updated_at"] = datetime.utcnow()
    
    await db.sales.update_one({"_id": ObjectId(sale_id)}, {"$set": update_data})
    
    updated = await db.sales.find_one({"_id": ObjectId(sale_id)})
    return str_id(updated)

@api_router.post("/sales/{sale_id}/void")
async def void_sale(sale_id: str, reason: str, current_user: dict = Depends(get_current_user)):
    sale = await db.sales.find_one({"_id": ObjectId(sale_id)})
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    
    if sale.get("is_voided"):
        raise HTTPException(status_code=400, detail="Sale is already voided")
    
    # Check permissions
    is_same_day = sale["created_at"].date() == datetime.utcnow().date()
    is_own_sale = sale["driver_id"] == current_user["id"]
    
    # Drivers can only void their own same-day sales
    if current_user["role"] == "driver":
        if not is_own_sale:
            raise HTTPException(status_code=403, detail="Cannot void other driver's sales")
        if not is_same_day:
            raise HTTPException(status_code=403, detail="Cannot void past sales - contact manager")
    
    await db.sales.update_one(
        {"_id": ObjectId(sale_id)},
        {"$set": {
            "is_voided": True,
            "void_reason": reason,
            "voided_by": current_user["id"],
            "voided_at": datetime.utcnow()
        }}
    )
    
    # Reverse customer balance if was credit
    if sale.get("payment_type") in ["credit", "mixed"]:
        balance_change = sale["total_amount"] - sale["cash_collected"]
        if balance_change > 0:
            await db.customers.update_one(
                {"_id": ObjectId(sale["customer_id"])},
                {"$inc": {"balance": -balance_change}}
            )
    
    return {"message": "Sale voided successfully"}

@api_router.get("/sales/customer/{customer_id}")
async def get_customer_sales(customer_id: str):
    """Get sales history for a specific customer"""
    sales = await db.sales.find({
        "customer_id": customer_id,
        "is_voided": {"$ne": True}
    }).sort("created_at", -1).to_list(100)
    return [str_id(s) for s in sales]

# ==================== DAILY ROUTE ENDPOINTS ====================

@api_router.post("/daily-routes/create-pending")
async def create_pending_daily_route(data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """Create a pending daily route (admin creates before dispatch)"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    route_id = data.get("route_id")
    vehicle_id = data.get("vehicle_id")
    driver_id = data.get("driver_id")
    
    if not route_id or not vehicle_id:
        raise HTTPException(status_code=400, detail="route_id and vehicle_id are required")
    
    today = datetime.utcnow().strftime("%Y-%m-%d")
    company_id = current_user.get("company_id", "")
    
    # Check if route already has a pending/dispatched daily route today
    existing = await db.daily_routes.find_one({
        "route_id": route_id,
        "date": today,
        "status": {"$in": ["pending", "dispatched", "active"]},
        "company_id": company_id
    })
    if existing:
        raise HTTPException(status_code=400, detail="This route already has a pending or active daily route today")
    
    # Get route and vehicle info
    route = await db.routes.find_one({"_id": ObjectId(route_id)})
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    
    vehicle = await db.vehicles.find_one({"_id": ObjectId(vehicle_id)})
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Get driver info if provided
    driver_name = None
    if driver_id:
        driver = await db.users.find_one({"_id": ObjectId(driver_id)})
        if driver:
            driver_name = driver.get("name", "")
    
    daily_route = {
        "route_id": route_id,
        "route_name": route["name"],
        "vehicle_id": vehicle_id,
        "vehicle_name": vehicle["name"],
        "vehicle_registration": vehicle["registration"],
        "driver_id": driver_id,
        "driver_name": driver_name,
        "date": today,
        "status": "pending",
        "company_id": company_id,
        "created_by": current_user["id"],
        "created_at": datetime.utcnow()
    }
    
    result = await db.daily_routes.insert_one(daily_route)
    daily_route["_id"] = result.inserted_id
    
    return str_id(daily_route)

@api_router.get("/daily-routes/pending")
async def get_pending_daily_routes(current_user: dict = Depends(get_current_user)):
    """Get routes that admin has dispatched (status='dispatched') ready for driver to start"""
    if current_user.get("role") == "driver":
        # Driver sees only dispatched routes assigned to them or unassigned
        today = datetime.utcnow().strftime("%Y-%m-%d")
        query = {
            "date": today,
            "status": "dispatched",
            "$or": [
                {"driver_id": current_user["id"]},
                {"driver_id": None},
                {"driver_id": ""}
            ]
        }
        routes = await db.daily_routes.find(query).to_list(100)
        return [str_id(r) for r in routes]
    else:
        # Admin sees pending and dispatched routes
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cf = get_company_filter(current_user)
        query = {
            "date": today,
            "status": {"$in": ["pending", "dispatched"]},
            **cf
        }
        routes = await db.daily_routes.find(query).to_list(100)
        return [str_id(r) for r in routes]

@api_router.put("/daily-routes/{daily_route_id}/mark-dispatched")
async def mark_route_dispatched(daily_route_id: str, current_user: dict = Depends(get_current_user)):
    """Mark route as dispatched after stock loaded - ready for driver to start"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(daily_route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    if daily_route.get("status") not in ["pending"]:
        raise HTTPException(status_code=400, detail="Can only mark pending routes as dispatched")
    
    await db.daily_routes.update_one(
        {"_id": ObjectId(daily_route_id)},
        {"$set": {
            "status": "dispatched",
            "dispatched_by": current_user["id"],
            "dispatched_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }}
    )
    
    updated = await db.daily_routes.find_one({"_id": ObjectId(daily_route_id)})
    return str_id(updated)

@api_router.post("/daily-routes/start", response_model=DailyRouteResponse)
async def start_daily_route(data: DailyRouteStart, current_user: dict = Depends(get_current_user)):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    company_id = current_user.get("company_id", "")
    
    # Check if THIS SPECIFIC ROUTE is already active today for this driver
    existing = await db.daily_routes.find_one({
        "driver_id": current_user["id"],
        "route_id": data.route_id,
        "date": today,
        "status": "active"
    })
    if existing:
        raise HTTPException(status_code=400, detail="This route is already active today")
    
    # Check if this route is already active today (within the same company)
    route_already_active = await db.daily_routes.find_one({
        "route_id": data.route_id,
        "date": today,
        "status": "active",
        "company_id": company_id
    })
    if route_already_active:
        raise HTTPException(status_code=400, detail=f"This route is already active today. End the current run first.")
    
    # Check vehicle availability (within the same company)
    vehicle_in_use = await db.daily_routes.find_one({
        "vehicle_id": data.vehicle_id,
        "date": today,
        "status": "active",
        "company_id": company_id
    })
    if vehicle_in_use:
        raise HTTPException(status_code=400, detail="This vehicle is already in use on another route today")
    
    # Get route name
    route = await db.routes.find_one({"_id": ObjectId(data.route_id)})
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    
    # Get vehicle info
    vehicle = await db.vehicles.find_one({"_id": ObjectId(data.vehicle_id)})
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    daily_route = {
        "route_id": data.route_id,
        "route_name": route["name"],
        "vehicle_id": data.vehicle_id,
        "vehicle_name": vehicle["name"],
        "vehicle_registration": vehicle["registration"],
        "driver_id": current_user["id"],
        "driver_name": current_user["name"],
        "date": today,
        "opening_km": data.opening_km,
        "closing_km": None,
        "km_traveled": None,
        "crates_out": data.crates_out,
        "crates_in": None,
        "damages_count": 0,
        "fuel_used": None,
        "vehicle_check": data.vehicle_check,
        "status": "active",
        "sales_count": 0,
        "total_collected": 0.0,
        "company_id": current_user.get("company_id"),
        "started_at": datetime.utcnow()
    }
    
    result = await db.daily_routes.insert_one(daily_route)
    daily_route["_id"] = result.inserted_id
    return str_id(daily_route)

@api_router.put("/daily-routes/{route_id}", response_model=DailyRouteResponse)
async def update_daily_route(route_id: str, update: DailyRouteUpdate, current_user: dict = Depends(get_current_user)):
    """Update daily route - Admin/Manager can update any, Driver can update own same-day"""
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    # Check permissions
    is_own_route = daily_route["driver_id"] == current_user["id"]
    today = datetime.utcnow().strftime("%Y-%m-%d")
    is_same_day = daily_route["date"] == today
    
    if not is_admin_or_manager(current_user):
        if not is_own_route:
            raise HTTPException(status_code=403, detail="Cannot update other driver's route")
        if not is_same_day:
            raise HTTPException(status_code=403, detail="Cannot update past routes - contact manager")
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    
    # Recalculate km traveled if both values present
    if "closing_km" in update_data or "opening_km" in update_data:
        opening = update_data.get("opening_km", daily_route.get("opening_km"))
        closing = update_data.get("closing_km", daily_route.get("closing_km"))
        if closing and opening:
            update_data["km_traveled"] = closing - opening
    
    update_data["updated_by"] = current_user["id"]
    update_data["updated_at"] = datetime.utcnow()
    
    await db.daily_routes.update_one({"_id": ObjectId(route_id)}, {"$set": update_data})
    
    updated = await db.daily_routes.find_one({"_id": ObjectId(route_id)})
    return str_id(updated)

@api_router.put("/daily-routes/{route_id}/end", response_model=DailyRouteResponse)
async def end_daily_route(route_id: str, data: DailyRouteEnd, current_user: dict = Depends(get_current_user)):
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    if daily_route["driver_id"] != current_user["id"] and not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Not your route")
    
    km_traveled = data.closing_km - daily_route["opening_km"]
    
    update_data = {
        "closing_km": data.closing_km,
        "km_traveled": km_traveled,
        "crates_in": data.crates_in,
        "damages_count": data.damages_count,
        "fuel_used": data.fuel_used,
        "notes": data.notes,
        "status": "completed",
        "ended_at": datetime.utcnow()
    }
    
    await db.daily_routes.update_one({"_id": ObjectId(route_id)}, {"$set": update_data})
    
    daily_route.update(update_data)
    return str_id(daily_route)

@api_router.get("/daily-routes/active", response_model=List[DailyRouteResponse])
async def get_active_daily_routes(current_user: dict = Depends(get_current_user)):
    """Get all active routes for the current driver (supports multiple concurrent routes)"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    query = {
        "date": today,
        "status": "active"
    }
    
    # Drivers see only their own routes, admin/manager see their company's routes
    if current_user["role"] not in ["admin", "manager"]:
        query["driver_id"] = current_user["id"]
    else:
        cf = get_company_filter(current_user)
        query.update(cf)
    
    daily_routes = await db.daily_routes.find(query).to_list(50)
    return [str_id(dr) for dr in daily_routes]

@api_router.get("/daily-routes/active/all", response_model=List[DailyRouteResponse])
async def get_all_active_routes(current_user: dict = Depends(get_current_user)):
    """Get all active routes across all drivers (Admin/Manager view)"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    today = datetime.utcnow().strftime("%Y-%m-%d")
    query = {
        "date": today,
        "status": "active"
    }
    cf = get_company_filter(current_user)
    query.update(cf)
    daily_routes = await db.daily_routes.find(query).to_list(100)
    return [str_id(dr) for dr in daily_routes]

@api_router.get("/daily-routes/history", response_model=List[DailyRouteResponse])
async def get_daily_route_history(
    driver_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    query = get_company_filter(current_user)
    
    # Drivers can only see their own history, admin/manager can see all or filter
    if current_user["role"] == "driver":
        query["driver_id"] = current_user["id"]
    elif driver_id:
        query["driver_id"] = driver_id
    
    routes = await db.daily_routes.find(query).sort("date", -1).to_list(100)
    return [str_id(r) for r in routes]

@api_router.get("/daily-routes/{route_id}", response_model=DailyRouteResponse)
async def get_daily_route_by_id(route_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific daily route by ID"""
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    # Check permissions - drivers can only see their own routes
    if current_user["role"] == "driver" and daily_route["driver_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this route")
    
    return str_id(daily_route)

@api_router.delete("/daily-routes/{route_id}")
async def delete_daily_route(route_id: str, current_user: dict = Depends(get_current_user)):
    """Delete/cancel a daily route - Admin/Manager only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required to delete routes")
    
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    # Delete associated sales if any
    await db.sales.delete_many({"route_id": route_id})
    
    # Delete the route
    await db.daily_routes.delete_one({"_id": ObjectId(route_id)})
    
    return {"message": "Route deleted successfully", "deleted_sales": True}

# ==================== REPORTS ENDPOINTS ====================

@api_router.get("/reports")
async def get_all_reports(current_user: dict = Depends(get_current_user)):
    """Get all generated reports (company-scoped)"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    cf = get_company_filter(current_user)
    # Get daily routes as reports
    daily_routes = await db.daily_routes.find(cf).sort("date", -1).to_list(100)
    
    reports = []
    for dr in daily_routes:
        reports.append({
            "id": str(dr["_id"]),
            "type": "daily_route",
            "date": dr.get("date", ""),
            "route_name": dr.get("route_name", ""),
            "driver_name": dr.get("driver_name", ""),
            "status": dr.get("status", ""),
            "sales_count": dr.get("sales_count", 0),
            "total_collected": dr.get("total_collected", 0),
            "created_at": dr.get("started_at", dr.get("created_at"))
        })
    
    return reports

@api_router.get("/reports/daily-summary")
async def get_daily_summary(date_str: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    if not date_str:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    query = {"date": date_str}
    cf = get_company_filter(current_user)
    query.update(cf)
    if current_user["role"] == "driver":
        query["driver_id"] = current_user["id"]
    
    daily_routes = await db.daily_routes.find(query).to_list(100)
    
    # Get sales for the day (company-scoped)
    start = datetime.strptime(date_str, "%Y-%m-%d")
    end = start.replace(hour=23, minute=59, second=59)
    
    sales_query = {"created_at": {"$gte": start, "$lte": end}, "is_voided": {"$ne": True}}
    sales_query.update(cf)
    if current_user["role"] == "driver":
        sales_query["driver_id"] = current_user["id"]
    
    sales = await db.sales.find(sales_query).to_list(1000)
    
    # Aggregate product sales
    product_totals = {}
    for sale in sales:
        for item in sale.get("items", []):
            prod_name = item.get("product_name", "Unknown")
            if prod_name not in product_totals:
                product_totals[prod_name] = {"delivered": 0, "returned": 0, "damages": 0, "revenue": 0}
            product_totals[prod_name]["delivered"] += item.get("quantity_delivered", 0)
            product_totals[prod_name]["returned"] += item.get("quantity_returned", 0)
            product_totals[prod_name]["damages"] += item.get("damages", 0)
            product_totals[prod_name]["revenue"] += (item.get("quantity_delivered", 0) - item.get("quantity_returned", 0)) * item.get("unit_price", 0)
    
    total_collected = sum(s.get("cash_collected", 0) for s in sales)
    total_expected = sum(s.get("total_amount", 0) for s in sales)
    total_km = sum(dr.get("km_traveled", 0) or 0 for dr in daily_routes)
    
    # Delivery status breakdown
    delivery_status = {
        "delivered": len([s for s in sales if s.get("delivery_status") == "delivered"]),
        "partial": len([s for s in sales if s.get("delivery_status") == "partial"]),
        "skipped": len([s for s in sales if s.get("delivery_status") == "skipped"]),
    }
    
    return {
        "date": date_str,
        "routes_completed": len([dr for dr in daily_routes if dr.get("status") == "completed"]),
        "routes_active": len([dr for dr in daily_routes if dr.get("status") == "active"]),
        "total_sales": len(sales),
        "total_collected": total_collected,
        "total_expected": total_expected,
        "collection_rate": (total_collected / total_expected * 100) if total_expected > 0 else 0,
        "total_km_traveled": total_km,
        "product_breakdown": product_totals,
        "delivery_status": delivery_status,
        "daily_routes": [str_id(dr) for dr in daily_routes],
        "vehicle_inspections": [
            {
                "route_name": dr.get("route_name", ""),
                "vehicle_name": dr.get("vehicle_name", ""),
                "vehicle_registration": dr.get("vehicle_registration", ""),
                "driver_name": dr.get("driver_name", ""),
                "inspection": dr.get("vehicle_check", {}),
            }
            for dr in daily_routes if dr.get("vehicle_check")
        ]
    }

@api_router.get("/reports/route-performance/{route_id}")
async def get_route_performance(route_id: str, days: int = 7):
    """Get performance metrics for a route over the past N days"""
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    daily_routes = await db.daily_routes.find({
        "route_id": route_id,
        "started_at": {"$gte": start_date, "$lte": end_date}
    }).to_list(100)
    
    return {
        "route_id": route_id,
        "period_days": days,
        "total_trips": len(daily_routes),
        "total_sales": sum(dr.get("sales_count", 0) for dr in daily_routes),
        "total_collected": sum(dr.get("total_collected", 0) for dr in daily_routes),
        "total_km": sum(dr.get("km_traveled", 0) or 0 for dr in daily_routes),
        "avg_sales_per_trip": sum(dr.get("sales_count", 0) for dr in daily_routes) / len(daily_routes) if daily_routes else 0,
        "daily_breakdown": [str_id(dr) for dr in daily_routes]
    }

@api_router.get("/reports/export/excel")
async def export_route_report_excel(
    date_str: Optional[str] = None,
    route_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Export beautiful, interactive route report to Excel format"""
    if not date_str:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    # Get daily routes
    query = {"date": date_str}
    if route_id:
        query["route_id"] = route_id
    if current_user["role"] == "driver":
        query["driver_id"] = current_user["id"]
    
    daily_routes = await db.daily_routes.find(query).to_list(100)
    
    # Get sales for the day
    start = datetime.strptime(date_str, "%Y-%m-%d")
    end = start.replace(hour=23, minute=59, second=59)
    
    sales_query = {"created_at": {"$gte": start, "$lte": end}, "is_voided": {"$ne": True}}
    if route_id:
        sales_query["route_id"] = route_id
    if current_user["role"] == "driver":
        sales_query["driver_id"] = current_user["id"]
    
    sales = await db.sales.find(sales_query).to_list(1000)
    
    # Get products for breakdown
    products = await db.products.find().to_list(500)
    product_map = {str(p["_id"]): p["name"] for p in products}
    
    # Create Excel file in memory
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    
    # ==================== DEFINE STYLES ====================
    # Brand colors
    primary_color = '#3B82F6'  # Blue
    success_color = '#10B981'  # Green
    warning_color = '#F59E0B'  # Orange
    danger_color = '#EF4444'   # Red
    dark_bg = '#1E293B'        # Dark background
    light_text = '#F8FAFC'     # Light text
    
    # Title styles
    title_format = workbook.add_format({
        'bold': True, 'font_size': 24, 'font_color': primary_color,
        'align': 'center', 'valign': 'vcenter'
    })
    subtitle_format = workbook.add_format({
        'bold': True, 'font_size': 14, 'font_color': '#64748B',
        'align': 'center', 'valign': 'vcenter'
    })
    
    # Header styles
    header_format = workbook.add_format({
        'bold': True, 'font_size': 11, 'font_color': 'white',
        'bg_color': primary_color, 'border': 1, 'border_color': primary_color,
        'align': 'center', 'valign': 'vcenter', 'text_wrap': True
    })
    header_green = workbook.add_format({
        'bold': True, 'font_size': 11, 'font_color': 'white',
        'bg_color': success_color, 'border': 1,
        'align': 'center', 'valign': 'vcenter'
    })
    header_orange = workbook.add_format({
        'bold': True, 'font_size': 11, 'font_color': 'white',
        'bg_color': warning_color, 'border': 1,
        'align': 'center', 'valign': 'vcenter'
    })
    
    # Data cell styles
    cell_format = workbook.add_format({
        'border': 1, 'border_color': '#E2E8F0',
        'valign': 'vcenter', 'align': 'left'
    })
    cell_center = workbook.add_format({
        'border': 1, 'border_color': '#E2E8F0',
        'valign': 'vcenter', 'align': 'center'
    })
    cell_wrap = workbook.add_format({
        'border': 1, 'border_color': '#E2E8F0',
        'valign': 'vcenter', 'text_wrap': True
    })
    
    # Number formats
    money_format = workbook.add_format({
        'num_format': 'R #,##0.00', 'border': 1, 'border_color': '#E2E8F0',
        'align': 'right', 'valign': 'vcenter'
    })
    money_bold = workbook.add_format({
        'num_format': 'R #,##0.00', 'border': 1, 'bold': True,
        'align': 'right', 'valign': 'vcenter', 'bg_color': '#F0FDF4'
    })
    number_format = workbook.add_format({
        'num_format': '#,##0', 'border': 1, 'border_color': '#E2E8F0',
        'align': 'center', 'valign': 'vcenter'
    })
    percent_format = workbook.add_format({
        'num_format': '0.0%', 'border': 1, 'border_color': '#E2E8F0',
        'align': 'center', 'valign': 'vcenter'
    })
    date_format = workbook.add_format({
        'num_format': 'yyyy-mm-dd', 'border': 1, 'border_color': '#E2E8F0',
        'align': 'center', 'valign': 'vcenter'
    })
    time_format = workbook.add_format({
        'num_format': 'hh:mm', 'border': 1, 'border_color': '#E2E8F0',
        'align': 'center', 'valign': 'vcenter'
    })
    
    # Status styles
    status_delivered = workbook.add_format({
        'bold': True, 'font_color': 'white', 'bg_color': success_color,
        'border': 1, 'align': 'center', 'valign': 'vcenter'
    })
    status_pending = workbook.add_format({
        'bold': True, 'font_color': 'white', 'bg_color': warning_color,
        'border': 1, 'align': 'center', 'valign': 'vcenter'
    })
    status_active = workbook.add_format({
        'bold': True, 'font_color': 'white', 'bg_color': primary_color,
        'border': 1, 'align': 'center', 'valign': 'vcenter'
    })
    
    # KPI Card styles
    kpi_label = workbook.add_format({
        'font_size': 10, 'font_color': '#64748B',
        'align': 'center', 'valign': 'bottom'
    })
    kpi_value = workbook.add_format({
        'bold': True, 'font_size': 18, 'font_color': '#1E293B',
        'align': 'center', 'valign': 'top'
    })
    kpi_value_money = workbook.add_format({
        'bold': True, 'font_size': 18, 'font_color': success_color,
        'num_format': 'R #,##0.00', 'align': 'center', 'valign': 'top'
    })
    
    # Alternating row colors
    row_even = workbook.add_format({
        'border': 1, 'border_color': '#E2E8F0',
        'bg_color': '#F8FAFC', 'valign': 'vcenter'
    })
    row_odd = workbook.add_format({
        'border': 1, 'border_color': '#E2E8F0',
        'valign': 'vcenter'
    })
    
    # Calculate totals
    total_collected = sum(s.get("cash_collected", 0) for s in sales)
    total_expected = sum(s.get("total_amount", 0) for s in sales)
    total_crates_dropped = sum(s.get("crates_dropped", 0) for s in sales)
    total_crates_collected = sum(s.get("crates_collected", 0) for s in sales)
    total_km = sum(dr.get("km_traveled", 0) or 0 for dr in daily_routes)
    collection_rate = (total_collected / total_expected) if total_expected > 0 else 0
    
    # ==================== DASHBOARD SHEET ====================
    dashboard = workbook.add_worksheet('📊 Dashboard')
    dashboard.set_tab_color(primary_color)
    
    # Set column widths
    dashboard.set_column('A:A', 3)   # Margin
    dashboard.set_column('B:G', 18)  # KPI columns
    dashboard.set_column('H:H', 3)   # Margin
    
    # Hide gridlines for cleaner look
    dashboard.hide_gridlines(2)
    
    # Title
    dashboard.set_row(1, 40)
    dashboard.merge_range('B2:G2', '📊 MZANSI DISTRIBUTION TRACKER', title_format)
    dashboard.merge_range('B3:G3', f'Daily Report - {date_str}', subtitle_format)
    
    # KPI Cards Row 1
    dashboard.set_row(5, 20)
    dashboard.set_row(6, 30)
    
    total_shortage = sum(s.get("shortage_amount", 0) for s in sales)
    
    kpis = [
        ('Total Sales', len(sales), None),
        ('Cash Collected', total_collected, 'money'),
        ('Expected', total_expected, 'money'),
        ('Shortage', total_shortage, 'money'),
        ('Collection Rate', collection_rate, 'percent'),
        ('Routes', len(daily_routes), None),
        ('KM Traveled', total_km, None),
    ]
    
    for i, (label, value, fmt) in enumerate(kpis):
        col = i + 1  # B=1, C=2, etc.
        dashboard.write(4, col, label, kpi_label)
        if fmt == 'money':
            dashboard.write(5, col, value, kpi_value_money)
        elif fmt == 'percent':
            dashboard.write(5, col, f"{value*100:.1f}%", kpi_value)
        else:
            dashboard.write(5, col, value, kpi_value)
    
    # Crates Summary Row
    dashboard.set_row(8, 20)
    dashboard.set_row(9, 30)
    
    crate_kpis = [
        ('Crates Out', total_crates_dropped),
        ('Crates In', total_crates_collected),
        ('Net Crates', total_crates_dropped - total_crates_collected),
    ]
    
    for i, (label, value) in enumerate(crate_kpis):
        col = i + 1
        dashboard.write(7, col, label, kpi_label)
        dashboard.write(8, col, value, kpi_value)
    
    # Add a mini sales table on dashboard
    dashboard.write(11, 1, 'Recent Sales', header_format)
    dashboard.merge_range('B12:G12', '', header_format)
    
    mini_headers = ['Time', 'Customer', 'Amount', 'Collected', 'Status']
    for i, h in enumerate(mini_headers):
        dashboard.write(12, i + 1, h, header_format)
    
    for row_idx, sale in enumerate(sales[:10], start=13):
        time_str = sale.get("created_at", datetime.utcnow()).strftime("%H:%M")
        row_fmt = row_even if row_idx % 2 == 0 else row_odd
        dashboard.write(row_idx, 1, time_str, row_fmt)
        dashboard.write(row_idx, 2, sale.get("customer_name", "")[:20], row_fmt)
        dashboard.write(row_idx, 3, sale.get("total_amount", 0), money_format)
        dashboard.write(row_idx, 4, sale.get("cash_collected", 0), money_format)
        status = sale.get("delivery_status", "delivered")
        if status == "delivered":
            dashboard.write(row_idx, 5, "✓ Delivered", status_delivered)
        else:
            dashboard.write(row_idx, 5, "⏳ Pending", status_pending)
    
    # ==================== SALES DETAILS SHEET ====================
    sales_sheet = workbook.add_worksheet('💰 Sales Details')
    sales_sheet.set_tab_color(success_color)
    sales_sheet.hide_gridlines(2)
    
    # Set column widths
    sales_sheet.set_column('A:A', 12)   # Time
    sales_sheet.set_column('B:B', 25)   # Customer
    sales_sheet.set_column('C:C', 18)   # Driver
    sales_sheet.set_column('D:D', 50)   # Products (wider for details)
    sales_sheet.set_column('E:E', 14)   # Total
    sales_sheet.set_column('F:F', 14)   # Collected
    sales_sheet.set_column('G:G', 12)   # Shortage
    sales_sheet.set_column('H:H', 12)   # Crates Out
    sales_sheet.set_column('I:I', 12)   # Crates In
    sales_sheet.set_column('J:J', 25)   # Payment (wider for split payments)
    sales_sheet.set_column('K:K', 12)   # Status
    sales_sheet.set_column('L:L', 40)   # Notes
    
    # Title
    sales_sheet.set_row(0, 30)
    sales_sheet.merge_range('A1:J1', f'💰 Sales Report - {date_str}', title_format)
    
    # Headers with filters
    sales_headers = ['Time', 'Customer', 'Driver', 'Products', 'Total', 'Collected', 
                     'Shortage', 'Crates Out', 'Crates In', 'Payment', 'Status', 'Notes']
    
    for col, header in enumerate(sales_headers):
        sales_sheet.write(2, col, header, header_format)
    
    # Enable auto-filter
    if sales:
        sales_sheet.autofilter(2, 0, 2 + len(sales), len(sales_headers) - 1)
    
    # Freeze header row
    sales_sheet.freeze_panes(3, 0)
    
    # Data rows
    for row_num, sale in enumerate(sales, start=3):
        time_str = sale.get("created_at", datetime.utcnow()).strftime("%H:%M")
        
        # Enhanced product details with D:delivered, R:returned, DMG:damages, Net:net_sold
        product_details = []
        for i in sale.get("items", []):
            delivered = i.get('quantity_delivered', 0)
            returned = i.get('quantity_returned', 0)
            damages = i.get('damages', 0)
            net = delivered - returned
            product_details.append(f"{i.get('product_name', '')} (D:{delivered}, R:{returned}, DMG:{damages}, Net:{net})")
        products = ", ".join(product_details)
        
        # Enhanced payment details with split payment breakdown
        payment_type = sale.get("payment_type", "cash").upper()
        if payment_type == "SPLIT" and sale.get("split_payments"):
            payment_parts = []
            for sp in sale.get("split_payments", []):
                payment_parts.append(f"{sp.get('method', '').upper()}:R{sp.get('amount', 0):.0f}")
            payment_display = ", ".join(payment_parts)
        else:
            payment_display = payment_type
        
        shortage = sale.get("shortage_amount", 0)
        notes = sale.get("notes", "") or ""
        
        row_fmt = row_even if row_num % 2 == 0 else row_odd
        
        sales_sheet.write(row_num, 0, time_str, cell_center)
        sales_sheet.write(row_num, 1, sale.get("customer_name", ""), cell_format)
        sales_sheet.write(row_num, 2, sale.get("driver_name", ""), cell_format)
        sales_sheet.write(row_num, 3, products, cell_wrap)
        sales_sheet.write(row_num, 4, sale.get("total_amount", 0), money_format)
        sales_sheet.write(row_num, 5, sale.get("cash_collected", 0), money_format)
        sales_sheet.write(row_num, 6, shortage, money_format)
        sales_sheet.write(row_num, 7, sale.get("crates_dropped", 0), number_format)
        sales_sheet.write(row_num, 8, sale.get("crates_collected", 0), number_format)
        sales_sheet.write(row_num, 9, payment_display, cell_wrap)
        
        status = sale.get("delivery_status", "delivered")
        if status == "delivered":
            sales_sheet.write(row_num, 10, "✓ DELIVERED", status_delivered)
        else:
            sales_sheet.write(row_num, 10, "PENDING", status_pending)
        
        sales_sheet.write(row_num, 11, notes, cell_wrap)
    
    # Totals row
    if sales:
        total_row = 3 + len(sales)
        total_shortage = sum(s.get("shortage_amount", 0) for s in sales)
        sales_sheet.write(total_row, 3, 'TOTALS:', header_format)
        sales_sheet.write(total_row, 4, total_expected, money_bold)
        sales_sheet.write(total_row, 5, total_collected, money_bold)
        sales_sheet.write(total_row, 6, total_shortage, money_bold)
        sales_sheet.write(total_row, 7, total_crates_dropped, header_green)
        sales_sheet.write(total_row, 8, total_crates_collected, header_green)
    
    # ==================== ROUTES SHEET ====================
    routes_sheet = workbook.add_worksheet('🚗 Routes')
    routes_sheet.set_tab_color(warning_color)
    routes_sheet.hide_gridlines(2)
    
    # Set column widths
    routes_sheet.set_column('A:A', 20)  # Route
    routes_sheet.set_column('B:B', 18)  # Driver
    routes_sheet.set_column('C:C', 25)  # Vehicle
    routes_sheet.set_column('D:D', 12)  # Opening KM
    routes_sheet.set_column('E:E', 12)  # Closing KM
    routes_sheet.set_column('F:F', 12)  # KM Traveled
    routes_sheet.set_column('G:G', 12)  # Crates Out
    routes_sheet.set_column('H:H', 12)  # Crates In
    routes_sheet.set_column('I:I', 10)  # Sales
    routes_sheet.set_column('J:J', 14)  # Collected
    routes_sheet.set_column('K:K', 14)  # Shortage
    routes_sheet.set_column('L:L', 12)  # Status
    
    # Title
    routes_sheet.set_row(0, 30)
    routes_sheet.merge_range('A1:K1', f'🚗 Route Details - {date_str}', title_format)
    
    # Headers
    route_headers = ['Route', 'Driver', 'Vehicle', 'Start KM', 'End KM', 'Distance', 
                     'Crates Out', 'Crates In', 'Sales', 'Collected', 'Shortage', 'Status']
    
    for col, header in enumerate(route_headers):
        routes_sheet.write(2, col, header, header_format)
    
    # Enable auto-filter
    if daily_routes:
        routes_sheet.autofilter(2, 0, 2 + len(daily_routes), len(route_headers) - 1)
    
    routes_sheet.freeze_panes(3, 0)
    
    # Data rows
    for row_num, dr in enumerate(daily_routes, start=3):
        vehicle_info = f"{dr.get('vehicle_name', 'N/A')} ({dr.get('vehicle_registration', '')})"
        row_fmt = row_even if row_num % 2 == 0 else row_odd
        
        routes_sheet.write(row_num, 0, dr.get("route_name", ""), cell_format)
        routes_sheet.write(row_num, 1, dr.get("driver_name", ""), cell_format)
        routes_sheet.write(row_num, 2, vehicle_info, cell_format)
        routes_sheet.write(row_num, 3, dr.get("opening_km", 0), number_format)
        routes_sheet.write(row_num, 4, dr.get("closing_km", 0) or 0, number_format)
        routes_sheet.write(row_num, 5, dr.get("km_traveled", 0) or 0, number_format)
        routes_sheet.write(row_num, 6, dr.get("crates_out", 0), number_format)
        routes_sheet.write(row_num, 7, dr.get("crates_in", 0) or 0, number_format)
        routes_sheet.write(row_num, 8, dr.get("sales_count", 0), number_format)
        routes_sheet.write(row_num, 9, dr.get("total_collected", 0), money_format)
        routes_sheet.write(row_num, 10, dr.get("total_shortage", 0), money_format)
        
        status = dr.get("status", "active")
        if status == "completed":
            routes_sheet.write(row_num, 11, "✓ COMPLETED", status_delivered)
        else:
            routes_sheet.write(row_num, 11, "🔵 ACTIVE", status_active)
    
    # ==================== PRODUCT BREAKDOWN SHEET ====================
    products_sheet = workbook.add_worksheet('📦 Products')
    products_sheet.set_tab_color('#8B5CF6')
    products_sheet.hide_gridlines(2)
    
    # Set column widths
    products_sheet.set_column('A:A', 25)  # Product
    products_sheet.set_column('B:B', 15)  # Category
    products_sheet.set_column('C:C', 12)  # Delivered
    products_sheet.set_column('D:D', 12)  # Returned
    products_sheet.set_column('E:E', 12)  # Damages
    products_sheet.set_column('F:F', 12)  # Net Sold
    products_sheet.set_column('G:G', 14)  # Revenue
    
    # Title
    products_sheet.set_row(0, 30)
    products_sheet.merge_range('A1:G1', f'📦 Product Breakdown - {date_str}', title_format)
    
    # Calculate product totals
    product_totals = {}
    for sale in sales:
        for item in sale.get("items", []):
            prod_name = item.get("product_name", "Unknown")
            if prod_name not in product_totals:
                product_totals[prod_name] = {
                    "delivered": 0, "returned": 0, "damages": 0, "revenue": 0,
                    "category": item.get("category", "Other")
                }
            product_totals[prod_name]["delivered"] += item.get("quantity_delivered", 0)
            product_totals[prod_name]["returned"] += item.get("quantity_returned", 0)
            product_totals[prod_name]["damages"] += item.get("damages", 0)
            net = item.get("quantity_delivered", 0) - item.get("quantity_returned", 0)
            product_totals[prod_name]["revenue"] += net * item.get("unit_price", 0)
    
    # Headers
    prod_headers = ['Product', 'Category', 'Delivered', 'Returned', 'Damages', 'Net Sold', 'Revenue']
    for col, header in enumerate(prod_headers):
        products_sheet.write(2, col, header, header_format)
    
    if product_totals:
        products_sheet.autofilter(2, 0, 2 + len(product_totals), len(prod_headers) - 1)
    
    products_sheet.freeze_panes(3, 0)
    
    # Data rows
    for row_num, (prod_name, data) in enumerate(sorted(product_totals.items()), start=3):
        net_sold = data["delivered"] - data["returned"]
        row_fmt = row_even if row_num % 2 == 0 else row_odd
        
        products_sheet.write(row_num, 0, prod_name, cell_format)
        products_sheet.write(row_num, 1, data["category"], cell_center)
        products_sheet.write(row_num, 2, data["delivered"], number_format)
        products_sheet.write(row_num, 3, data["returned"], number_format)
        products_sheet.write(row_num, 4, data["damages"], number_format)
        products_sheet.write(row_num, 5, net_sold, header_green if net_sold > 0 else number_format)
        products_sheet.write(row_num, 6, data["revenue"], money_format)
    
    # Grand totals
    if product_totals:
        total_row = 3 + len(product_totals)
        grand_delivered = sum(d["delivered"] for d in product_totals.values())
        grand_returned = sum(d["returned"] for d in product_totals.values())
        grand_damages = sum(d["damages"] for d in product_totals.values())
        grand_revenue = sum(d["revenue"] for d in product_totals.values())
        
        products_sheet.write(total_row, 0, 'GRAND TOTAL', header_format)
        products_sheet.write(total_row, 1, '', header_format)
        products_sheet.write(total_row, 2, grand_delivered, header_green)
        products_sheet.write(total_row, 3, grand_returned, header_orange)
        products_sheet.write(total_row, 4, grand_damages, header_orange)
        products_sheet.write(total_row, 5, grand_delivered - grand_returned, header_green)
        products_sheet.write(total_row, 6, grand_revenue, money_bold)
    
    # ==================== VEHICLE INSPECTION SHEET ====================
    insp_sheet = workbook.add_worksheet('🔍 Vehicle Inspection')
    insp_sheet.set_tab_color('#EF4444')
    insp_sheet.hide_gridlines(2)
    
    insp_sheet.set_column('A:A', 22)  # Category / Route
    insp_sheet.set_column('B:B', 18)  # Vehicle
    insp_sheet.set_column('C:C', 18)  # Driver
    insp_sheet.set_column('D:D', 35)  # Item
    insp_sheet.set_column('E:E', 10)  # Status
    insp_sheet.set_column('F:F', 40)  # Comment
    
    insp_sheet.set_row(0, 30)
    insp_sheet.merge_range('A1:F1', f'🔍 Vehicle Inspection Report - {date_str}', title_format)
    
    insp_headers = ['Route / Category', 'Vehicle', 'Driver', 'Inspection Item', 'Status', 'Comments']
    for col, header in enumerate(insp_headers):
        insp_sheet.write(2, col, header, header_format)
    
    insp_sheet.freeze_panes(3, 0)
    
    insp_row = 3
    for dr in daily_routes:
        vc = dr.get("vehicle_check") or {}
        if not vc:
            continue
        
        route_name = dr.get("route_name", "")
        vehicle_info = f"{dr.get('vehicle_name', 'N/A')} ({dr.get('vehicle_registration', '')})"
        driver_name = dr.get("driver_name", "")
        
        # Summary row
        summary = vc.get("summary", {})
        pass_rate = summary.get("pass_rate", 0)
        total_items = summary.get("total_items", 0)
        passed_count = summary.get("passed", 0)
        failed_count = summary.get("failed", 0)
        
        summary_text = f"Pass Rate: {pass_rate}% ({passed_count}/{total_items})"
        if failed_count > 0:
            summary_text += f" - {failed_count} FAILED"
        
        insp_sheet.write(insp_row, 0, route_name, header_format)
        insp_sheet.write(insp_row, 1, vehicle_info, header_format)
        insp_sheet.write(insp_row, 2, driver_name, header_format)
        insp_sheet.write(insp_row, 3, summary_text, header_format)
        insp_sheet.write(insp_row, 4, f"{pass_rate}%", header_green if pass_rate >= 80 else header_orange)
        insp_sheet.write(insp_row, 5, vc.get("overall_notes", "") or "", cell_wrap)
        insp_row += 1
        
        # Category details
        categories = vc.get("categories", {})
        for cat_id, cat_data in categories.items():
            cat_title = cat_data.get("title", cat_id)
            for item in cat_data.get("items", []):
                status = item.get("passed")
                status_text = "✓ PASS" if status is True else ("✗ FAIL" if status is False else "—")
                status_fmt = status_delivered if status is True else (status_pending if status is False else cell_center)
                comment = item.get("comment") or ""
                
                insp_sheet.write(insp_row, 0, cat_title, cell_format)
                insp_sheet.write(insp_row, 1, "", cell_format)
                insp_sheet.write(insp_row, 2, "", cell_format)
                insp_sheet.write(insp_row, 3, item.get("label", ""), cell_format)
                insp_sheet.write(insp_row, 4, status_text, status_fmt)
                insp_sheet.write(insp_row, 5, comment, cell_wrap)
                insp_row += 1
        
        # Blank separator row
        insp_row += 1
    
    # If no inspections
    if insp_row == 3:
        insp_sheet.write(3, 0, "No vehicle inspections recorded for this date", cell_format)

    # Set Dashboard as the active sheet
    dashboard.activate()
    
    workbook.close()
    output.seek(0)
    
    filename = f"mzansi_report_{date_str}.xlsx"
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ==================== PERMISSIONS CHECK ENDPOINT ====================

@api_router.get("/permissions")
async def get_permissions(current_user: dict = Depends(get_current_user)):
    """Get current user's permissions"""
    role = current_user.get("role", "driver")
    
    permissions = {
        "role": role,
        "can_manage_users": role == "admin",
        "can_manage_products": role in ["admin", "manager"],
        "can_manage_routes": role in ["admin", "manager"],
        "can_manage_customers": role in ["admin", "manager"],
        "can_edit_all_sales": role in ["admin", "manager"],
        "can_void_any_sale": role in ["admin", "manager"],
        "can_edit_own_same_day_sales": True,
        "can_add_customers": True,
        "can_record_sales": role in ["admin", "manager", "driver"],
        "can_view_all_reports": role in ["admin", "manager"],
    }
    
    return permissions

# ==================== SEED ALL DATA ====================

@api_router.post("/seed-all")
async def seed_all_data():
    """Seed all sample data for testing"""
    # Seed routes first
    await seed_routes()
    
    # Seed products
    await seed_products()
    
    # Seed vehicles
    await seed_vehicles()
    
    # Seed customers
    await seed_customers()
    
    # Create demo users
    demo_users = [
        {"name": "Admin User", "phone": "0800000001", "pin": "0000", "role": "admin"},
        {"name": "Manager User", "phone": "0800000002", "pin": "1111", "role": "manager"},
        {"name": "Demo Driver", "phone": "0812345678", "pin": "1234", "role": "driver"},
        {"name": "Second Driver", "phone": "0812345679", "pin": "5678", "role": "driver"},
        {"name": "Conductor", "phone": "0812345680", "pin": "9999", "role": "conductor"},
    ]
    
    for user in demo_users:
        existing = await db.users.find_one({"phone": user["phone"]})
        if not existing:
            user_doc = {
                "name": user["name"],
                "phone": user["phone"],
                "pin_hash": hash_pin(user["pin"]),
                "role": user["role"],
                "is_active": True,
                "created_at": datetime.utcnow()
            }
            await db.users.insert_one(user_doc)
    
    return {
        "message": "All data seeded successfully",
        "demo_logins": {
            "admin": {"phone": "0800000001", "pin": "0000"},
            "manager": {"phone": "0800000002", "pin": "1111"},
            "driver": {"phone": "0812345678", "pin": "1234"},
        }
    }

# Register all routes
app.include_router(api_router)

# Health check endpoint
@app.get("/")
async def root():
    return {"status": "ok", "message": "Mzansi FMCG Tracker API is running"}

@app.get("/health")
async def health_check():
    try:
        # Test database connection
        await client.admin.command('ping')
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": "disconnected", "error": str(e)}

# ==================== EMAIL REPORT ENDPOINTS ====================

class EmailReportRequest(BaseModel):
    report_type: str  # daily, weekly, monthly
    recipient_emails: List[str]
    date_str: Optional[str] = None
    include_excel: bool = True

@api_router.post("/settings/email")
async def save_email_settings(config: dict, current_user: dict = Depends(get_current_user)):
    """Save email settings - Admin only"""
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    await db.settings.update_one(
        {"key": "email_config"},
        {"$set": {"key": "email_config", "value": config, "updated_at": datetime.utcnow()}},
        upsert=True
    )
    return {"message": "Email settings saved"}

@api_router.get("/settings/email")
async def get_email_settings(current_user: dict = Depends(get_current_user)):
    """Get email settings - Admin only"""
    if not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    settings = await db.settings.find_one({"key": "email_config"})
    if not settings:
        return {"configured": False}
    return {"configured": True, "recipient_emails": settings.get("value", {}).get("recipient_emails", [])}

async def generate_report_excel_for_email(report_type: str, date_str: str = None):
    """Generate Excel report for emailing"""
    if not date_str:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    end_date = datetime.strptime(date_str, "%Y-%m-%d")
    if report_type == "daily":
        start_date = end_date
    elif report_type == "weekly":
        start_date = end_date - timedelta(days=7)
    elif report_type == "monthly":
        start_date = end_date - timedelta(days=30)
    else:
        start_date = end_date
    
    daily_routes = await db.daily_routes.find({
        "date": {"$gte": start_date.strftime("%Y-%m-%d"), "$lte": date_str}
    }).to_list(500)
    
    sales = await db.sales.find({
        "created_at": {"$gte": start_date, "$lte": end_date.replace(hour=23, minute=59, second=59)},
        "is_voided": {"$ne": True}
    }).to_list(5000)
    
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    
    header_format = workbook.add_format({'bold': True, 'bg_color': '#3B82F6', 'font_color': 'white', 'border': 1})
    money_format = workbook.add_format({'num_format': 'R #,##0.00', 'border': 1})
    cell_format = workbook.add_format({'border': 1})
    
    summary = workbook.add_worksheet('Summary')
    summary.set_column('A:B', 25)
    summary.write('A1', 'Metric', header_format)
    summary.write('B1', 'Value', header_format)
    
    total_collected = sum(s.get("cash_collected", 0) for s in sales)
    total_expected = sum(s.get("total_amount", 0) for s in sales)
    
    metrics = [
        ('Report Type', report_type.capitalize()),
        ('Period', f"{start_date.strftime('%Y-%m-%d')} to {date_str}"),
        ('Total Routes', len(daily_routes)),
        ('Total Sales', len(sales)),
        ('Total Collected', total_collected),
        ('Total Expected', total_expected),
    ]
    
    for i, (metric, value) in enumerate(metrics, start=1):
        summary.write(i, 0, metric, cell_format)
        summary.write(i, 1, str(value) if not isinstance(value, float) else value, money_format if isinstance(value, float) else cell_format)
    
    sales_sheet = workbook.add_worksheet('Sales')
    headers = ['Date/Time', 'Customer', 'Driver', 'Products', 'Total', 'Collected', 'Shortage', 'Crates Out', 'Crates In', 'Payment', 'Notes']
    for col, h in enumerate(headers):
        sales_sheet.write(0, col, h, header_format)
    
    for row, sale in enumerate(sales, start=1):
        created_at = sale.get("created_at", datetime.utcnow())
        
        # Enhanced product details
        product_details = []
        for item in sale.get('items', []):
            delivered = item.get('quantity_delivered', 0)
            returned = item.get('quantity_returned', 0)
            damages = item.get('damages', 0)
            net = delivered - returned
            product_details.append(f"{item.get('product_name', '')} (D:{delivered} R:{returned} DMG:{damages} Net:{net})")
        
        # Enhanced payment details
        payment_type = sale.get('payment_type', 'cash').upper()
        if payment_type == 'SPLIT' and sale.get('split_payments'):
            payment_parts = []
            for sp in sale.get('split_payments', []):
                payment_parts.append(f"{sp.get('method', '').upper()}:R{sp.get('amount', 0):.2f}")
            payment_display = ' + '.join(payment_parts)
        else:
            payment_display = payment_type
        
        sales_sheet.write(row, 0, created_at.strftime("%Y-%m-%d %H:%M"), cell_format)
        sales_sheet.write(row, 1, sale.get("customer_name", ""), cell_format)
        sales_sheet.write(row, 2, sale.get("driver_name", ""), cell_format)
        sales_sheet.write(row, 3, '; '.join(product_details), cell_format)
        sales_sheet.write(row, 4, sale.get("total_amount", 0), money_format)
        sales_sheet.write(row, 5, sale.get("cash_collected", 0), money_format)
        sales_sheet.write(row, 6, sale.get("shortage_amount", 0), money_format)
        sales_sheet.write(row, 7, sale.get("crates_dropped", 0), cell_format)
        sales_sheet.write(row, 8, sale.get("crates_collected", 0), cell_format)
        sales_sheet.write(row, 9, payment_display, cell_format)
        sales_sheet.write(row, 10, sale.get("notes", "") or "", cell_format)
    
    workbook.close()
    output.seek(0)
    return output.getvalue()

@api_router.post("/reports/email")
async def email_report(
    request: EmailReportRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Send report via email - Admin/Manager only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    settings = await db.settings.find_one({"key": "email_config"})
    if not settings or not settings.get("value"):
        raise HTTPException(status_code=400, detail="Email not configured. Please set up email settings first.")
    
    email_config = settings["value"]
    date_str = request.date_str or datetime.utcnow().strftime("%Y-%m-%d")
    
    email_record = {
        "report_type": request.report_type,
        "recipient_emails": request.recipient_emails,
        "date_str": date_str,
        "status": "pending",
        "requested_by": current_user["id"],
        "requested_at": datetime.utcnow()
    }
    
    result = await db.email_logs.insert_one(email_record)
    email_id = str(result.inserted_id)
    
    async def send_email_task():
        try:
            excel_data = await generate_report_excel_for_email(request.report_type, date_str)
            
            msg = MIMEMultipart()
            msg['From'] = email_config.get('sender_email')
            msg['To'] = ', '.join(request.recipient_emails)
            msg['Subject'] = f"Mzansi FMCG Tracker - {request.report_type.capitalize()} Report ({date_str})"
            
            body = f"Dear Team,\n\nPlease find attached the {request.report_type} report for {date_str}.\n\nBest regards,\nDistribution Management System"
            msg.attach(MIMEText(body, 'plain'))
            
            if request.include_excel:
                attachment = MIMEBase('application', 'vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                attachment.set_payload(excel_data)
                encoders.encode_base64(attachment)
                attachment.add_header('Content-Disposition', f'attachment; filename={request.report_type}_report_{date_str}.xlsx')
                msg.attach(attachment)
            
            # Support both SSL (port 465) and TLS (port 587)
            smtp_port = email_config.get('smtp_port', 465)
            smtp_server = email_config.get('smtp_server', 'mail.mzansipc.co.za')
            
            if smtp_port == 465:
                # Use SSL
                server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            else:
                # Use TLS
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
            
            server.login(email_config.get('sender_email'), email_config.get('sender_password'))
            server.send_message(msg)
            server.quit()
            
            await db.email_logs.update_one(
                {"_id": ObjectId(email_id)},
                {"$set": {"status": "sent", "sent_at": datetime.utcnow()}}
            )
        except Exception as e:
            await db.email_logs.update_one(
                {"_id": ObjectId(email_id)},
                {"$set": {"status": "failed", "error": str(e), "failed_at": datetime.utcnow()}}
            )
    
    background_tasks.add_task(send_email_task)
    
    return {"message": f"{request.report_type.capitalize()} report queued", "email_id": email_id}

@api_router.get("/reports/email-logs")
async def get_email_logs(current_user: dict = Depends(get_current_user)):
    """Get email send history"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    logs = await db.email_logs.find().sort("requested_at", -1).to_list(50)
    return [str_id(log) for log in logs]

# ==================== CUSTOMER PRICING ENDPOINTS ====================

@api_router.get("/customers/{customer_id}/prices")
async def get_customer_prices(customer_id: str, current_user: dict = Depends(get_current_user)):
    """Get custom prices for a customer"""
    customer = await db.customers.find_one({"_id": ObjectId(customer_id)})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    products = await db.products.find().to_list(500)
    custom_prices = customer.get("custom_prices") or {}
    
    price_list = []
    for product in products:
        product_id = str(product["_id"])
        price_list.append({
            "product_id": product_id,
            "product_name": product["name"],
            "category": product["category"],
            "default_price": product["price"],
            "custom_price": custom_prices.get(product_id) if custom_prices else None,
            "effective_price": custom_prices.get(product_id, product["price"]) if custom_prices else product["price"]
        })
    
    return {"customer_id": customer_id, "customer_name": customer["name"], "price_list": price_list}

@api_router.put("/customers/{customer_id}/prices")
async def update_customer_prices(
    customer_id: str,
    prices: Dict[str, float],
    current_user: dict = Depends(get_current_user)
):
    """Update custom prices for a customer - Admin/Manager only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    customer = await db.customers.find_one({"_id": ObjectId(customer_id)})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    await db.customers.update_one(
        {"_id": ObjectId(customer_id)},
        {"$set": {"custom_prices": prices, "prices_updated_at": datetime.utcnow()}}
    )
    
    return {"message": "Customer prices updated", "customer_id": customer_id}

# ==================== STOCK MANAGEMENT ENDPOINTS ====================

@api_router.get("/stock")
async def get_all_stock(current_user: dict = Depends(get_current_user)):
    """Get all stock records (company-scoped)"""
    cf = get_company_filter(current_user)
    stock_records = await db.stock.find(cf).to_list(500)
    return [str_id(s) for s in stock_records]

@api_router.get("/stock/levels")
async def get_stock_levels(current_user: dict = Depends(get_current_user)):
    """Get current stock levels for all products (company-scoped)"""
    cf = get_company_filter(current_user)
    products = await db.products.find(cf).to_list(500)
    stock_levels = []
    
    for product in products:
        product_id = str(product["_id"])
        company_id = product.get("company_id", "")
        stock_item = await db.stock.find_one({"product_id": product_id, "company_id": company_id})
        
        stock_levels.append({
            "product_id": product_id,
            "product_name": product["name"],
            "category": product["category"],
            "unit_type": product.get("unit_type", "units"),
            "current_quantity": stock_item.get("quantity", 0) if stock_item else 0,
            "last_updated": stock_item.get("updated_at") if stock_item else None
        })
    
    return stock_levels

@api_router.post("/stock/receive")
async def receive_stock(data: StockReceiveCreate, current_user: dict = Depends(get_current_user)):
    """Record incoming stock from supplier - Admin/Manager only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    company_id = current_user.get("company_id", "")
    
    # Calculate net quantity after deducting damages/rejects/spoilt
    total_deductions = data.damages_in_transit + data.rejected_stock + data.spoilt_from_factory
    net_quantity = data.quantity - total_deductions
    
    # Update or create stock record with net quantity (company-scoped)
    await db.stock.update_one(
        {"product_id": data.product_id, "company_id": company_id},
        {
            "$inc": {"quantity": net_quantity},
            "$set": {"product_name": data.product_name, "company_id": company_id, "updated_at": datetime.utcnow()}
        },
        upsert=True
    )
    
    # Log the receive movement
    movement = {
        "movement_type": "receive",
        "product_id": data.product_id,
        "product_name": data.product_name,
        "quantity": data.quantity,
        "net_quantity": net_quantity,
        "damages_in_transit": data.damages_in_transit,
        "rejected_stock": data.rejected_stock,
        "spoilt_from_factory": data.spoilt_from_factory,
        "crates_received": data.crates_received,
        "crates_returned": data.crates_returned,
        "supplier": data.supplier,
        "batch_reference": data.batch_reference,
        "notes": data.notes,
        "company_id": company_id,
        "personnel_id": current_user["id"],
        "personnel_name": current_user["name"],
        "created_at": datetime.utcnow()
    }
    await db.stock_movements.insert_one(movement)
    
    # Log deductions separately for accountability if any
    if data.damages_in_transit > 0:
        await db.stock_movements.insert_one({
            "movement_type": "damages_in_transit",
            "product_id": data.product_id,
            "product_name": data.product_name,
            "quantity": -data.damages_in_transit,
            "supplier": data.supplier,
            "batch_reference": data.batch_reference,
            "company_id": company_id,
            "personnel_id": current_user["id"],
            "personnel_name": current_user["name"],
            "created_at": datetime.utcnow()
        })
    
    if data.rejected_stock > 0:
        await db.stock_movements.insert_one({
            "movement_type": "rejected_stock",
            "product_id": data.product_id,
            "product_name": data.product_name,
            "quantity": -data.rejected_stock,
            "supplier": data.supplier,
            "batch_reference": data.batch_reference,
            "company_id": company_id,
            "personnel_id": current_user["id"],
            "personnel_name": current_user["name"],
            "created_at": datetime.utcnow()
        })
    
    if data.spoilt_from_factory > 0:
        await db.stock_movements.insert_one({
            "movement_type": "spoilt_from_factory",
            "product_id": data.product_id,
            "product_name": data.product_name,
            "quantity": -data.spoilt_from_factory,
            "supplier": data.supplier,
            "batch_reference": data.batch_reference,
            "company_id": company_id,
            "personnel_id": current_user["id"],
            "personnel_name": current_user["name"],
            "created_at": datetime.utcnow()
        })
    
    # Update global crates tracking
    if data.crates_received > 0 or data.crates_returned > 0:
        await db.crates_tracking.update_one(
            {"type": "global"},
            {
                "$inc": {
                    "crates_from_manufacturer": data.crates_received,
                    "crates_returned_to_manufacturer": data.crates_returned
                },
                "$set": {"updated_at": datetime.utcnow()}
            },
            upsert=True
        )
    
    # Get updated stock level
    stock = await db.stock.find_one({"product_id": data.product_id})
    
    return {
        "message": "Stock received successfully",
        "product_id": data.product_id,
        "product_name": data.product_name,
        "quantity_received": data.quantity,
        "damages_in_transit": data.damages_in_transit,
        "rejected_stock": data.rejected_stock,
        "spoilt_from_factory": data.spoilt_from_factory,
        "net_quantity_added": net_quantity,
        "crates_received": data.crates_received,
        "crates_returned": data.crates_returned,
        "new_total": stock.get("quantity", 0) if stock else net_quantity
    }

@api_router.post("/stock/adjustment")
async def adjust_stock(data: StockAdjustmentCreate, current_user: dict = Depends(get_current_user)):
    """Adjust stock for damages, spoilage, theft, etc. - Admin/Manager only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    company_id = current_user.get("company_id", "")
    
    # Get current stock (company-scoped)
    stock = await db.stock.find_one({"product_id": data.product_id, "company_id": company_id})
    current_qty = stock.get("quantity", 0) if stock else 0
    
    # Calculate new quantity
    new_qty = current_qty + data.adjustment_quantity
    if new_qty < 0:
        raise HTTPException(status_code=400, detail=f"Cannot adjust below zero. Current: {current_qty}, Adjustment: {data.adjustment_quantity}")
    
    # Update stock (company-scoped)
    await db.stock.update_one(
        {"product_id": data.product_id, "company_id": company_id},
        {
            "$set": {"quantity": new_qty, "product_name": data.product_name, "company_id": company_id, "updated_at": datetime.utcnow()}
        },
        upsert=True
    )
    
    # Log the movement
    movement = {
        "movement_type": "adjustment",
        "product_id": data.product_id,
        "product_name": data.product_name,
        "quantity": data.adjustment_quantity,
        "reason": data.reason,
        "notes": data.notes,
        "previous_quantity": current_qty,
        "new_quantity": new_qty,
        "company_id": company_id,
        "personnel_id": current_user["id"],
        "personnel_name": current_user["name"],
        "created_at": datetime.utcnow()
    }
    await db.stock_movements.insert_one(movement)
    
    return {
        "message": "Stock adjusted successfully",
        "product_id": data.product_id,
        "product_name": data.product_name,
        "adjustment": data.adjustment_quantity,
        "reason": data.reason,
        "previous_quantity": current_qty,
        "new_quantity": new_qty
    }

@api_router.post("/stock/take")
async def record_stock_take(data: StockTakeCreate, current_user: dict = Depends(get_current_user)):
    """Record stock take (physical count) - Admin/Manager only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    company_id = current_user.get("company_id", "")
    variance = data.physical_count - data.system_quantity
    
    # Update stock to physical count (company-scoped)
    await db.stock.update_one(
        {"product_id": data.product_id, "company_id": company_id},
        {
            "$set": {
                "quantity": data.physical_count,
                "product_name": data.product_name,
                "company_id": company_id,
                "updated_at": datetime.utcnow(),
                "last_stock_take": datetime.utcnow()
            }
        },
        upsert=True
    )
    
    # Log the stock take
    stock_take_record = {
        "movement_type": "stock_take",
        "product_id": data.product_id,
        "product_name": data.product_name,
        "system_quantity": data.system_quantity,
        "physical_count": data.physical_count,
        "variance": variance,
        "variance_reason": data.variance_reason,
        "company_id": company_id,
        "personnel_id": current_user["id"],
        "personnel_name": current_user["name"],
        "created_at": datetime.utcnow()
    }
    await db.stock_movements.insert_one(stock_take_record)
    
    return {
        "message": "Stock take recorded",
        "product_id": data.product_id,
        "product_name": data.product_name,
        "system_quantity": data.system_quantity,
        "physical_count": data.physical_count,
        "variance": variance,
        "variance_reason": data.variance_reason
    }

@api_router.get("/stock/movements")
async def get_stock_movements(
    product_id: Optional[str] = None,
    movement_type: Optional[str] = None,
    days: int = 30,
    current_user: dict = Depends(get_current_user)
):
    """Get stock movement history (company-scoped)"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    start_date = datetime.utcnow() - timedelta(days=days)
    query = {"created_at": {"$gte": start_date}}
    
    # Company isolation
    cf = get_company_filter(current_user)
    query.update(cf)
    
    if product_id:
        query["product_id"] = product_id
    if movement_type:
        query["movement_type"] = movement_type
    
    movements = await db.stock_movements.find(query).sort("created_at", -1).to_list(500)
    return [str_id(m) for m in movements]

@api_router.get("/stock/report")
async def get_stock_report(current_user: dict = Depends(get_current_user)):
    """Generate stock report with opening, received, sold, adjustments, closing, and variances (company-scoped)"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    cf = get_company_filter(current_user)
    company_id = current_user.get("company_id", "")
    products = await db.products.find(cf).to_list(500)
    report = []
    
    # Get date range for this week (Monday to now)
    today = datetime.utcnow()
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    
    total_damages_transit = 0
    total_rejected = 0
    total_spoilt = 0
    total_stock_take_variance = 0
    
    for product in products:
        product_id = str(product["_id"])
        
        # Get movements for this product this week (company-scoped)
        movement_query = {
            "product_id": product_id,
            "created_at": {"$gte": week_start}
        }
        if company_id:
            movement_query["company_id"] = company_id
        movements = await db.stock_movements.find(movement_query).to_list(500)
        
        # Calculate totals by movement type
        received = sum(m.get("net_quantity", m.get("quantity", 0)) for m in movements if m.get("movement_type") == "receive")
        adjustments = sum(m.get("quantity", 0) for m in movements if m.get("movement_type") == "adjustment")
        damages_transit = sum(abs(m.get("quantity", 0)) for m in movements if m.get("movement_type") == "damages_in_transit")
        rejected = sum(abs(m.get("quantity", 0)) for m in movements if m.get("movement_type") == "rejected_stock")
        spoilt = sum(abs(m.get("quantity", 0)) for m in movements if m.get("movement_type") == "spoilt_from_factory")
        
        # Stock take variances
        stock_takes = [m for m in movements if m.get("movement_type") == "stock_take"]
        variance = sum(m.get("variance", 0) for m in stock_takes)
        
        total_damages_transit += damages_transit
        total_rejected += rejected
        total_spoilt += spoilt
        total_stock_take_variance += variance
        
        # Get sales from stock movements (now tracked there)
        sold = abs(sum(m.get("quantity", 0) for m in movements if m.get("movement_type") == "sale"))
        
        # If no sales in movements, check sales collection
        if sold == 0:
            sales_query_sr = {
                "created_at": {"$gte": week_start},
                "is_voided": {"$ne": True}
            }
            if company_id:
                sales_query_sr["company_id"] = company_id
            sales = await db.sales.find(sales_query_sr).to_list(2000)
            
            for sale in sales:
                for item in sale.get("items", []):
                    if item.get("product_id") == product_id:
                        sold += (item.get("quantity_delivered", 0) - item.get("quantity_returned", 0))
        
        # Current stock (company-scoped)
        stock = await db.stock.find_one({"product_id": product_id, "company_id": company_id})
        closing = stock.get("quantity", 0) if stock else 0
        
        # Calculate opening (closing - received - adjustments + sold + damages + rejected + spoilt)
        opening = closing - received - adjustments + sold + damages_transit + rejected + spoilt
        
        report.append({
            "product_id": product_id,
            "product_name": product["name"],
            "category": product["category"],
            "opening_stock": max(0, opening),
            "received": received,
            "sold": sold,
            "adjustments": adjustments,
            "damages_in_transit": damages_transit,
            "rejected_stock": rejected,
            "spoilt_from_factory": spoilt,
            "stock_take_variance": variance,
            "closing_stock": closing
        })
    
    # Get crates tracking
    crates = await db.crates_tracking.find_one({"type": "global"})
    
    return {
        "report_date": today.isoformat(),
        "week_start": week_start.isoformat(),
        "products": report,
        "summary": {
            "total_products": len(report),
            "total_received": sum(p["received"] for p in report),
            "total_sold": sum(p["sold"] for p in report),
            "total_adjustments": sum(p["adjustments"] for p in report),
            "total_damages_in_transit": total_damages_transit,
            "total_rejected_stock": total_rejected,
            "total_spoilt_from_factory": total_spoilt,
            "total_stock_take_variance": total_stock_take_variance
        },
        "crates": {
            "from_manufacturer": crates.get("crates_from_manufacturer", 0) if crates else 0,
            "returned_to_manufacturer": crates.get("crates_returned_to_manufacturer", 0) if crates else 0,
            "net_crates": (crates.get("crates_from_manufacturer", 0) - crates.get("crates_returned_to_manufacturer", 0)) if crates else 0
        }
    }

@api_router.post("/stock/seed")
async def seed_stock():
    """Seed initial stock levels"""
    products = await db.products.find().to_list(100)
    
    for product in products:
        product_id = str(product["_id"])
        # Set initial stock level
        await db.stock.update_one(
            {"product_id": product_id},
            {
                "$set": {
                    "product_id": product_id,
                    "product_name": product["name"],
                    "quantity": 100,  # Default starting stock
                    "updated_at": datetime.utcnow()
                }
            },
            upsert=True
        )
    
    return {"message": f"Seeded stock for {len(products)} products"}

# ==================== PDF EXPORT ====================

@api_router.get("/reports/export/pdf")
async def export_report_pdf(
    date_str: Optional[str] = None,
    route_id: Optional[str] = None,
    driver_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Export route report to PDF format with filters"""
    if not date_str:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    # Build query based on filters
    query = {"date": date_str}
    if route_id:
        query["route_id"] = route_id
    if driver_id and is_admin_or_manager(current_user):
        query["driver_id"] = driver_id
    elif current_user["role"] == "driver":
        query["driver_id"] = current_user["id"]
    
    daily_routes = await db.daily_routes.find(query).to_list(100)
    
    # Get sales
    start = datetime.strptime(date_str, "%Y-%m-%d")
    end = start.replace(hour=23, minute=59, second=59)
    
    sales_query = {"created_at": {"$gte": start, "$lte": end}, "is_voided": {"$ne": True}}
    if route_id:
        sales_query["route_id"] = route_id
    if customer_id:
        sales_query["customer_id"] = customer_id
    if driver_id and is_admin_or_manager(current_user):
        sales_query["driver_id"] = driver_id
    elif current_user["role"] == "driver":
        sales_query["driver_id"] = current_user["id"]
    
    sales = await db.sales.find(sales_query).to_list(1000)
    
    # Create PDF - use landscape for more room
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=30, bottomMargin=30, leftMargin=30, rightMargin=30)
    styles = getSampleStyleSheet()
    elements = []
    
    # Custom styles
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=22, spaceAfter=6, alignment=1, textColor=colors.HexColor('#3B82F6'))
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'], fontSize=12, spaceAfter=10, alignment=1, textColor=colors.HexColor('#64748B'))
    section_style = ParagraphStyle('Section', parent=styles['Heading2'], fontSize=14, spaceBefore=20, spaceAfter=10, textColor=colors.HexColor('#1E293B'))
    
    # Header
    elements.append(Paragraph("Mzansi FMCG Tracker", title_style))
    elements.append(Paragraph(f"Route Sales Report - {date_str}", subtitle_style))
    elements.append(Spacer(1, 15))
    
    # Summary section
    total_sales = len(sales)
    total_collected = sum(s.get('cash_collected', 0) for s in sales)
    total_expected = sum(s.get('total_amount', 0) for s in sales)
    total_shortage = sum(s.get('shortage_amount', 0) for s in sales)
    total_crates_out = sum(s.get('crates_dropped', 0) for s in sales)
    total_crates_in = sum(s.get('crates_collected', 0) for s in sales)
    
    elements.append(Paragraph("Summary", section_style))
    summary_data = [
        ['Total Sales', 'Total Expected', 'Total Collected', 'Total Shortage', 'Crates Out', 'Crates In', 'Collection Rate'],
        [str(total_sales), f'R {total_expected:.2f}', f'R {total_collected:.2f}', f'R {total_shortage:.2f}', 
         str(total_crates_out), str(total_crates_in), f'{(total_collected/total_expected*100) if total_expected > 0 else 0:.1f}%']
    ]
    summary_table = Table(summary_data, colWidths=[90, 90, 90, 90, 80, 80, 90])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3B82F6')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F8FAFC')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#E2E8F0')),
        ('FONTSIZE', (0, 1), (-1, -1), 12),
        ('TOPPADDING', (0, 1), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 10),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 15))
    
    # Sales Detail section
    if sales:
        elements.append(Paragraph("Sales Details", section_style))
        sales_data = [['Invoice', 'Customer', 'Driver', 'Items', 'Amount', 'Received', 'Shortage', 'Payment', 'Notes']]
        for sale in sales[:50]:
            items_list = []
            for item in sale.get('items', []):
                delivered = item.get('quantity_delivered', 0)
                returned = item.get('quantity_returned', 0)
                damages = item.get('damages', 0)
                net = delivered - returned
                items_list.append(f"{item.get('product_name', '?')} (Net:{net})")
            
            payment_type = sale.get('payment_type', 'cash').upper()
            if payment_type == 'SPLIT' and sale.get('split_payments'):
                split_parts = []
                for sp in sale.get('split_payments', []):
                    split_parts.append(f"{sp.get('method','?').title()}:R{sp.get('amount',0):.0f}")
                payment_display = ', '.join(split_parts) if split_parts else 'SPLIT'
            else:
                payment_display = payment_type
            
            notes = sale.get('notes', '') or ''
            shortage = sale.get('shortage_amount', 0)
            
            sales_data.append([
                sale.get('invoice_number', 'N/A')[:15],
                sale.get('customer_name', 'N/A')[:15],
                sale.get('driver_name', 'N/A')[:12],
                ', '.join(items_list)[:25],
                f"R {sale.get('total_amount', 0):.2f}",
                f"R {sale.get('cash_collected', 0):.2f}",
                f"R {shortage:.2f}",
                payment_display[:20],
                notes[:15]
            ])
        
        sales_table = Table(sales_data, colWidths=[75, 70, 60, 120, 55, 55, 55, 90, 70])
        sales_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10B981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (3, 1), (3, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('FONTSIZE', (0, 1), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(sales_table)
    
    # Route Summary section
    if daily_routes:
        elements.append(Spacer(1, 20))
        elements.append(Paragraph("Route Summary", section_style))
        route_data = [['Route', 'Driver', 'Vehicle', 'Status', 'Sales', 'Collected', 'Shortage', 'Crates Out', 'Crates In']]
        for dr in daily_routes:
            route_data.append([
                dr.get('route_name', 'N/A')[:12],
                dr.get('driver_name', 'N/A')[:10],
                dr.get('vehicle_name', 'N/A')[:10],
                dr.get('status', 'N/A'),
                str(dr.get('sales_count', 0)),
                f"R {dr.get('total_collected', 0):.2f}",
                f"R {dr.get('total_shortage', 0):.2f}",
                str(dr.get('crates_out', 0)),
                str(dr.get('crates_in', 0) or 0)
            ])
        
        route_table = Table(route_data, colWidths=[70, 60, 60, 50, 40, 65, 65, 55, 55])
        route_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F59E0B')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 7),
            ('FONTSIZE', (0, 1), (-1, -1), 7),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FEF3C7')]),
        ]))
        elements.append(route_table)
    
    # Footer
    elements.append(Spacer(1, 30))
    footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=8, alignment=1, textColor=colors.HexColor('#94A3B8'))
    elements.append(Paragraph(f"Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC", footer_style))
    elements.append(Paragraph("Mzansi FMCG Tracker - Powered by Emergent", footer_style))
    
    doc.build(elements)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=sales_report_{date_str}.pdf"}
    )

# ==================== EMAIL RECIPIENTS MANAGEMENT ====================

class EmailRecipient(BaseModel):
    email: str
    name: Optional[str] = None
    report_types: List[str] = []  # e.g., ['sales', 'stock', 'summary']
    is_active: bool = True

class EmailRecipientCreate(BaseModel):
    email: str
    name: Optional[str] = None
    report_types: List[str] = []

class EmailConfig(BaseModel):
    sender_email: str
    sender_password: str
    smtp_server: str = "mail.mzansipc.co.za"
    smtp_port: int = 465

@api_router.get("/admin/settings/email")
async def get_email_settings(current_user: dict = Depends(get_current_user)):
    """Get email configuration - Admin only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    config = await db.settings.find_one({"type": "email_config"})
    if not config:
        return {"configured": False}
    
    # Don't expose password
    return {
        "configured": True,
        "sender_email": config.get("sender_email"),
        "smtp_server": config.get("smtp_server"),
        "smtp_port": config.get("smtp_port")
    }

@api_router.post("/admin/settings/email")
async def save_email_settings(config: EmailConfig, current_user: dict = Depends(get_current_user)):
    """Save email configuration - Admin only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    await db.settings.update_one(
        {"type": "email_config"},
        {"$set": {
            "type": "email_config",
            "sender_email": config.sender_email,
            "sender_password": config.sender_password,
            "smtp_server": config.smtp_server,
            "smtp_port": config.smtp_port,
            "updated_at": datetime.utcnow(),
            "updated_by": current_user["id"]
        }},
        upsert=True
    )
    
    return {"message": "Email configuration saved successfully"}

@api_router.get("/admin/email-recipients")
async def get_email_recipients(current_user: dict = Depends(get_current_user)):
    """Get all email recipients - Admin only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    recipients = await db.email_recipients.find().to_list(100)
    return [str_id(r) for r in recipients]

@api_router.post("/admin/email-recipients")
async def add_email_recipient(data: EmailRecipientCreate, current_user: dict = Depends(get_current_user)):
    """Add a new email recipient - Admin only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Check if email already exists
    existing = await db.email_recipients.find_one({"email": data.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email recipient already exists")
    
    recipient = {
        "email": data.email,
        "name": data.name,
        "report_types": data.report_types,
        "is_active": True,
        "created_at": datetime.utcnow(),
        "created_by": current_user["id"]
    }
    
    result = await db.email_recipients.insert_one(recipient)
    recipient["_id"] = result.inserted_id
    
    return str_id(recipient)

@api_router.put("/admin/email-recipients/{recipient_id}")
async def update_email_recipient(recipient_id: str, data: EmailRecipientCreate, current_user: dict = Depends(get_current_user)):
    """Update an email recipient - Admin only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.email_recipients.update_one(
        {"_id": ObjectId(recipient_id)},
        {"$set": {
            "email": data.email,
            "name": data.name,
            "report_types": data.report_types,
            "updated_at": datetime.utcnow()
        }}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    return {"message": "Recipient updated"}

@api_router.delete("/admin/email-recipients/{recipient_id}")
async def delete_email_recipient(recipient_id: str, current_user: dict = Depends(get_current_user)):
    """Delete an email recipient - Admin only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.email_recipients.delete_one({"_id": ObjectId(recipient_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    return {"message": "Recipient deleted"}

@api_router.post("/admin/email-recipients/{recipient_id}/toggle")
async def toggle_email_recipient(recipient_id: str, current_user: dict = Depends(get_current_user)):
    """Toggle email recipient active status - Admin only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    recipient = await db.email_recipients.find_one({"_id": ObjectId(recipient_id)})
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    new_status = not recipient.get("is_active", True)
    await db.email_recipients.update_one(
        {"_id": ObjectId(recipient_id)},
        {"$set": {"is_active": new_status}}
    )
    
    return {"message": f"Recipient {'activated' if new_status else 'deactivated'}", "is_active": new_status}

# ==================== AUTOMATED REPORTS ====================

@api_router.post("/admin/send-report")
async def send_report_email(
    report_type: str,  # 'sales', 'stock', 'summary'
    date_str: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Send report to configured email recipients - Admin only"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    if not date_str:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    # Get email config
    email_config = await db.settings.find_one({"type": "email_config"})
    if not email_config:
        raise HTTPException(status_code=400, detail="Email not configured. Please set up email settings first.")
    
    # Get recipients for this report type
    recipients = await db.email_recipients.find({
        "is_active": True,
        "report_types": report_type
    }).to_list(50)
    
    if not recipients:
        raise HTTPException(status_code=400, detail=f"No active recipients configured for {report_type} reports")
    
    # Generate report based on type
    if report_type == 'stock':
        # Generate stock report
        report_data = await get_stock_report(current_user)
        subject = f"Stock Report - {date_str}"
        body = f"""
        <h2>Weekly Stock Report</h2>
        <p><strong>Report Date:</strong> {date_str}</p>
        <p><strong>Week Start:</strong> {report_data.get('week_start', 'N/A')}</p>
        <h3>Summary</h3>
        <ul>
            <li>Total Products: {report_data['summary']['total_products']}</li>
            <li>Total Received: {report_data['summary']['total_received']}</li>
            <li>Total Sold: {report_data['summary']['total_sold']}</li>
            <li>Total Adjustments: {report_data['summary']['total_adjustments']}</li>
        </ul>
        <h3>Product Details</h3>
        <table border="1" style="border-collapse: collapse;">
            <tr style="background-color: #3B82F6; color: white;">
                <th>Product</th><th>Opening</th><th>Received</th><th>Sold</th><th>Adjustments</th><th>Closing</th>
            </tr>
        """
        for p in report_data.get('products', []):
            body += f"""
            <tr>
                <td>{p['product_name']}</td>
                <td>{p['opening_stock']}</td>
                <td>{p['received']}</td>
                <td>{p['sold']}</td>
                <td>{p['adjustments']}</td>
                <td>{p['closing_stock']}</td>
            </tr>
            """
        body += "</table>"
    else:
        # Generate sales report
        start = datetime.strptime(date_str, "%Y-%m-%d")
        end = start.replace(hour=23, minute=59, second=59)
        sales = await db.sales.find({
            "created_at": {"$gte": start, "$lte": end},
            "is_voided": {"$ne": True}
        }).to_list(1000)
        
        total_expected = sum(s.get('total_amount', 0) for s in sales)
        total_collected = sum(s.get('cash_collected', 0) for s in sales)
        total_shortage = sum(s.get('shortage_amount', 0) for s in sales)
        
        subject = f"Sales Report - {date_str}"
        body = f"""
        <h2>Daily Sales Report</h2>
        <p><strong>Date:</strong> {date_str}</p>
        <h3>Summary</h3>
        <ul>
            <li><strong>Total Sales:</strong> {len(sales)}</li>
            <li><strong>Total Expected:</strong> R {total_expected:.2f}</li>
            <li><strong>Total Collected:</strong> R {total_collected:.2f}</li>
            <li><strong>Total Shortage:</strong> R {total_shortage:.2f}</li>
        </ul>
        """
    
    # Send emails
    sent_count = 0
    failed = []
    
    for recipient in recipients:
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = email_config.get('sender_email')
            msg['To'] = recipient['email']
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'html'))
            
            smtp_port = email_config.get('smtp_port', 465)
            smtp_server = email_config.get('smtp_server', 'mail.mzansipc.co.za')
            
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
            
            server.login(email_config.get('sender_email'), email_config.get('sender_password'))
            server.send_message(msg)
            server.quit()
            sent_count += 1
        except Exception as e:
            failed.append({"email": recipient['email'], "error": str(e)})
    
    return {
        "message": f"Report sent to {sent_count} recipients",
        "sent": sent_count,
        "failed": failed
    }

# ==================== CLEAR DATA ====================

@api_router.post("/admin/clear-data")
async def clear_all_data(current_user: dict = Depends(get_current_user)):
    """Clear all practice/demo data - Admin only"""
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Clear transactional data only (keep users, products, routes, customers)
    await db.sales.delete_many({})
    await db.daily_routes.delete_many({})
    await db.stock_movements.delete_many({})
    await db.stock.delete_many({})
    await db.crates_tracking.delete_many({})
    await db.email_logs.delete_many({})
    
    return {
        "message": "All practice data cleared successfully",
        "cleared": ["sales", "daily_routes", "stock_movements", "stock", "crates_tracking", "email_logs"]
    }

# ==================== ORDERING SYSTEM ====================

# --- Order Models ---
class CustomerRegister(BaseModel):
    business_name: str
    contact_person: str
    phone: str
    pin: str
    delivery_address: Optional[str] = None
    province: Optional[str] = None
    district: Optional[str] = None
    city: Optional[str] = None
    company_id: Optional[str] = None  # optional - marketplace model allows browsing all
    route_id: Optional[str] = None    # optional - matched by location

class DeliverySchedule(BaseModel):
    delivery_days: List[str] = []  # e.g. ["Monday", "Thursday"]
    cut_off_hours_before: int = 16  # hours before delivery day to cut off orders
    cut_off_time: str = "16:00"    # display time

class OrderItemCreate(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: float

class OrderCreate(BaseModel):
    company_id: str
    items: List[OrderItemCreate]
    notes: Optional[str] = None

class OrderAdjustItem(BaseModel):
    product_id: str
    product_name: str
    original_quantity: int
    adjusted_quantity: int
    unit_price: float
    reason: Optional[str] = None

class OrderAdjust(BaseModel):
    items: List[OrderAdjustItem]
    adjustment_reason: Optional[str] = None

ORDER_STATUSES = ["pending", "confirmed", "adjusted", "packed", "out_for_delivery", "delivered", "cancelled"]

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ==================== SOUTH AFRICA LOCATION DATA ====================
SA_LOCATIONS = {
    "Gauteng": {
        "City of Johannesburg": ["Soweto", "Sandton", "Randburg", "Roodepoort", "Alexandra", "Midrand", "Johannesburg CBD", "Diepsloot", "Orange Farm"],
        "City of Tshwane": ["Pretoria CBD", "Centurion", "Mamelodi", "Atteridgeville", "Soshanguve", "Hammanskraal", "Ga-Rankuwa"],
        "Ekurhuleni": ["Germiston", "Boksburg", "Benoni", "Springs", "Brakpan", "Alberton", "Kempton Park", "Tembisa", "Katlehong"],
        "Sedibeng": ["Vereeniging", "Vanderbijlpark", "Meyerton", "Heidelberg", "Evaton"],
        "West Rand": ["Randfontein", "Krugersdorp", "Westonaria", "Carletonville", "Kagiso"],
    },
    "KwaZulu-Natal": {
        "eThekwini": ["Durban CBD", "Umlazi", "Chatsworth", "Phoenix", "Pinetown", "KwaMashu", "Ntuzuma", "Inanda"],
        "uMgungundlovu": ["Pietermaritzburg", "Richmond", "Howick", "Hilton", "Edendale"],
        "King Cetshwayo": ["Richards Bay", "Empangeni", "Mtunzini", "Eshowe"],
        "iLembe": ["KwaDukuza", "Stanger", "Ballito", "Mandeni", "Darnall"],
        "Ugu": ["Port Shepstone", "Margate", "Scottburgh", "Hibberdene", "Umzinto"],
        "uMkhanyakude": ["Hluhluwe", "Jozini", "Mtubatuba", "Mbazwana"],
    },
    "Western Cape": {
        "City of Cape Town": ["Cape Town CBD", "Khayelitsha", "Mitchells Plain", "Gugulethu", "Nyanga", "Bellville", "Delft", "Langa", "Atlantis"],
        "Cape Winelands": ["Stellenbosch", "Paarl", "Franschhoek", "Worcester", "Wellington"],
        "Overberg": ["Hermanus", "Caledon", "Bredasdorp", "Swellendam"],
        "Garden Route": ["George", "Knysna", "Plettenberg Bay", "Mossel Bay", "Oudtshoorn"],
        "West Coast": ["Saldanha", "Langebaan", "Vredenburg", "Malmesbury"],
    },
    "Eastern Cape": {
        "Nelson Mandela Bay": ["Port Elizabeth", "Uitenhage", "Despatch", "KwaNobuhle"],
        "Buffalo City": ["East London", "Mdantsane", "King William's Town", "Bhisho"],
        "OR Tambo": ["Mthatha", "Lusikisiki", "Port St Johns", "Libode", "Tsolo"],
        "Amathole": ["Fort Beaufort", "Alice", "Stutterheim", "Keiskammahoek"],
        "Chris Hani": ["Queenstown", "Cradock", "Cofimvaba", "Lady Frere"],
    },
    "Limpopo": {
        "Capricorn": ["Polokwane", "Mankweng", "Seshego", "Lebowakgomo"],
        "Vhembe": ["Thohoyandou", "Louis Trichardt", "Musina", "Malamulele"],
        "Mopani": ["Tzaneen", "Phalaborwa", "Modjadjiskloof", "Giyani", "Nkowankowa"],
        "Sekhukhune": ["Jane Furse", "Burgersfort", "Groblersdal", "Marble Hall"],
        "Waterberg": ["Mokopane", "Lephalale", "Modimolle", "Bela-Bela", "Thabazimbi"],
    },
    "Mpumalanga": {
        "Ehlanzeni": ["Nelspruit", "White River", "Hazyview", "Barberton", "Malelane", "Komatipoort"],
        "Nkangala": ["Witbank", "Middelburg", "Secunda", "Standerton", "Bethal"],
        "Gert Sibande": ["Ermelo", "Piet Retief", "Volksrust", "Amersfoort", "Balfour"],
    },
    "North West": {
        "Bojanala Platinum": ["Rustenburg", "Brits", "Mogwase", "Sun City", "Phokeng"],
        "Ngaka Modiri Molema": ["Mahikeng", "Lichtenburg", "Zeerust", "Coligny"],
        "Dr Kenneth Kaunda": ["Klerksdorp", "Potchefstroom", "Orkney", "Stilfontein"],
        "Dr Ruth Segomotsi Mompati": ["Vryburg", "Taung", "Christiana", "Schweizer-Reneke"],
    },
    "Free State": {
        "Mangaung": ["Bloemfontein", "Botshabelo", "Thaba Nchu"],
        "Fezile Dabi": ["Sasolburg", "Kroonstad", "Parys", "Heilbron"],
        "Lejweleputswa": ["Welkom", "Virginia", "Odendaalsrus", "Hennenman"],
        "Thabo Mofutsanyana": ["Bethlehem", "Harrismith", "QwaQwa", "Phuthaditjhaba"],
    },
    "Northern Cape": {
        "Frances Baard": ["Kimberley", "Barkly West", "Warrenton"],
        "John Taolo Gaetsewe": ["Kuruman", "Kathu", "Postmasburg"],
        "ZF Mgcawu": ["Upington", "Keimoes", "Kakamas"],
        "Namakwa": ["Springbok", "Port Nolloth", "Kleinsee"],
    },
}

@api_router.get("/locations/provinces")
async def get_provinces():
    """Get all SA provinces"""
    return list(SA_LOCATIONS.keys())

@api_router.get("/locations/districts/{province}")
async def get_districts(province: str):
    """Get districts for a province"""
    if province not in SA_LOCATIONS:
        raise HTTPException(status_code=404, detail="Province not found")
    return list(SA_LOCATIONS[province].keys())

@api_router.get("/locations/areas/{province}/{district}")
async def get_areas(province: str, district: str):
    """Get areas/towns for a district"""
    if province not in SA_LOCATIONS:
        raise HTTPException(status_code=404, detail="Province not found")
    if district not in SA_LOCATIONS[province]:
        raise HTTPException(status_code=404, detail="District not found")
    return SA_LOCATIONS[province][district]

# --- Database Reset & Clean Seed ---
@api_router.post("/admin/reset-and-seed")
async def reset_and_seed():
    """Clear entire database and seed with clean demo data"""
    # Drop all collections
    collections = await db.list_collection_names()
    for coll in collections:
        await db[coll].drop()
    
    # === COMPANY 1: User's company (Mzansi Distribution) ===
    comp1 = await db.companies.insert_one({
        "name": "Mzansi Distribution",
        "contact_person": "Owner",
        "phone": "0767862760",
        "email": "info@mzansidistribution.co.za",
        "address": "Johannesburg, Gauteng",
        "province": "Gauteng",
        "created_at": datetime.utcnow()
    })
    comp1_id = str(comp1.inserted_id)
    
    # Admin for Mzansi Distribution
    await db.users.insert_one({
        "name": "Admin",
        "phone": "0767862760",
        "pin_hash": hash_pin("1984"),
        "role": "admin",
        "is_active": True,
        "company_id": comp1_id,
        "created_at": datetime.utcnow()
    })
    
    # Driver for Mzansi Distribution
    await db.users.insert_one({
        "name": "Sipho Driver",
        "phone": "0812345001",
        "pin_hash": hash_pin("1234"),
        "role": "driver",
        "is_active": True,
        "company_id": comp1_id,
        "created_at": datetime.utcnow()
    })
    
    # Routes for Mzansi Distribution
    r1 = await db.routes.insert_one({
        "name": "Soweto & Surrounds",
        "description": "Soweto, Orange Farm and surrounding areas",
        "company_id": comp1_id,
        "province": "Gauteng",
        "district": "City of Johannesburg",
        "areas_covered": ["Soweto", "Orange Farm", "Diepsloot"],
        "delivery_schedule": {"delivery_days": ["Monday", "Wednesday", "Friday"], "cut_off_hours_before": 16, "cut_off_time": "16:00"},
        "created_at": datetime.utcnow()
    })
    r2 = await db.routes.insert_one({
        "name": "Pretoria Route",
        "description": "Pretoria CBD, Mamelodi and surrounds",
        "company_id": comp1_id,
        "province": "Gauteng",
        "district": "City of Tshwane",
        "areas_covered": ["Pretoria CBD", "Mamelodi", "Atteridgeville", "Soshanguve"],
        "delivery_schedule": {"delivery_days": ["Tuesday", "Thursday"], "cut_off_hours_before": 14, "cut_off_time": "14:00"},
        "created_at": datetime.utcnow()
    })
    
    # Products for Mzansi Distribution
    products1 = [
        {"name": "White Bread", "category": "Bakery", "unit_type": "loaf", "price": 18.50, "company_id": comp1_id, "created_at": datetime.utcnow()},
        {"name": "Brown Bread", "category": "Bakery", "unit_type": "loaf", "price": 16.00, "company_id": comp1_id, "created_at": datetime.utcnow()},
        {"name": "Full Cream Milk 2L", "category": "Dairy", "unit_type": "bottle", "price": 32.00, "company_id": comp1_id, "created_at": datetime.utcnow()},
        {"name": "Amasi 1L", "category": "Dairy", "unit_type": "bottle", "price": 22.50, "company_id": comp1_id, "created_at": datetime.utcnow()},
        {"name": "Large Eggs (30)", "category": "Eggs", "unit_type": "tray", "price": 65.00, "company_id": comp1_id, "created_at": datetime.utcnow()},
        {"name": "Sunflower Oil 750ml", "category": "Cooking", "unit_type": "bottle", "price": 45.00, "company_id": comp1_id, "created_at": datetime.utcnow()},
        {"name": "Maize Meal 5kg", "category": "Staples", "unit_type": "bag", "price": 55.00, "company_id": comp1_id, "created_at": datetime.utcnow()},
        {"name": "Sugar 2kg", "category": "Staples", "unit_type": "bag", "price": 38.50, "company_id": comp1_id, "created_at": datetime.utcnow()},
    ]
    await db.products.insert_many(products1)
    
    # Vehicles for Mzansi Distribution
    await db.vehicles.insert_many([
        {"registration": "GP 123 ABC", "name": "Truck 1 - Toyota Dyna", "vehicle_type": "truck", "capacity_crates": 120, "is_active": True, "company_id": comp1_id, "created_at": datetime.utcnow()},
        {"registration": "GP 456 DEF", "name": "Truck 2 - Isuzu NPR", "vehicle_type": "truck", "capacity_crates": 150, "is_active": True, "company_id": comp1_id, "created_at": datetime.utcnow()},
    ])
    
    # === COMPANY 2: Fresh Foods SA ===
    comp2 = await db.companies.insert_one({
        "name": "Fresh Foods SA",
        "contact_person": "James Moyo",
        "phone": "0711002001",
        "email": "james@freshfoods.co.za",
        "address": "Durban, KwaZulu-Natal",
        "province": "KwaZulu-Natal",
        "created_at": datetime.utcnow()
    })
    comp2_id = str(comp2.inserted_id)
    
    await db.users.insert_one({
        "name": "James Moyo",
        "phone": "0711002001",
        "pin_hash": hash_pin("2222"),
        "role": "admin",
        "is_active": True,
        "company_id": comp2_id,
        "created_at": datetime.utcnow()
    })
    
    r3 = await db.routes.insert_one({
        "name": "Durban Central",
        "description": "Durban CBD, Umlazi, Chatsworth",
        "company_id": comp2_id,
        "province": "KwaZulu-Natal",
        "district": "eThekwini",
        "areas_covered": ["Durban CBD", "Umlazi", "Chatsworth", "Phoenix", "KwaMashu"],
        "delivery_schedule": {"delivery_days": ["Monday", "Thursday"], "cut_off_hours_before": 12, "cut_off_time": "12:00"},
        "created_at": datetime.utcnow()
    })
    
    products2 = [
        {"name": "Fresh Chicken 1.5kg", "category": "Poultry", "unit_type": "pack", "price": 75.00, "company_id": comp2_id, "created_at": datetime.utcnow()},
        {"name": "Beef Mince 500g", "category": "Meat", "unit_type": "pack", "price": 55.00, "company_id": comp2_id, "created_at": datetime.utcnow()},
        {"name": "Pap 2.5kg", "category": "Staples", "unit_type": "bag", "price": 29.00, "company_id": comp2_id, "created_at": datetime.utcnow()},
        {"name": "Tinned Pilchards", "category": "Canned", "unit_type": "tin", "price": 18.50, "company_id": comp2_id, "created_at": datetime.utcnow()},
        {"name": "Cooking Oil 2L", "category": "Cooking", "unit_type": "bottle", "price": 69.00, "company_id": comp2_id, "created_at": datetime.utcnow()},
    ]
    await db.products.insert_many(products2)
    
    await db.vehicles.insert_one({
        "registration": "KZN 789 GHI", "name": "Van 1 - Hyundai HD72", "vehicle_type": "van", "capacity_crates": 80, "is_active": True, "company_id": comp2_id, "created_at": datetime.utcnow()
    })
    
    # === COMPANY 3: Cape Traders ===
    comp3 = await db.companies.insert_one({
        "name": "Cape Traders",
        "contact_person": "Sarah van Wyk",
        "phone": "0722003001",
        "email": "sarah@capetraders.co.za",
        "address": "Cape Town, Western Cape",
        "province": "Western Cape",
        "created_at": datetime.utcnow()
    })
    comp3_id = str(comp3.inserted_id)
    
    await db.users.insert_one({
        "name": "Sarah van Wyk",
        "phone": "0722003001",
        "pin_hash": hash_pin("3333"),
        "role": "admin",
        "is_active": True,
        "company_id": comp3_id,
        "created_at": datetime.utcnow()
    })
    
    r4 = await db.routes.insert_one({
        "name": "Cape Flats Route",
        "description": "Khayelitsha, Mitchells Plain, Gugulethu",
        "company_id": comp3_id,
        "province": "Western Cape",
        "district": "City of Cape Town",
        "areas_covered": ["Khayelitsha", "Mitchells Plain", "Gugulethu", "Nyanga", "Langa"],
        "delivery_schedule": {"delivery_days": ["Wednesday", "Saturday"], "cut_off_hours_before": 18, "cut_off_time": "18:00"},
        "created_at": datetime.utcnow()
    })
    
    products3 = [
        {"name": "Biltong 100g", "category": "Snacks", "unit_type": "pack", "price": 45.00, "company_id": comp3_id, "created_at": datetime.utcnow()},
        {"name": "Rooibos Tea (40 bags)", "category": "Beverages", "unit_type": "box", "price": 35.00, "company_id": comp3_id, "created_at": datetime.utcnow()},
        {"name": "Coke 2L", "category": "Beverages", "unit_type": "bottle", "price": 24.00, "company_id": comp3_id, "created_at": datetime.utcnow()},
        {"name": "Simba Chips (36 pack)", "category": "Snacks", "unit_type": "box", "price": 120.00, "company_id": comp3_id, "created_at": datetime.utcnow()},
    ]
    await db.products.insert_many(products3)
    
    # Vehicle for Cape Traders
    await db.vehicles.insert_one({
        "registration": "CA 321 JKL", "name": "Bakkie 1 - Toyota Hilux", "vehicle_type": "bakkie", "capacity_crates": 60, "is_active": True, "company_id": comp3_id, "created_at": datetime.utcnow()
    })
    
    # === CUSTOMER 1: Thabo's Spaza (Soweto - MARKETPLACE: not tied to single company) ===
    cust1 = await db.users.insert_one({
        "name": "Thabo Mokoena",
        "phone": "0831001001",
        "pin_hash": hash_pin("1111"),
        "role": "customer",
        "is_active": True,
        "company_id": "",
        "customer_profile": {
            "business_name": "Thabo's Spaza Shop",
            "contact_person": "Thabo Mokoena",
            "delivery_address": "123 Vilakazi St, Soweto",
            "province": "Gauteng",
            "district": "City of Johannesburg",
            "city": "Soweto",
        },
        "created_at": datetime.utcnow()
    })
    
    # === CUSTOMER 2: Nomsa's Tuck Shop (Durban - MARKETPLACE: not tied to single company) ===
    cust2 = await db.users.insert_one({
        "name": "Nomsa Dlamini",
        "phone": "0842002002",
        "pin_hash": hash_pin("2222"),
        "role": "customer",
        "is_active": True,
        "company_id": "",
        "customer_profile": {
            "business_name": "Nomsa's Tuck Shop",
            "contact_person": "Nomsa Dlamini",
            "delivery_address": "45 Booth Rd, Umlazi",
            "province": "KwaZulu-Natal",
            "district": "eThekwini",
            "city": "Umlazi",
        },
        "created_at": datetime.utcnow()
    })
    
    return {
        "message": "Database reset and seeded successfully",
        "companies": [
            {"name": "Mzansi Distribution", "admin_phone": "0767862760", "admin_pin": "1984", "id": comp1_id},
            {"name": "Fresh Foods SA", "admin_phone": "0711002001", "admin_pin": "2222", "id": comp2_id},
            {"name": "Cape Traders", "admin_phone": "0722003001", "admin_pin": "3333", "id": comp3_id},
        ],
        "customers": [
            {"name": "Thabo's Spaza Shop", "phone": "0831001001", "pin": "1111", "location": "Soweto, Gauteng"},
            {"name": "Nomsa's Tuck Shop", "phone": "0842002002", "pin": "2222", "location": "Umlazi, KZN"},
        ],
        "routes": 4,
        "products": len(products1) + len(products2) + len(products3),
        "vehicles": 4
    }


def generate_order_number(company_name: str) -> str:
    """Generate unique order number: COMPCODE-DATE-SEQ"""
    code = ''.join(c for c in company_name.upper() if c.isalpha())[:4]
    if len(code) < 3:
        code = code.ljust(3, 'X')
    date_str = datetime.utcnow().strftime("%Y%m%d")
    import random
    seq = random.randint(1, 999)
    return f"{code}-{date_str}-{seq:03d}"

def get_next_delivery_day(delivery_days: List[str], cut_off_hours: int = 16, cut_off_time_str: str = None) -> Optional[dict]:
    """Calculate the next delivery day and whether ordering is still open.
    
    Supports both:
    - cut_off_hours: hours before delivery day to cut off (legacy)
    - cut_off_time_str: specific time on the day before delivery, e.g. "16:00" (preferred)
    """
    if not delivery_days:
        return None
    
    now = datetime.utcnow()
    today_name = now.strftime("%A")
    
    day_indices = {d: i for i, d in enumerate(DAYS_OF_WEEK)}
    today_idx = day_indices.get(today_name, 0)
    
    def calc_cutoff(delivery_date_obj, offset):
        """Calculate cut-off datetime. If cut_off_time_str given, use day-before at that time."""
        if cut_off_time_str:
            try:
                hour, minute = map(int, cut_off_time_str.split(":"))
                # Cut-off is the day before delivery at the specified time
                cutoff_day = delivery_date_obj - timedelta(days=1)
                return cutoff_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except (ValueError, AttributeError):
                pass
        return delivery_date_obj - timedelta(hours=cut_off_hours)
    
    for offset in range(0, 8):
        check_idx = (today_idx + offset) % 7
        check_day = DAYS_OF_WEEK[check_idx]
        
        if check_day in delivery_days:
            delivery_date = (now + timedelta(days=offset)).replace(hour=8, minute=0, second=0, microsecond=0)
            cut_off_date = calc_cutoff(delivery_date, offset)
            
            if now < cut_off_date:
                seconds_left = (cut_off_date - now).total_seconds()
                hours_left = seconds_left / 3600
                return {
                    "delivery_day": check_day,
                    "delivery_date": delivery_date.strftime("%Y-%m-%d"),
                    "cut_off_time": cut_off_date.isoformat(),
                    "cut_off_display": cut_off_date.strftime("%A %d %b, %H:%M"),
                    "is_open": True,
                    "hours_until_cutoff": max(0, hours_left),
                    "minutes_until_cutoff": max(0, int(seconds_left / 60)),
                    "cutoff_message": f"Orders close {cut_off_date.strftime('%A at %H:%M')} for {check_day} delivery"
                }
    
    # All cut-offs passed, find next week's first delivery
    for offset in range(1, 8):
        check_idx = (today_idx + offset) % 7
        check_day = DAYS_OF_WEEK[check_idx]
        if check_day in delivery_days:
            delivery_date = (now + timedelta(days=offset + 7)).replace(hour=8, minute=0, second=0, microsecond=0)
            cut_off_date = calc_cutoff(delivery_date, offset + 7)
            is_open = now < cut_off_date
            seconds_left = max(0, (cut_off_date - now).total_seconds())
            return {
                "delivery_day": check_day,
                "delivery_date": delivery_date.strftime("%Y-%m-%d"),
                "cut_off_time": cut_off_date.isoformat(),
                "cut_off_display": cut_off_date.strftime("%A %d %b, %H:%M"),
                "is_open": is_open,
                "hours_until_cutoff": max(0, seconds_left / 3600),
                "minutes_until_cutoff": max(0, int(seconds_left / 60)),
                "cutoff_message": f"Orders close {cut_off_date.strftime('%A at %H:%M')} for {check_day} delivery" if is_open else f"Next ordering window opens soon for {check_day} delivery"
            }
    
    return None

# --- Customer Registration ---
@api_router.post("/auth/register-customer")
async def register_customer(data: CustomerRegister):
    """Register a new customer user - marketplace model"""
    existing = await db.users.find_one({"phone": data.phone})
    if existing:
        raise HTTPException(status_code=400, detail="Phone number already registered")
    
    profile = {
        "business_name": data.business_name,
        "contact_person": data.contact_person,
        "delivery_address": data.delivery_address or "",
        "province": data.province or "",
        "district": data.district or "",
        "city": data.city or "",
    }
    
    company_id = ""
    company_name = ""
    if data.company_id:
        try:
            company = await db.companies.find_one({"_id": ObjectId(data.company_id)})
            if company:
                company_id = data.company_id
                company_name = company.get("name", "")
        except Exception:
            pass
    
    if data.route_id:
        try:
            route = await db.routes.find_one({"_id": ObjectId(data.route_id)})
            if route:
                profile["route_id"] = data.route_id
                profile["route_name"] = route.get("name", "")
                if not company_id:
                    company_id = route.get("company_id", "")
        except Exception:
            pass
    
    # Auto-match by location if no route
    if not data.route_id and data.province and data.city:
        matching = await db.routes.find({"province": data.province, "areas_covered": data.city}).to_list(10)
        if matching:
            best = matching[0]
            profile["route_id"] = str(best["_id"])
            profile["route_name"] = best.get("name", "")
            if not company_id:
                company_id = best.get("company_id", "")
    
    user_doc = {
        "name": data.contact_person,
        "phone": data.phone,
        "pin_hash": hash_pin(data.pin),
        "role": "customer",
        "is_active": True,
        "company_id": company_id,
        "customer_profile": profile,
        "created_at": datetime.utcnow()
    }
    result = await db.users.insert_one(user_doc)
    
    if company_id:
        await db.customers.insert_one({
            "name": data.business_name,
            "phone": data.phone,
            "address": data.delivery_address or "",
            "route_id": data.route_id or profile.get("route_id", ""),
            "route_name": profile.get("route_name", ""),
            "is_active": True,
            "balance": 0.0,
            "company_id": company_id,
            "user_id": str(result.inserted_id),
            "created_at": datetime.utcnow()
        })
    
    return {
        "message": "Customer registered successfully",
        "user_id": str(result.inserted_id),
        "company_name": company_name or "Browse all suppliers",
        "route_name": profile.get("route_name", "Not assigned"),
    }

# --- Public: List Companies for customer registration ---
@api_router.get("/companies/list")
async def list_companies():
    """Public endpoint - list all companies for customer registration"""
    companies = await db.companies.find({}).to_list(100)
    return [{"id": str(c["_id"]), "name": c["name"], "phone": c.get("phone", "")} for c in companies]

# --- Public: List routes for a company ---
@api_router.get("/companies/{company_id}/routes")
async def list_company_routes(company_id: str):
    """Public endpoint - list routes for a company with delivery schedules"""
    routes = await db.routes.find({"company_id": company_id}).to_list(50)
    result = []
    for r in routes:
        schedule = r.get("delivery_schedule", {})
        result.append({
            "id": str(r["_id"]),
            "name": r.get("name", ""),
            "description": r.get("description", ""),
            "delivery_days": schedule.get("delivery_days", []),
            "cut_off_time": schedule.get("cut_off_time", "16:00"),
        })
    return result

# --- Public: List products for a company ---
@api_router.get("/companies/{company_id}/products")
async def list_company_products(company_id: str):
    """Public endpoint - list products for a company (for customer browsing)"""
    products = await db.products.find({"company_id": company_id}).to_list(200)
    return [str_id(p) for p in products]

# --- Route Delivery Schedule ---
@api_router.put("/routes/{route_id}/schedule")
async def update_route_schedule(route_id: str, schedule: DeliverySchedule, current_user: dict = Depends(get_current_user)):
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        oid = ObjectId(route_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid route ID")
    
    cf = get_company_filter(current_user)
    route = await db.routes.find_one({"_id": oid, **cf})
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    
    await db.routes.update_one(
        {"_id": oid},
        {"$set": {"delivery_schedule": schedule.dict()}}
    )
    # Return the updated schedule with delivery info
    delivery_info = get_next_delivery_day(
        schedule.delivery_days,
        schedule.cut_off_hours_before or 16,
        schedule.cut_off_time or "16:00"
    )
    return {
        "message": "Delivery schedule updated",
        "route_id": route_id,
        "route_name": route.get("name", ""),
        "schedule": schedule.dict(),
        "next_delivery": delivery_info
    }

@api_router.get("/routes/{route_id}/schedule")
async def get_route_schedule(route_id: str, current_user: dict = Depends(get_current_user)):
    try:
        oid = ObjectId(route_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid route ID")
    
    route = await db.routes.find_one({"_id": oid})
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    
    schedule = route.get("delivery_schedule", {"delivery_days": [], "cut_off_hours_before": 16, "cut_off_time": "16:00"})
    delivery_info = get_next_delivery_day(
        schedule.get("delivery_days", []),
        schedule.get("cut_off_hours_before", 16),
        schedule.get("cut_off_time", "16:00")
    )
    
    return {
        "route_id": route_id,
        "route_name": route.get("name", ""),
        "schedule": schedule,
        "next_delivery": delivery_info
    }

# --- Customer: Get available companies (MARKETPLACE) ---
@api_router.get("/customer/available-companies")
async def get_available_companies(current_user: dict = Depends(get_current_user)):
    """Customer sees all companies that deliver to their area"""
    if not is_customer(current_user):
        raise HTTPException(status_code=403, detail="Customer access only")
    
    profile = current_user.get("customer_profile", {})
    customer_province = profile.get("province", "")
    customer_district = profile.get("district", "")
    customer_city = profile.get("city", "")
    
    # Find routes that cover the customer's area
    query = {}
    if customer_city:
        query["areas_covered"] = customer_city
    if customer_province:
        query["province"] = customer_province
    
    matching_routes = await db.routes.find(query).to_list(100)
    
    # If no exact match, try broader match by province only
    if not matching_routes and customer_province:
        matching_routes = await db.routes.find({"province": customer_province}).to_list(100)
    
    # If still nothing, return all companies
    if not matching_routes:
        all_companies = await db.companies.find({}).to_list(100)
        result = []
        for c in all_companies:
            result.append({
                "id": str(c["_id"]),
                "name": c.get("name", ""),
                "phone": c.get("phone", ""),
                "address": c.get("address", ""),
                "province": c.get("province", ""),
                "routes": [],
                "product_count": await db.products.count_documents({"company_id": str(c["_id"])}),
            })
        return result
    
    # Collect unique company IDs from matching routes
    company_ids = set()
    route_by_company = {}
    for r in matching_routes:
        cid = r.get("company_id", "")
        if cid:
            company_ids.add(cid)
            if cid not in route_by_company:
                route_by_company[cid] = []
            schedule = r.get("delivery_schedule", {})
            route_by_company[cid].append({
                "id": str(r["_id"]),
                "name": r.get("name", ""),
                "areas": r.get("areas_covered", []),
                "delivery_days": schedule.get("delivery_days", []),
                "cut_off_time": schedule.get("cut_off_time", "16:00"),
            })
    
    result = []
    for cid in company_ids:
        try:
            company = await db.companies.find_one({"_id": ObjectId(cid)})
        except Exception:
            continue
        if not company:
            continue
        product_count = await db.products.count_documents({"company_id": cid})
        result.append({
            "id": str(company["_id"]),
            "name": company.get("name", ""),
            "phone": company.get("phone", ""),
            "address": company.get("address", ""),
            "province": company.get("province", ""),
            "routes": route_by_company.get(cid, []),
            "product_count": product_count,
        })
    
    return result

# --- Customer: Get products for a specific company (MARKETPLACE) ---
@api_router.get("/customer/company/{company_id}/products")
async def get_customer_company_products(company_id: str, current_user: dict = Depends(get_current_user)):
    """Customer browses products from a specific company"""
    if not is_customer(current_user):
        raise HTTPException(status_code=403, detail="Customer access only")
    
    try:
        company = await db.companies.find_one({"_id": ObjectId(company_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid company ID")
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    products = await db.products.find({"company_id": company_id}).to_list(200)
    
    # Also get delivery info for routes covering customer area
    profile = current_user.get("customer_profile", {})
    customer_city = profile.get("city", "")
    route_query = {"company_id": company_id}
    if customer_city:
        route_query["areas_covered"] = customer_city
    matching_route = await db.routes.find_one(route_query)
    
    delivery_info = None
    if matching_route:
        schedule = matching_route.get("delivery_schedule") or {}
        delivery_info = get_next_delivery_day(
            schedule.get("delivery_days", []),
            schedule.get("cut_off_hours_before", 16),
            schedule.get("cut_off_time", "16:00")
        )
    
    return {
        "company": {
            "id": str(company["_id"]),
            "name": company.get("name", ""),
            "phone": company.get("phone", ""),
        },
        "products": [str_id(p) for p in products],
        "route": {
            "id": str(matching_route["_id"]),
            "name": matching_route.get("name", ""),
            "delivery_days": matching_route.get("delivery_schedule", {}).get("delivery_days", []),
            "cut_off_time": matching_route.get("delivery_schedule", {}).get("cut_off_time", "16:00"),
        } if matching_route else None,
        "next_delivery": delivery_info,
    }

# --- Customer: Get products from their assigned distributor (legacy support) ---
@api_router.get("/customer/products")
async def get_customer_products(current_user: dict = Depends(get_current_user)):
    """Customer sees products from their assigned distributor"""
    if not is_customer(current_user):
        raise HTTPException(status_code=403, detail="Customer access only")
    
    company_id = current_user.get("company_id")
    if not company_id:
        return []
    
    products = await db.products.find({"company_id": company_id}).to_list(200)
    return [str_id(p) for p in products]

# --- Customer: Get delivery info ---
@api_router.get("/customer/delivery-info")
async def get_customer_delivery_info(current_user: dict = Depends(get_current_user)):
    """Get the customer's delivery info and profile"""
    if not is_customer(current_user):
        raise HTTPException(status_code=403, detail="Customer access only")
    
    profile = current_user.get("customer_profile", {})
    
    return {
        "company_name": "",
        "route_name": "",
        "schedule": {},
        "next_delivery": None,
        "profile": profile,
    }

# --- Place Order ---
@api_router.post("/orders")
async def create_order(order: OrderCreate, current_user: dict = Depends(get_current_user)):
    """Customer places an order - marketplace model supports ordering from any company"""
    if not is_customer(current_user):
        raise HTTPException(status_code=403, detail="Customer access only")
    
    # Get customer profile
    profile = current_user.get("customer_profile", {})
    customer_city = profile.get("city", "")
    
    # Verify company
    company = await db.companies.find_one({"_id": ObjectId(order.company_id)})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    # Find matching route for this company and customer's area
    route_query = {"company_id": order.company_id}
    if customer_city:
        route_query["areas_covered"] = customer_city
    matching_route = await db.routes.find_one(route_query)
    
    # Fallback: any route for this company
    if not matching_route:
        matching_route = await db.routes.find_one({"company_id": order.company_id})
    
    route_id = str(matching_route["_id"]) if matching_route else ""
    route_name = matching_route.get("name", "") if matching_route else ""
    
    # Check delivery schedule and cut-off
    delivery_info = None
    if matching_route:
        schedule = matching_route.get("delivery_schedule") or {}
        delivery_days = schedule.get("delivery_days", [])
        cut_off_hours = schedule.get("cut_off_hours_before", 16)
        cut_off_time_str = schedule.get("cut_off_time", "16:00")
        delivery_info = get_next_delivery_day(delivery_days, cut_off_hours, cut_off_time_str)
        
        if delivery_info and not delivery_info.get("is_open", True):
            return {
                "error": True, 
                "message": f"Orders for the next delivery are closed. {delivery_info.get('cutoff_message', '')}",
                "next_delivery": delivery_info
            }
    
    # Check for duplicate orders (same customer, same company, same day)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    existing_order = await db.orders.find_one({
        "customer_id": current_user["id"],
        "company_id": order.company_id,
        "created_at": {"$gte": today_start},
        "status": {"$in": ["pending", "confirmed", "adjusted", "packed", "out_for_delivery"]}
    })
    if existing_order:
        raise HTTPException(status_code=400, detail="You already have an active order for today. Please wait or cancel the existing order.")
    
    # Generate order number
    order_number = generate_order_number(company.get("name", "ORD"))
    while await db.orders.find_one({"order_number": order_number}):
        order_number = generate_order_number(company.get("name", "ORD"))
    
    total_amount = sum(item.quantity * item.unit_price for item in order.items)
    
    order_doc = {
        "order_number": order_number,
        "company_id": order.company_id,
        "company_name": company.get("name", ""),
        "customer_id": current_user["id"],
        "customer_name": profile.get("business_name", current_user.get("name", "")),
        "customer_phone": current_user.get("phone", ""),
        "route_id": route_id,
        "route_name": route_name,
        "items": [item.model_dump() for item in order.items],
        "original_items": [item.model_dump() for item in order.items],
        "total_amount": total_amount,
        "status": "pending",
        "delivery_day": delivery_info.get("delivery_day", "") if delivery_info else "",
        "delivery_date": delivery_info.get("delivery_date", "") if delivery_info else "",
        "notes": order.notes,
        "adjustments": [],
        "status_history": [{"status": "pending", "timestamp": datetime.utcnow().isoformat(), "by": current_user["id"]}],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    
    result = await db.orders.insert_one(order_doc)
    order_doc["_id"] = result.inserted_id
    
    return str_id(order_doc)

# --- Get Orders ---
@api_router.get("/orders")
async def get_orders(
    status: Optional[str] = None,
    route_id: Optional[str] = None,
    date_str: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get orders - filtered by role. Drivers cannot view orders."""
    query = {}
    
    if is_customer(current_user):
        query["customer_id"] = current_user["id"]
    elif current_user.get("role") == "driver":
        # Drivers should NOT see orders - admin dispatches orders to drivers
        raise HTTPException(status_code=403, detail="Drivers cannot view orders. Orders are managed by admin.")
    else:
        # Admin/Manager see their company's orders
        cf = get_company_filter(current_user)
        query.update(cf)
    
    if status:
        query["status"] = status
    if route_id:
        query["route_id"] = route_id
    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            query["created_at"] = {
                "$gte": date_obj,
                "$lt": date_obj + timedelta(days=1)
            }
        except ValueError:
            pass
    
    orders = await db.orders.find(query).sort("created_at", -1).to_list(500)
    return [str_id(o) for o in orders]

# --- Get Single Order ---
@api_router.get("/orders/{order_id}")
async def get_order(order_id: str, current_user: dict = Depends(get_current_user)):
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Security: customers can only see their own orders
    if is_customer(current_user) and order.get("customer_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return str_id(order)

class OrderStatusUpdate(BaseModel):
    status: str

# --- Update Order Status ---
@api_router.put("/orders/{order_id}/status")
async def update_order_status(order_id: str, body: OrderStatusUpdate, current_user: dict = Depends(get_current_user)):
    status = body.status
    if is_customer(current_user):
        # Customers can only cancel pending orders
        if status != "cancelled":
            raise HTTPException(status_code=403, detail="Customers can only cancel orders")
    
    if status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {ORDER_STATUSES}")
    
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if is_customer(current_user) and order.get("customer_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if is_customer(current_user) and order.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Can only cancel pending orders")
    
    await db.orders.update_one(
        {"_id": ObjectId(order_id)},
        {
            "$set": {"status": status, "updated_at": datetime.utcnow()},
            "$push": {"status_history": {"status": status, "timestamp": datetime.utcnow().isoformat(), "by": current_user["id"]}}
        }
    )
    
    # Return the full updated order
    updated_order = await db.orders.find_one({"_id": ObjectId(order_id)})
    return str_id(updated_order)

# --- Adjust Order (Distributor) ---
@api_router.put("/orders/{order_id}/adjust")
async def adjust_order(order_id: str, adjustment: OrderAdjust, current_user: dict = Depends(get_current_user)):
    if is_customer(current_user):
        raise HTTPException(status_code=403, detail="Only distributors can adjust orders")
    
    order = await db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if order.get("status") in ["delivered", "cancelled"]:
        raise HTTPException(status_code=400, detail="Cannot adjust delivered or cancelled orders")
    
    # Update items
    new_items = []
    for adj_item in adjustment.items:
        new_items.append({
            "product_id": adj_item.product_id,
            "product_name": adj_item.product_name,
            "quantity": adj_item.adjusted_quantity,
            "unit_price": adj_item.unit_price,
        })
    
    new_total = sum(i["quantity"] * i["unit_price"] for i in new_items)
    
    await db.orders.update_one(
        {"_id": ObjectId(order_id)},
        {
            "$set": {
                "items": new_items,
                "total_amount": new_total,
                "status": "adjusted",
                "updated_at": datetime.utcnow(),
            },
            "$push": {
                "adjustments": {
                    "adjusted_by": current_user["id"],
                    "adjusted_by_name": current_user.get("name", ""),
                    "reason": adjustment.adjustment_reason,
                    "items": [i.model_dump() for i in adjustment.items],
                    "timestamp": datetime.utcnow().isoformat()
                },
                "status_history": {"status": "adjusted", "timestamp": datetime.utcnow().isoformat(), "by": current_user["id"]}
            }
        }
    )
    
    return {"message": "Order adjusted successfully", "new_total": new_total, "order": str_id(await db.orders.find_one({"_id": ObjectId(order_id)}))}

# --- Order Dashboard for Distributor ---
@api_router.get("/orders/dashboard/summary")
async def get_order_dashboard(current_user: dict = Depends(get_current_user)):
    if is_customer(current_user):
        raise HTTPException(status_code=403, detail="Distributor access only")
    
    query = get_company_filter(current_user)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    query["created_at"] = {"$gte": today}
    
    orders = await db.orders.find(query).to_list(1000)
    
    summary = {
        "total_orders": len(orders),
        "pending": len([o for o in orders if o.get("status") == "pending"]),
        "confirmed": len([o for o in orders if o.get("status") == "confirmed"]),
        "adjusted": len([o for o in orders if o.get("status") == "adjusted"]),
        "packed": len([o for o in orders if o.get("status") == "packed"]),
        "out_for_delivery": len([o for o in orders if o.get("status") == "out_for_delivery"]),
        "delivered": len([o for o in orders if o.get("status") == "delivered"]),
        "cancelled": len([o for o in orders if o.get("status") == "cancelled"]),
        "total_value": sum(o.get("total_amount", 0) for o in orders if o.get("status") not in ["cancelled"]),
    }
    
    # Route breakdown
    route_orders = {}
    for o in orders:
        rn = o.get("route_name", "Unassigned")
        if rn not in route_orders:
            route_orders[rn] = {"count": 0, "value": 0}
        route_orders[rn]["count"] += 1
        route_orders[rn]["value"] += o.get("total_amount", 0)
    
    summary["by_route"] = route_orders
    return summary

# --- Route Packing Summary ---
@api_router.get("/orders/packing/{route_id}")
async def get_route_packing_summary(route_id: str, current_user: dict = Depends(get_current_user)):
    if is_customer(current_user):
        raise HTTPException(status_code=403, detail="Distributor access only")
    
    query = get_company_filter(current_user)
    query["route_id"] = route_id
    query["status"] = {"$in": ["pending", "confirmed", "adjusted"]}
    
    orders = await db.orders.find(query).to_list(500)
    
    product_totals = {}
    for order in orders:
        for item in order.get("items", []):
            pid = item.get("product_id", "")
            if pid not in product_totals:
                product_totals[pid] = {"product_name": item.get("product_name", ""), "total_quantity": 0, "orders_count": 0}
            product_totals[pid]["total_quantity"] += item.get("quantity", 0)
            product_totals[pid]["orders_count"] += 1
    
    route = await db.routes.find_one({"_id": ObjectId(route_id)})
    
    return {
        "route_id": route_id,
        "route_name": route.get("name", "") if route else "",
        "total_orders": len(orders),
        "products": list(product_totals.values()),
    }


# ==================== VEHICLE STOCK DISPATCH & RETURN ====================

class VehicleStockItem(BaseModel):
    product_id: str
    product_name: str
    quantity: int

class VehicleStockDispatch(BaseModel):
    daily_route_id: str
    items: List[VehicleStockItem]
    notes: Optional[str] = None

class VehicleStockReturn(BaseModel):
    daily_route_id: str
    items: List[VehicleStockItem]
    notes: Optional[str] = None

@api_router.post("/vehicle-stock/dispatch")
async def dispatch_vehicle_stock(data: VehicleStockDispatch, current_user: dict = Depends(get_current_user)):
    """Admin dispatches/loads stock onto a vehicle for a daily route"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    company_id = current_user.get("company_id", "")
    
    # Get daily route info
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(data.daily_route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    today = datetime.utcnow().strftime("%Y-%m-%d")
    dispatched_items = []
    
    for item in data.items:
        if item.quantity <= 0:
            continue
        
        # Check warehouse stock availability (company-scoped)
        stock = await db.stock.find_one({"product_id": item.product_id, "company_id": company_id})
        available = stock.get("quantity", 0) if stock else 0
        
        if available < item.quantity:
            raise HTTPException(
                status_code=400, 
                detail=f"Insufficient stock for {item.product_name}. Available: {available}, Requested: {item.quantity}"
            )
        
        # Deduct from warehouse stock
        await db.stock.update_one(
            {"product_id": item.product_id, "company_id": company_id},
            {"$inc": {"quantity": -item.quantity}, "$set": {"updated_at": datetime.utcnow()}}
        )
        
        # Add/update vehicle stock record
        existing = await db.vehicle_stock.find_one({
            "daily_route_id": data.daily_route_id,
            "product_id": item.product_id,
            "status": "active"
        })
        
        if existing:
            await db.vehicle_stock.update_one(
                {"_id": existing["_id"]},
                {"$inc": {"quantity_loaded": item.quantity, "quantity_remaining": item.quantity},
                 "$set": {"updated_at": datetime.utcnow()}}
            )
        else:
            await db.vehicle_stock.insert_one({
                "daily_route_id": data.daily_route_id,
                "vehicle_id": daily_route.get("vehicle_id", ""),
                "vehicle_name": daily_route.get("vehicle_name", ""),
                "driver_id": daily_route.get("driver_id", ""),
                "driver_name": daily_route.get("driver_name", ""),
                "route_id": daily_route.get("route_id", ""),
                "route_name": daily_route.get("route_name", ""),
                "product_id": item.product_id,
                "product_name": item.product_name,
                "quantity_loaded": item.quantity,
                "quantity_sold": 0,
                "quantity_returned": 0,
                "quantity_remaining": item.quantity,
                "date": today,
                "status": "active",
                "company_id": company_id,
                "dispatched_by": current_user["id"],
                "dispatched_by_name": current_user["name"],
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            })
        
        # Log the dispatch movement
        await db.stock_movements.insert_one({
            "movement_type": "dispatch_to_vehicle",
            "product_id": item.product_id,
            "product_name": item.product_name,
            "quantity": -item.quantity,
            "daily_route_id": data.daily_route_id,
            "vehicle_name": daily_route.get("vehicle_name", ""),
            "driver_name": daily_route.get("driver_name", ""),
            "company_id": company_id,
            "personnel_id": current_user["id"],
            "personnel_name": current_user["name"],
            "created_at": datetime.utcnow()
        })
        
        dispatched_items.append({
            "product_id": item.product_id,
            "product_name": item.product_name,
            "quantity_loaded": item.quantity,
        })
    
    return {
        "message": f"Stock dispatched to {daily_route.get('vehicle_name', 'vehicle')} ({daily_route.get('driver_name', 'driver')})",
        "daily_route_id": data.daily_route_id,
        "items": dispatched_items,
    }

@api_router.post("/vehicle-stock/return")
async def return_vehicle_stock(data: VehicleStockReturn, current_user: dict = Depends(get_current_user)):
    """Admin receives unsold stock returning from a vehicle/route"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    company_id = current_user.get("company_id", "")
    
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(data.daily_route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    returned_items = []
    
    for item in data.items:
        if item.quantity <= 0:
            continue
        
        # Update vehicle stock record
        vs = await db.vehicle_stock.find_one({
            "daily_route_id": data.daily_route_id,
            "product_id": item.product_id,
            "status": "active"
        })
        
        if vs:
            await db.vehicle_stock.update_one(
                {"_id": vs["_id"]},
                {
                    "$inc": {"quantity_returned": item.quantity, "quantity_remaining": -item.quantity},
                    "$set": {"updated_at": datetime.utcnow()}
                }
            )
        
        # Add back to warehouse stock (company-scoped)
        await db.stock.update_one(
            {"product_id": item.product_id, "company_id": company_id},
            {"$inc": {"quantity": item.quantity}, "$set": {"updated_at": datetime.utcnow()}}
        )
        
        # Log the return movement
        await db.stock_movements.insert_one({
            "movement_type": "return_from_vehicle",
            "product_id": item.product_id,
            "product_name": item.product_name,
            "quantity": item.quantity,
            "daily_route_id": data.daily_route_id,
            "vehicle_name": daily_route.get("vehicle_name", ""),
            "driver_name": daily_route.get("driver_name", ""),
            "company_id": company_id,
            "personnel_id": current_user["id"],
            "personnel_name": current_user["name"],
            "notes": data.notes,
            "created_at": datetime.utcnow()
        })
        
        returned_items.append({
            "product_id": item.product_id,
            "product_name": item.product_name,
            "quantity_returned": item.quantity,
        })
    
    # Close out vehicle stock records if route is completed
    if daily_route.get("status") == "completed":
        await db.vehicle_stock.update_many(
            {"daily_route_id": data.daily_route_id, "status": "active"},
            {"$set": {"status": "closed", "closed_at": datetime.utcnow()}}
        )
    
    return {
        "message": f"Stock returned from {daily_route.get('vehicle_name', 'vehicle')}",
        "daily_route_id": data.daily_route_id,
        "items": returned_items,
    }

@api_router.get("/vehicle-stock/{daily_route_id}")
async def get_vehicle_stock(daily_route_id: str, current_user: dict = Depends(get_current_user)):
    """Get stock loaded on a vehicle for a specific daily route"""
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(daily_route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    items = await db.vehicle_stock.find({
        "daily_route_id": daily_route_id,
        "status": "active"
    }).to_list(500)
    
    return {
        "daily_route_id": daily_route_id,
        "route_name": daily_route.get("route_name", ""),
        "vehicle_name": daily_route.get("vehicle_name", ""),
        "driver_name": daily_route.get("driver_name", ""),
        "date": daily_route.get("date", ""),
        "items": [str_id(i) for i in items],
        "total_loaded": sum(i.get("quantity_loaded", 0) for i in items),
        "total_sold": sum(i.get("quantity_sold", 0) for i in items),
        "total_remaining": sum(i.get("quantity_remaining", 0) for i in items),
        "total_returned": sum(i.get("quantity_returned", 0) for i in items),
    }

@api_router.get("/vehicle-stock/driver/my-stock")
async def get_driver_vehicle_stock(current_user: dict = Depends(get_current_user)):
    """Driver/Admin views stock loaded onto their vehicle(s) for today, grouped by route/vehicle"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    query = {"date": today, "status": "active"}
    # Drivers see only their own, admins see all for their company
    if current_user.get("role") == "driver":
        query["driver_id"] = current_user["id"]
    else:
        company_id = current_user.get("company_id", "")
        if company_id:
            query["company_id"] = company_id
    
    items = await db.vehicle_stock.find(query).to_list(500)
    
    if not items:
        return {"vehicles": [], "message": "No stock has been loaded onto any vehicle yet."}
    
    # Group by daily_route_id (each represents a unique vehicle/route combo)
    grouped = {}
    for item in items:
        key = item.get("daily_route_id", "unknown")
        if key not in grouped:
            grouped[key] = {
                "daily_route_id": key,
                "vehicle_name": item.get("vehicle_name", ""),
                "route_name": item.get("route_name", ""),
                "driver_name": item.get("driver_name", ""),
                "items": [],
                "total_loaded": 0,
                "total_sold": 0,
                "total_remaining": 0,
            }
        grouped[key]["items"].append(str_id(item))
        grouped[key]["total_loaded"] += item.get("quantity_loaded", 0)
        grouped[key]["total_sold"] += item.get("quantity_sold", 0)
        grouped[key]["total_remaining"] += item.get("quantity_remaining", 0)
    
    vehicles = list(grouped.values())
    
    return {
        "vehicles": vehicles,
        "total_vehicles": len(vehicles),
        "grand_total_loaded": sum(v["total_loaded"] for v in vehicles),
        "grand_total_remaining": sum(v["total_remaining"] for v in vehicles),
    }


# ==================== DELIVERY TRACKING (P2) ====================

class LocationUpdate(BaseModel):
    latitude: float
    longitude: float
    accuracy: Optional[float] = None
    speed: Optional[float] = None
    heading: Optional[float] = None

@api_router.post("/daily-routes/{route_id}/location")
async def update_driver_location(route_id: str, location: LocationUpdate, current_user: dict = Depends(get_current_user)):
    """Driver updates GPS location during active route"""
    try:
        daily_route = await db.daily_routes.find_one({"_id": ObjectId(route_id), "status": "active"})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid route ID")
    
    if not daily_route:
        raise HTTPException(status_code=404, detail="Active route not found")
    
    if daily_route.get("driver_id") != current_user["id"] and not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Not your route")
    
    loc_entry = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "accuracy": location.accuracy,
        "speed": location.speed,
        "heading": location.heading,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    await db.daily_routes.update_one(
        {"_id": ObjectId(route_id)},
        {
            "$set": {"current_location": loc_entry, "location_updated_at": datetime.utcnow()},
            "$push": {"location_history": {"$each": [loc_entry], "$slice": -100}}  # Keep last 100 points
        }
    )
    
    return {"message": "Location updated", "location": loc_entry}

@api_router.get("/daily-routes/{route_id}/location")
async def get_driver_location(route_id: str, current_user: dict = Depends(get_current_user)):
    """Get driver's current location for an active route"""
    try:
        daily_route = await db.daily_routes.find_one({"_id": ObjectId(route_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid route ID")
    
    if not daily_route:
        raise HTTPException(status_code=404, detail="Route not found")
    
    return {
        "route_id": route_id,
        "driver_name": daily_route.get("driver_name", ""),
        "vehicle_name": daily_route.get("vehicle_name", ""),
        "vehicle_registration": daily_route.get("vehicle_registration", ""),
        "status": daily_route.get("status", ""),
        "current_location": daily_route.get("current_location"),
        "location_updated_at": daily_route.get("location_updated_at", "").isoformat() if isinstance(daily_route.get("location_updated_at"), datetime) else daily_route.get("location_updated_at", ""),
        "sales_count": daily_route.get("sales_count", 0),
    }

@api_router.get("/orders/{order_id}/tracking")
async def get_order_tracking(order_id: str, current_user: dict = Depends(get_current_user)):
    """Customer gets full tracking info for their order"""
    try:
        order = await db.orders.find_one({"_id": ObjectId(order_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Customer can only see their own orders
    if is_customer(current_user) and order.get("customer_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    tracking = {
        "order_id": str(order["_id"]),
        "order_number": order.get("order_number", ""),
        "status": order.get("status", ""),
        "status_history": order.get("status_history", []),
        "delivery_day": order.get("delivery_day", ""),
        "delivery_date": order.get("delivery_date", ""),
        "items": order.get("items", []),
        "total_amount": order.get("total_amount", 0),
        "company_name": order.get("company_name", ""),
        "route_name": order.get("route_name", ""),
        "driver_info": None,
        "current_location": None,
        "estimated_delivery": None,
    }
    
    # If out for delivery, find the active daily route and get driver/location info
    if order.get("status") in ["out_for_delivery", "packed"]:
        route_id = order.get("route_id", "")
        if route_id:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            active_route = await db.daily_routes.find_one({
                "route_id": route_id,
                "date": today,
                "status": "active"
            })
            if active_route:
                tracking["driver_info"] = {
                    "name": active_route.get("driver_name", ""),
                    "vehicle": active_route.get("vehicle_name", ""),
                    "registration": active_route.get("vehicle_registration", ""),
                    "started_at": active_route.get("started_at", "").isoformat() if isinstance(active_route.get("started_at"), datetime) else str(active_route.get("started_at", "")),
                }
                tracking["current_location"] = active_route.get("current_location")
                loc_time = active_route.get("location_updated_at")
                tracking["location_updated_at"] = loc_time.isoformat() if isinstance(loc_time, datetime) else str(loc_time or "")
                
                # Calculate stops info
                total_orders_on_route = await db.orders.count_documents({
                    "route_id": route_id,
                    "delivery_date": order.get("delivery_date", ""),
                    "status": {"$in": ["confirmed", "adjusted", "packed", "out_for_delivery"]}
                })
                delivered_on_route = await db.orders.count_documents({
                    "route_id": route_id,
                    "delivery_date": order.get("delivery_date", ""),
                    "status": "delivered"
                })
                tracking["delivery_progress"] = {
                    "total_stops": total_orders_on_route + delivered_on_route,
                    "completed_stops": delivered_on_route,
                    "remaining_stops": total_orders_on_route,
                }
    
    return tracking

@api_router.put("/orders/batch-status")
async def batch_update_order_status(
    order_ids: List[str] = Body(..., embed=True),
    status: str = Body(..., embed=True),
    current_user: dict = Depends(get_current_user)
):
    """Driver/Admin batch-updates order statuses (e.g., mark all route orders as out_for_delivery)"""
    if is_customer(current_user):
        raise HTTPException(status_code=403, detail="Distributor/Driver access only")
    
    if status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {ORDER_STATUSES}")
    
    updated = 0
    for oid in order_ids:
        try:
            result = await db.orders.update_one(
                {"_id": ObjectId(oid)},
                {
                    "$set": {"status": status, "updated_at": datetime.utcnow()},
                    "$push": {"status_history": {"status": status, "timestamp": datetime.utcnow().isoformat(), "by": current_user["id"]}}
                }
            )
            if result.modified_count > 0:
                updated += 1
        except Exception:
            continue
    
    return {"message": f"{updated} orders updated to {status}", "updated_count": updated}

@api_router.get("/daily-routes/{route_id}/deliveries")
async def get_route_deliveries(route_id: str, current_user: dict = Depends(get_current_user)):
    """Get all orders for a daily route for delivery management by the driver"""
    try:
        daily_route = await db.daily_routes.find_one({"_id": ObjectId(route_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid route ID")
    
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    route_id_ref = daily_route.get("route_id", "")
    delivery_date = daily_route.get("date", "")
    
    # Get orders for this route and delivery date
    orders = await db.orders.find({
        "route_id": route_id_ref,
        "delivery_date": delivery_date,
        "status": {"$nin": ["cancelled"]}
    }).sort("customer_name", 1).to_list(500)
    
    # Group by status
    summary = {
        "total": len(orders),
        "pending": len([o for o in orders if o.get("status") == "pending"]),
        "confirmed": len([o for o in orders if o.get("status") == "confirmed"]),
        "packed": len([o for o in orders if o.get("status") == "packed"]),
        "out_for_delivery": len([o for o in orders if o.get("status") == "out_for_delivery"]),
        "delivered": len([o for o in orders if o.get("status") == "delivered"]),
    }
    
    return {
        "daily_route_id": str(daily_route["_id"]),
        "route_name": daily_route.get("route_name", ""),
        "date": delivery_date,
        "driver_name": daily_route.get("driver_name", ""),
        "vehicle_name": daily_route.get("vehicle_name", ""),
        "summary": summary,
        "orders": [str_id(o) for o in orders],
    }

@api_router.get("/support-info")
async def get_support_info():
    """Get app support and contact information"""
    return {
        "company": "Mzansi FMCG Tracker",
        "website": "www.mzafri.co.za",
        "support_email": "supportapp@mzafri.co.za",
        "contact_number": "+27628138949",
        "app_name": "Mzansi FMCG Tracker",
        "version": "1.0.0"
    }

# ==================== HEALTH CHECK ====================

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# ==================== DATABASE ADMIN PANEL ====================

def serialize_doc(doc: dict) -> dict:
    """Deep serialize a MongoDB document for JSON"""
    result = {}
    for key, value in doc.items():
        if key == "_id":
            result["_id"] = str(value)
            result["id"] = str(value)
        elif isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        elif isinstance(value, list):
            result[key] = [serialize_doc(v) if isinstance(v, dict) else str(v) if isinstance(v, (ObjectId, datetime)) else v for v in value]
        else:
            result[key] = value
    return result

@api_router.get("/admin/db/collections")
async def list_collections(current_user: dict = Depends(get_current_user)):
    """List all database collections with document counts"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    collections = await db.list_collection_names()
    result = []
    for coll_name in sorted(collections):
        count = await db[coll_name].count_documents({})
        result.append({"name": coll_name, "count": count})
    return result

@api_router.get("/admin/db/collections/{collection_name}")
async def browse_collection(
    collection_name: str,
    skip: int = 0,
    limit: int = 50,
    company_filter: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """Browse documents in a collection with pagination"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    collections = await db.list_collection_names()
    if collection_name not in collections:
        raise HTTPException(status_code=404, detail="Collection not found")
    
    query = {}
    # Always enforce company isolation (except for 'companies' collection which only shows names)
    if collection_name not in ["companies"]:
        cf = get_company_filter(current_user)
        query.update(cf)
    
    total = await db[collection_name].count_documents(query)
    docs = await db[collection_name].find(query).sort("_id", -1).skip(skip).limit(limit).to_list(limit)
    
    serialized = []
    for doc in docs:
        serialized.append(serialize_doc(doc))
    
    return {"collection": collection_name, "total": total, "skip": skip, "limit": limit, "documents": serialized}

@api_router.get("/admin/db/collections/{collection_name}/{doc_id}")
async def get_document(collection_name: str, doc_id: str, current_user: dict = Depends(get_current_user)):
    """Get a single document by ID"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        doc = await db[collection_name].find_one({"_id": ObjectId(doc_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Verify document belongs to user's company
    if collection_name not in ["companies"]:
        verify_company_ownership(doc, current_user)
    
    return serialize_doc(doc)

@api_router.put("/admin/db/collections/{collection_name}/{doc_id}")
async def update_document(collection_name: str, doc_id: str, body: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """Update a document's fields"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    update_data = {k: v for k, v in body.items() if k not in ["_id", "id"]}
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    try:
        result = await db[collection_name].update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": update_data}
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Update failed: {str(e)}")
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    
    doc = await db[collection_name].find_one({"_id": ObjectId(doc_id)})
    return serialize_doc(doc)

@api_router.delete("/admin/db/collections/{collection_name}/{doc_id}")
async def delete_document(collection_name: str, doc_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a document by ID"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        result = await db[collection_name].delete_one({"_id": ObjectId(doc_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID")
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    
    return {"message": "Document deleted", "collection": collection_name, "id": doc_id}

# Include the router in the main app
app.include_router(api_router)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
