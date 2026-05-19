# HIGH PRIORITY MISSING ENDPOINTS
# Add these to server.py after the existing vehicle-stock endpoints

# ==================== VEHICLE STOCK GET ENDPOINTS ====================

@api_router.get("/vehicle-stock")
async def get_all_vehicle_stock(current_user: dict = Depends(get_current_user)):
    """Get all vehicle stock records (company-scoped)"""
    cf = get_company_filter(current_user)
    vehicle_stock = await db.vehicle_stock.find(cf).sort("date", -1).to_list(500)
    return [str_id(vs) for vs in vehicle_stock]

@api_router.get("/vehicle-stock/active")
async def get_active_vehicle_stock(current_user: dict = Depends(get_current_user)):
    """Get currently active vehicle stock (loaded on vehicles)"""
    cf = get_company_filter(current_user)
    cf["status"] = "active"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    cf["date"] = today
    
    vehicle_stock = await db.vehicle_stock.find(cf).to_list(500)
    return [str_id(vs) for vs in vehicle_stock]

@api_router.get("/vehicle-stock/driver/{driver_id}")
async def get_driver_vehicle_stock(driver_id: str, current_user: dict = Depends(get_current_user)):
    """Get vehicle stock for a specific driver"""
    cf = get_company_filter(current_user)
    cf["driver_id"] = driver_id
    
    vehicle_stock = await db.vehicle_stock.find(cf).sort("date", -1).to_list(500)
    return [str_id(vs) for vs in vehicle_stock]

@api_router.get("/vehicle-stock/daily-route/{daily_route_id}")
async def get_daily_route_vehicle_stock(daily_route_id: str, current_user: dict = Depends(get_current_user)):
    """Get vehicle stock for a specific daily route"""
    vehicle_stock = await db.vehicle_stock.find({"daily_route_id": daily_route_id}).to_list(500)
    return [str_id(vs) for vs in vehicle_stock]

@api_router.get("/vehicle-stock/my-stock")
async def get_my_vehicle_stock(current_user: dict = Depends(get_current_user)):
    """Driver gets their current vehicle stock"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    vehicle_stock = await db.vehicle_stock.find({
        "driver_id": current_user["id"],
        "date": today,
        "status": "active"
    }).to_list(500)
    return [str_id(vs) for vs in vehicle_stock]

# ==================== VEHICLE INSPECTIONS ====================

@api_router.get("/vehicle-inspections")
async def get_vehicle_inspections(
    date_str: Optional[str] = None,
    vehicle_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get all vehicle inspections"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    query = get_company_filter(current_user)
    query["vehicle_check"] = {"$exists": True, "$ne": None}
    
    if date_str:
        query["date"] = date_str
    if vehicle_id:
        query["vehicle_id"] = vehicle_id
    
    daily_routes = await db.daily_routes.find(query).sort("date", -1).to_list(200)
    
    inspections = []
    for dr in daily_routes:
        vc = dr.get("vehicle_check", {})
        if vc:
            inspections.append({
                "id": str(dr["_id"]),
                "daily_route_id": str(dr["_id"]),
                "date": dr.get("date", ""),
                "vehicle_id": dr.get("vehicle_id", ""),
                "vehicle_name": dr.get("vehicle_name", ""),
                "vehicle_registration": dr.get("vehicle_registration", ""),
                "driver_id": dr.get("driver_id", ""),
                "driver_name": dr.get("driver_name", ""),
                "route_name": dr.get("route_name", ""),
                "inspection": vc,
                "created_at": dr.get("started_at", dr.get("created_at"))
            })
    
    return inspections

@api_router.get("/vehicle-inspections/{daily_route_id}")
async def get_vehicle_inspection(daily_route_id: str, current_user: dict = Depends(get_current_user)):
    """Get vehicle inspection for a specific daily route"""
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(daily_route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    vc = daily_route.get("vehicle_check", {})
    if not vc:
        raise HTTPException(status_code=404, detail="No vehicle inspection found for this route")
    
    return {
        "id": str(daily_route["_id"]),
        "daily_route_id": str(daily_route["_id"]),
        "date": daily_route.get("date", ""),
        "vehicle_id": daily_route.get("vehicle_id", ""),
        "vehicle_name": daily_route.get("vehicle_name", ""),
        "vehicle_registration": daily_route.get("vehicle_registration", ""),
        "driver_id": daily_route.get("driver_id", ""),
        "driver_name": daily_route.get("driver_name", ""),
        "route_name": daily_route.get("route_name", ""),
        "inspection": vc,
        "created_at": daily_route.get("started_at", daily_route.get("created_at"))
    }

# ==================== CRATE TRACKING ====================

@api_router.get("/crates/tracking")
async def get_crate_tracking(current_user: dict = Depends(get_current_user)):
    """Get global crate tracking summary"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    crates = await db.crates_tracking.find_one({"type": "global"})
    
    # Calculate from sales and daily routes
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)
    
    cf = get_company_filter(current_user)
    cf["created_at"] = {"$gte": week_ago}
    
    sales = await db.sales.find(cf).to_list(5000)
    total_dropped = sum(s.get("crates_dropped", 0) for s in sales)
    total_collected = sum(s.get("crates_collected", 0) for s in sales)
    
    return {
        "crates_from_manufacturer": crates.get("crates_from_manufacturer", 0) if crates else 0,
        "crates_returned_to_manufacturer": crates.get("crates_returned_to_manufacturer", 0) if crates else 0,
        "crates_with_customers": total_dropped - total_collected,
        "total_dropped_last_7_days": total_dropped,
        "total_collected_last_7_days": total_collected,
        "net_crates": (crates.get("crates_from_manufacturer", 0) - crates.get("crates_returned_to_manufacturer", 0)) if crates else 0,
        "updated_at": crates.get("updated_at") if crates else None
    }

