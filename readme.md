# Mzansi FMCG Tracker - Project Description

## Overview
Mzansi FMCG Tracker is a comprehensive distribution management system designed for Fast-Moving Consumer Goods (FMCG) businesses in South Africa. The platform streamlines the entire distribution workflow from warehouse to customer, including route management, sales tracking, inventory control, and customer ordering.

## What It Does

### Core Functionality
The system provides end-to-end management of FMCG distribution operations:

1. **Multi-Company Support**: Supports multiple distribution companies operating independently with complete data isolation
2. **Route Management**: Plan and manage delivery routes across South African provinces and districts
3. **Real-Time Sales Tracking**: Drivers record sales on mobile devices with instant synchronization
4. **Inventory Management**: Track stock levels, receive shipments, manage adjustments, and monitor vehicle stock
5. **Customer Ordering**: Customers can browse products and place orders through web or mobile portals
6. **Financial Tracking**: Monitor cash collection, shortages, payment methods (cash, EFT, split payments)
7. **Reporting & Analytics**: Generate comprehensive reports in Excel and PDF formats
8. **Vehicle Management**: Track vehicle usage, inspections, and stock dispatch

## Key Features

### For Administrators
- **Company Setup**: Register and manage distribution companies
- **User Management**: Create and manage users (admin, manager, driver, conductor roles)
- **Product Catalog**: Manage products with categories, pricing, and VAT settings
- **Route Planning**: Define delivery routes with schedules and coverage areas
- **Vehicle Fleet**: Track vehicles, capacity, and assignments
- **Stock Control**: Receive stock, manage adjustments, conduct stock takes
- **Order Management**: View, confirm, adjust, and fulfill customer orders
- **Reports**: Daily summaries, route performance, stock reports, sales analytics
- **Email Automation**: Configure automated report delivery

### For Drivers
- **Daily Route Management**: Start/end routes with vehicle checks and odometer readings
- **Sales Recording**: Record deliveries with product quantities, returns, damages
- **Customer Management**: Add new customers on the route
- **Crate Tracking**: Track crates dropped and collected
- **Payment Collection**: Record cash, EFT, Shop2Shop, Kazang, or split payments
- **Vehicle Stock**: View loaded stock and available quantities

### For Customers
- **Marketplace Browsing**: Browse products from multiple suppliers
- **Location-Based Matching**: Automatic route matching based on delivery area
- **Order Placement**: Place orders with delivery schedule visibility
- **Order Tracking**: Monitor order status from pending to delivered
- **Order History**: View past orders and reorder easily
- **Profile Management**: Manage business details and delivery information

### For Managers
- **Dashboard**: Real-time overview of operations
- **Sales Monitoring**: Track daily sales across all routes
- **Stock Oversight**: Monitor inventory levels and movements
- **Customer Management**: Manage customer accounts and pricing
- **Performance Analytics**: Route and driver performance metrics

## Technical Architecture

### Backend (Python/FastAPI)
- **Framework**: FastAPI with async/await support
- **Database**: MongoDB for flexible document storage
- **Authentication**: JWT token-based authentication
- **API Design**: RESTful API with comprehensive endpoints
- **Security**: Role-based access control, company data isolation
- **File Generation**: Excel (xlsxwriter) and PDF (ReportLab) reports

### Frontend Applications

#### Mobile App (React Native/Expo)
- **Platform**: iOS and Android support
- **Framework**: Expo with React Native
- **Navigation**: Expo Router for file-based routing
- **State Management**: React Context API
- **Offline Support**: AsyncStorage for local data persistence
- **Features**: Driver sales recording, route management, customer registration

#### Admin Portal (Next.js)
- **Framework**: Next.js 16 with App Router
- **UI**: React 19 with Tailwind CSS
- **Features**: Complete admin dashboard, user management, reports, settings
- **Deployment**: Standalone build for Docker/Vercel

#### Customer Portal (Next.js)
- **Framework**: Next.js 16 with App Router
- **UI**: React 19 with Tailwind CSS
- **Features**: Product browsing, shopping cart, order placement, order tracking
- **Deployment**: Standalone build for Docker/Vercel

## Business Workflows

