# Complete GET API Endpoints

## Authentication & User Management
- **GET /api/auth/me** - Get current user profile
- **GET /api/users** - Get all users (Admin only)

## Company Management
- **GET /api/companies/mine** - Get current user's company details
- **GET /api/companies/list** - Public: List all companies (for customer registration)
- **GET /api/companies/{company_id}/routes** - Public: List routes for a company
- **GET /api/companies/{company_id}/products** - Public: List products for a company

## Products
- **GET /api/products** - Get all products (company-scoped)

## Customers
- **GET /api/customers** - Get all customers (company-scoped)
  - Query params: `route_id`, `include_inactive`
- **GET /api/customers/{customer_id}** - Get single customer
- **GET /api/customers/{customer_id}/history** - Get customer purchase history
  - Query params: `days` (default: 30)
- **GET /api/customers/{customer_id}/prices** - Get custom prices for a customer

## Routes
- **GET /api/routes** - Get all routes (company-scoped)
- **GET /api/routes/{route_id}/customers** - Get customers on a route
- **GET /api/routes/{route_id}/schedule** - Get route delivery schedule

## Vehicles
- **GET /api/vehicles** - Get all vehicles (company-scoped)
  - Query params: `include_inactive`
- **GET /api/vehicles/available** - Get vehicles not currently in use

## Sales
- **GET /api/sales** - Get all sales (company-scoped)
  - Query params: `route_id`, `date_str`, `customer_id`, `include_voided`
- **GET /api/sales/{sale_id}** - Get single sale
- **GET /api/sales/customer/{customer_id}** - Get sales history for a customer

## Orders (Customer Ordering System)
- **GET /api/orders** - Get all orders
  - Query params: `status`, `route_id`, `date_str`
  - Customers see only their orders
  - Admin/Manager see company orders
- **GET /api/orders/{order_id}** - Get single order
- **GET /api/orders/dashboard/summary** - Get order dashboard summary (Distributor only)
- **GET /api/orders/packing/{route_id}** - Get route packing summary (Distributor only)

## Stock Management
- **GET /api/stock** - Get all stock records (company-scoped) ✨ NEW
- **GET /api/stock/levels** - Get current stock levels for all products (company-scoped)
- **GET /api/stock/movements** - Get stock movement history
  - Query params: `product_id`, `movement_type`, `days` (default: 30)
- **GET /api/stock/report** - Generate comprehensive stock report with opening, received, sold, adjustments, closing

## Reports
- **GET /api/reports** - Get all generated reports (company-scoped) ✨ NEW
- **GET /api/reports/daily-summary** - Get daily summary report
  - Query params: `date_str`
- **GET /api/reports/route-performance/{route_id}** - Get route performance metrics
  - Query params: `days` (default: 7)
- **GET /api/reports/export/excel** - Export route report to Excel
  - Query params: `date_str`, `route_id`
- **GET /api/reports/export/pdf** - Export route report to PDF
  - Query params: `date_str`, `route_id`, `driver_id`, `customer_id`
- **GET /api/reports/email-logs** - Get email send history (Admin/Manager only)

## Daily Routes
- **GET /api/daily-routes/active** - Get active routes for current driver (or all for admin/manager)
- **GET /api/daily-routes/active/all** - Get all active routes across all drivers (Admin/Manager only)
- **GET /api/daily-routes/history** - Get daily route history
  - Query params: `driver_id`
- **GET /api/daily-routes/{route_id}** - Get specific daily route by ID

## Locations (South Africa)
- **GET /api/locations/provinces** - Get all SA provinces
- **GET /api/locations/districts/{province}** - Get districts for a province
- **GET /api/locations/areas/{province}/{district}** - Get areas/towns for a district

## Customer Portal
- **GET /api/customer/available-companies** - Customer sees all companies that deliver to their area (Marketplace)
- **GET /api/customer/company/{company_id}/products** - Customer browses products from a specific company
- **GET /api/customer/products** - Customer sees products from their assigned distributor (legacy)
- **GET /api/customer/delivery-info** - Get customer's delivery info and profile

## Settings & Configuration
- **GET /api/permissions** - Get current user's permissions
- **GET /api/settings/email** - Get email configuration (Admin only)
- **GET /api/admin/settings/email** - Get email settings (Admin/Manager only)
- **GET /api/admin/email-recipients** - Get all email recipients (Admin/Manager only)

## Health & Status
- **GET /** - Root endpoint (health check)
- **GET /health** - Detailed health check with database connection status

---

## Summary by Database Table

### ✅ Customers
- GET /api/customers
- GET /api/customers/{customer_id}
- GET /api/customers/{customer_id}/history
- GET /api/customers/{customer_id}/prices

### ✅ Orders
- GET /api/orders
- GET /api/orders/{order_id}
- GET /api/orders/dashboard/summary
- GET /api/orders/packing/{route_id}

### ✅ Products
- GET /api/products
- GET /api/companies/{company_id}/products

### ✅ Reports
- GET /api/reports ✨ NEW
- GET /api/reports/daily-summary
- GET /api/reports/route-performance/{route_id}
- GET /api/reports/export/excel
- GET /api/reports/export/pdf
- GET /api/reports/email-logs

### ✅ Routes
- GET /api/routes
- GET /api/routes/{route_id}/customers
- GET /api/routes/{route_id}/schedule
- GET /api/daily-routes/active
- GET /api/daily-routes/active/all
- GET /api/daily-routes/history
- GET /api/daily-routes/{route_id}

### ✅ Sales
- GET /api/sales
- GET /api/sales/{sale_id}
- GET /api/sales/customer/{customer_id}

### ✅ Stock
- GET /api/stock ✨ NEW
- GET /api/stock/levels
- GET /api/stock/movements
- GET /api/stock/report

### ✅ Users
- GET /api/users
- GET /api/auth/me

### ✅ Vehicles
- GET /api/vehicles
- GET /api/vehicles/available

---

## Authentication

All endpoints (except public ones) require authentication via Bearer token:

```
Authorization: Bearer <your_jwt_token>
```

Get token from:
- POST /api/auth/login
- POST /api/auth/register

## Company Scoping

Most endpoints are automatically scoped to the user's company. This means:
- Users only see data from their own company
- Customers in marketplace mode can browse multiple companies
- Admin/Manager roles have full access to their company's data
- Drivers see only their own routes and sales