@api_router.get("/crates/daily-route/{route_id}")
async def get_route_crate_movements(route_id: str, current_user: dict = Depends(get_current_user)):
    """Get crate movements for a specific daily route"""
    daily_route = await db.daily_routes.find_one({"_id": ObjectId(route_id)})
    if not daily_route:
        raise HTTPException(status_code=404, detail="Daily route not found")
    
    # Get all sales for this route
    sales = await db.sales.find({"route_id": daily_route.get("route_id", "")}).to_list(1000)
    
    return {
        "daily_route_id": route_id,
        "route_name": daily_route.get("route_name", ""),
        "date": daily_route.get("date", ""),
        "crates_out": daily_route.get("crates_out", 0),
        "crates_in": daily_route.get("crates_in", 0),
        "total_dropped": sum(s.get("crates_dropped", 0) for s in sales),
        "total_collected": sum(s.get("crates_collected", 0) for s in sales),
        "net_crates": daily_route.get("crates_out", 0) - (daily_route.get("crates_in", 0) or 0)
    }

@api_router.get("/crates/history")
async def get_crate_history(days: int = 30, current_user: dict = Depends(get_current_user)):
    """Get crate movement history"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    start_date = datetime.utcnow() - timedelta(days=days)
    cf = get_company_filter(current_user)
    cf["created_at"] = {"$gte": start_date}
    
    sales = await db.sales.find(cf).sort("created_at", -1).to_list(2000)
    
    history = []
    for sale in sales:
        if sale.get("crates_dropped", 0) > 0 or sale.get("crates_collected", 0) > 0:
            history.append({
                "sale_id": str(sale["_id"]),
                "date": sale.get("created_at").strftime("%Y-%m-%d") if sale.get("created_at") else "",
                "customer_name": sale.get("customer_name", ""),
                "driver_name": sale.get("driver_name", ""),
                "crates_dropped": sale.get("crates_dropped", 0),
                "crates_collected": sale.get("crates_collected", 0),
                "net": sale.get("crates_dropped", 0) - sale.get("crates_collected", 0)
            })
    
    return history

# ==================== DASHBOARD ENDPOINTS ====================

@api_router.get("/dashboard/admin")
async def get_admin_dashboard(current_user: dict = Depends(get_current_user)):
    """Admin dashboard with comprehensive metrics"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    cf = get_company_filter(current_user)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Today's metrics
    today_str = today.strftime("%Y-%m-%d")
    today_sales = await db.sales.find({**cf, "created_at": {"$gte": today}, "is_voided": {"$ne": True}}).to_list(5000)
    today_routes = await db.daily_routes.find({**cf, "date": today_str}).to_list(100)
    
    # Active orders
    active_orders = await db.orders.find({**cf, "status": {"$in": ["pending", "confirmed", "adjusted", "packed"]}}).to_list(1000)
    
    # Stock levels
    products = await db.products.find(cf).to_list(500)
    low_stock_count = 0
    for p in products:
        stock = await db.stock.find_one({"product_id": str(p["_id"]), "company_id": current_user.get("company_id", "")})
        if stock and stock.get("quantity", 0) < 10:
            low_stock_count += 1
    
    # Customer count
    customer_count = await db.customers.count_documents({**cf, "is_active": {"$ne": False}})
    
    return {
        "today": {
            "date": today_str,
            "sales_count": len(today_sales),
            "total_revenue": sum(s.get("total_amount", 0) for s in today_sales),
            "total_collected": sum(s.get("cash_collected", 0) for s in today_sales),
            "active_routes": len([r for r in today_routes if r.get("status") == "active"]),
            "completed_routes": len([r for r in today_routes if r.get("status") == "completed"])
        },
        "orders": {
            "pending": len([o for o in active_orders if o.get("status") == "pending"]),
            "confirmed": len([o for o in active_orders if o.get("status") == "confirmed"]),
            "total_active": len(active_orders)
        },
        "inventory": {
            "total_products": len(products),
            "low_stock_items": low_stock_count
        },
        "customers": {
            "total_active": customer_count
        }
    }