### 1. Daily Route Operations
1. Driver starts route (vehicle check, opening odometer)
2. Admin dispatches stock to vehicle
3. Driver visits customers and records sales
4. System tracks deliveries, returns, damages, crates
5. Driver ends route (closing odometer, crates returned)
6. Admin receives returned stock back to warehouse

### 2. Customer Ordering
1. Customer registers with business details and location
2. System matches customer to available routes/suppliers
3. Customer browses products and places order
4. Distributor receives order notification
5. Admin confirms or adjusts order based on stock
6. Order is packed and assigned to route
7. Driver delivers order
8. Customer receives order confirmation

### 3. Stock Management
1. Admin receives stock from suppliers
2. System records damages, rejects, spoilage
3. Stock is dispatched to vehicles for routes
4. Sales automatically deduct from vehicle and warehouse stock
5. Unsold stock returns to warehouse
6. Stock takes reconcile physical vs system quantities

### 4. Financial Tracking
1. Sales record invoice amounts and cash collected
2. System calculates shortages (invoice - collected)
3. Multiple payment methods supported (cash, EFT, split)
4. Daily route summaries show collection rates
5. Reports track financial performance by route/driver

## Data Management

### Company Isolation
- Each company's data is completely isolated
- Users can only access their company's information
- Customers can browse multiple companies (marketplace model)
- Routes and products are company-specific

### Location Data
- Comprehensive South African location database
- 9 provinces with districts and areas/towns
- Automatic route matching based on customer location
- Delivery schedule management by route

### Stock Tracking
- Warehouse stock levels
- Vehicle stock dispatch and returns
- Sales deductions
- Stock movements audit trail
- Damages, spoilage, and adjustments

## Reporting & Analytics

### Available Reports
1. **Daily Summary**: Sales, routes, collection rates, product breakdown
2. **Route Performance**: Historical performance by route
3. **Stock Report**: Opening, received, sold, adjustments, closing
4. **Sales Details**: Comprehensive transaction listing
5. **Vehicle Inspections**: Pre-trip inspection records
6. **Product Breakdown**: Sales by product category

### Export Formats
- **Excel**: Multi-sheet workbooks with charts and formatting
- **PDF**: Professional reports with tables and summaries
- **Email**: Automated report delivery to configured recipients

## Deployment Options

### Development
- Local development with hot reload
- MongoDB local or cloud (MongoDB Atlas)
- Expo Go for mobile testing

### Production
- **Backend**: Docker container or cloud hosting (AWS, GCP, Azure)
- **Database**: MongoDB Atlas (managed cloud)
- **Mobile**: Expo EAS Build for app stores
- **Web Portals**: Vercel, Docker, or cloud platforms
- **Reverse Proxy**: Nginx for SSL and load balancing

## Security Features
- JWT authentication with expiration
- Role-based access control (RBAC)
- Company data isolation
- PIN-based mobile authentication
- Secure password hashing
- HTTPS enforcement in production
- Input validation and sanitization

## Scalability
- Async/await for concurrent operations
- MongoDB indexing for performance
- Pagination for large datasets
- Efficient query filtering
- Standalone Next.js builds for edge deployment

## Use Cases

### Small Distributors
- Single company with 2-5 routes
- 10-50 customers per route
- Basic product catalog (10-50 items)
- Daily operations and reporting

### Medium Distributors
- Multiple companies or branches
- 10-20 routes across regions
- 100-500 customers
- Extensive product catalog (100+ items)
- Advanced analytics and forecasting

### Large Distributors
- Enterprise-level operations
- 50+ routes nationwide
- 1000+ customers
- Complex product hierarchies
- Multi-warehouse operations
- Integration with ERP systems

## Future Enhancements
- Real-time GPS tracking
- Route optimization algorithms
- Customer credit management
- Loyalty programs
- Mobile payment integration
- WhatsApp notifications
- Advanced analytics and AI insights
- Multi-language support
- Barcode/QR code scanning

## Support & Maintenance
- Comprehensive API documentation
- User manuals for each role
- Video tutorials
- Technical support channels
- Regular updates and bug fixes

## License
Proprietary - Mzansi FMCG Tracker

## Contact
For inquiries, support, or customization requests, please contact the development team.