@api_router.get("/dashboard/driver")
async def get_driver_dashboard(current_user: dict = Depends(get_current_user)):
    """Driver dashboard with their metrics"""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = today.strftime("%Y-%m-%d")
    
    # Today's route
    active_route = await db.daily_routes.find_one({
        "driver_id": current_user["id"],
        "date": today_str,
        "status": "active"
    })
    
    # Today's sales
    today_sales = await db.sales.find({
        "driver_id": current_user["id"],
        "created_at": {"$gte": today},
        "is_voided": {"$ne": True}
    }).to_list(1000)
    
    # Vehicle stock
    vehicle_stock = await db.vehicle_stock.find({
        "driver_id": current_user["id"],
        "date": today_str,
        "status": "active"
    }).to_list(500)
    
    return {
        "active_route": str_id(active_route) if active_route else None,
        "today": {
            "date": today_str,
            "sales_count": len(today_sales),
            "total_collected": sum(s.get("cash_collected", 0) for s in today_sales),
            "total_expected": sum(s.get("total_amount", 0) for s in today_sales)
        },
        "vehicle_stock": [str_id(vs) for vs in vehicle_stock]
    }

# ==================== STOCK ALERTS ====================

@api_router.get("/stock/alerts")
async def get_stock_alerts(threshold: int = 10, current_user: dict = Depends(get_current_user)):
    """Get low stock alerts"""
    if not is_admin_or_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin or Manager access required")
    
    cf = get_company_filter(current_user)
    products = await db.products.find(cf).to_list(500)
    company_id = current_user.get("company_id", "")
    
    alerts = []
    for product in products:
        product_id = str(product["_id"])
        stock = await db.stock.find_one({"product_id": product_id, "company_id": company_id})
        quantity = stock.get("quantity", 0) if stock else 0
        
        if quantity <= threshold:
            alerts.append({
                "product_id": product_id,
                "product_name": product["name"],
                "category": product["category"],
                "current_quantity": quantity,
                "threshold": threshold,
                "status": "out_of_stock" if quantity == 0 else "low_stock"
            })
    
    return alerts

@api_router.get("/stock/low-stock")
async def get_low_stock(threshold: int = 10, current_user: dict = Depends(get_current_user)):
    """Get products below threshold"""
    return await get_stock_alerts(threshold, current_user)

@api_router.get("/stock/out-of-stock")
async def get_out_of_stock(current_user: dict = Depends(get_current_user)):
    """Get out of stock products"""
    alerts = await get_stock_alerts(0, current_user)
    return [a for a in alerts if a["status"] == "out_of_stock"]
