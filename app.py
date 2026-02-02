# super_optimized_app.py
from flask import Flask, render_template, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
import numpy as np
import time
import json
import os
import gc
import psutil
import traceback
import threading
from datetime import datetime
import logging
from flask_cors import CORS
# ======================================================
# APP INIT WITH MEMORY LIMITS
# ======================================================
app = Flask(__name__)
CORS(app, origins=["https://macrosporic-osculant-giovani.ngrok-free.dev"])

# 🔴 CRITICAL: Limit request size to prevent memory overload
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024  # 100KB max request size

# ======================================================
# FIREBASE CONFIG
# ======================================================
if not firebase_admin._apps:
    # Always use local file for development
    try:
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
        print("✅ Firebase initialized from serviceAccountKey.json")
    except Exception as e:
        print(f"❌ Firebase initialization failed: {e}")
        # Try environment variable as fallback
        import os
        import json
        firebase_key = os.environ.get("FIREBASE_KEY")
        if firebase_key:
            cred = credentials.Certificate(json.loads(firebase_key))
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized from environment variable")
        else:
            raise RuntimeError("Could not initialize Firebase")

db = firestore.client()

# ======================================================
# MEMORY MANAGEMENT CLASS
# ======================================================
class MemoryManager:
    """Manages memory usage to prevent crashes"""
    
    @staticmethod
    def get_memory_usage():
        """Get current memory usage in MB"""
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        except:
            return 0
    
    @staticmethod
    def check_memory_limit(max_mb=250):
        """Check if we're approaching memory limits"""
        current = MemoryManager.get_memory_usage()
        if current > max_mb * 0.8:
            print(f"⚠️ Memory warning: {current:.1f}MB used")
            gc.collect()
        return current

# ======================================================
# OPTIMIZED CACHE SYSTEM
# ======================================================
class OptimizedCache:
    """Optimized cache with minimal memory footprint"""
    
    def __init__(self):
        self.shops = {}
        self.last_updated = 0
        self._lock = threading.Lock()
        
    def refresh_shop(self, shop_id):
        """Refresh cache for a single shop"""
        with self._lock:
            try:
                start_time = time.time()
                print(f"\n🔄 Refreshing cache for shop: {shop_id}")
                
                shop_doc = db.collection("Shops").document(shop_id).get()
                if not shop_doc.exists:
                    print(f"❌ Shop {shop_id} not found")
                    return False
                
                shop_data = shop_doc.to_dict()
                shop_cache = {
                    "shop_id": shop_id,
                    "shop_name": shop_data.get("name", ""),
                    "categories": {},
                    "last_updated": time.time()
                }
                
                # Get categories with limit
                categories_ref = db.collection("Shops").document(shop_id).collection("categories")
                categories_docs = list(categories_ref.limit(50).stream())
                
                print(f"📊 Found {len(categories_docs)} categories")
                
                for cat_doc in categories_docs:
                    cat_data = cat_doc.to_dict()
                    category_id = cat_doc.id
                    
                    category_cache = {
                        "category_id": category_id,
                        "category_name": cat_data.get("name", ""),
                        "items": {}
                    }
                    
                    # Get items with limit
                    items_ref = categories_ref.document(category_id).collection("items")
                    items_docs = list(items_ref.limit(100).stream())
                    
                    for item_doc in items_docs:
                        item_data = item_doc.to_dict()
                        item_id = item_doc.id
                        
                        # Get active batches only
                        batches = item_data.get("batches", [])
                        active_batches = []
                        for batch in batches[:5]:  # Limit to 5 batches
                            qty = float(batch.get("quantity", 0))
                            if qty > 0:
                                active_batches.append({
                                    "batch_id": batch.get("id", f"batch_{int(time.time()*1000)}"),
                                    "batch_name": batch.get("batchName", batch.get("batch_name", "Batch")),
                                    "quantity": qty,
                                    "sell_price": float(batch.get("sellPrice", 0) or batch.get("sell_price", 0)),
                                    "timestamp": batch.get("timestamp", 0),
                                    "unit": batch.get("unit", "unit")
                                })
                        
                        category_cache["items"][item_id] = {
                            "item_id": item_id,
                            "name": item_data.get("name", ""),
                            "thumbnail": item_data.get("images", [None])[0],
                            "sell_price": float(item_data.get("sellPrice", 0) or 0),
                            "stock": float(item_data.get("stock", 0) or 0),
                            "base_unit": item_data.get("baseUnit", "unit"),
                            "category_id": category_id,
                            "category_name": cat_data.get("name", ""),
                            "batches": active_batches[:3],  # Keep only top 3 active batches
                            "selling_units": self._get_selling_units(shop_id, category_id, item_id)
                        }
                    
                    shop_cache["categories"][category_id] = category_cache
                
                self.shops[shop_id] = shop_cache
                self.last_updated = time.time()
                
                print(f"✅ Cache refreshed for {shop_id} in {time.time()-start_time:.2f}s")
                return True
                
            except Exception as e:
                print(f"❌ Cache refresh error: {e}")
                return False
    
    def _get_selling_units(self, shop_id, category_id, item_id):
        """Get selling units with minimal data"""
        try:
            sell_units_ref = (db.collection("Shops").document(shop_id)
                            .collection("categories").document(category_id)
                            .collection("items").document(item_id)
                            .collection("sellUnits"))
            
            sell_units_docs = list(sell_units_ref.limit(10).stream())
            selling_units = []
            
            for doc in sell_units_docs:
                data = doc.to_dict()
                selling_units.append({
                    "sell_unit_id": doc.id,
                    "name": data.get("name", ""),
                    "conversion_factor": float(data.get("conversionFactor", 1.0)),
                    "sell_price": float(data.get("sellPrice", 0.0)),
                    "thumbnail": data.get("images", [None])[0]
                })
            
            return selling_units[:5]  # Limit to 5 selling units
            
        except Exception:
            return []  # Return empty list on error
    
    def get_shop(self, shop_id):
        """Get shop from cache or refresh if needed"""
        if shop_id not in self.shops or time.time() - self.shops[shop_id].get("last_updated", 0) > 300:
            self.refresh_shop(shop_id)
        return self.shops.get(shop_id)
    
    def search_items(self, shop_id, query):
        """Fast in-memory search - SIMPLIFIED VERSION"""
        shop = self.get_shop(shop_id)
        if not shop:
            return []
        
        query = query.lower().strip()
        results = []
        
        for category_id, category in shop.get("categories", {}).items():
            for item_id, item in category.get("items", {}).items():
                item_name = item.get("name", "").lower()
                
                # Check main item
                if query in item_name:
                    active_batch = None
                    batches = item.get("batches", [])
                    if batches:
                        for batch in batches:
                            if float(batch.get("quantity", 0)) > 0:
                                active_batch = batch
                                break
                    
                    if active_batch:
                        results.append({
                            "type": "main_item",
                            "item_id": item_id,
                            "category_id": category_id,
                            "category_name": item.get("category_name", ""),
                            "name": item.get("name", ""),
                            "display_name": item.get("name", ""),
                            "thumbnail": item.get("thumbnail"),
                            "batch_id": active_batch.get("batch_id"),
                            "batch_name": active_batch.get("batch_name", "Batch"),
                            "batch_remaining": float(active_batch.get("quantity", 0)),
                            "price": round(float(active_batch.get("sell_price", 0)), 2),
                            "base_unit": item.get("base_unit", "unit"),
                            "unit_type": "base"
                        })
                
                # Check selling units
                for su in item.get("selling_units", []):
                    su_name = su.get("name", "").lower()
                    if query in su_name and active_batch:
                        conversion = float(su.get("conversion_factor", 1))
                        if conversion > 0:
                            results.append({
                                "type": "selling_unit",
                                "item_id": item_id,
                                "sell_unit_id": su.get("sell_unit_id"),
                                "category_id": category_id,
                                "category_name": item.get("category_name", ""),
                                "name": f"{su.get('name')}",
                                "display_name": su.get("name"),
                                "parent_item_name": item.get("name", ""),
                                "thumbnail": su.get("thumbnail") or item.get("thumbnail"),
                                "batch_id": active_batch.get("batch_id"),
                                "batch_remaining": float(active_batch.get("quantity", 0)),
                                "price": round(float(active_batch.get("sell_price", 0)) / conversion, 4),
                                "available_stock": round(float(active_batch.get("quantity", 0)) * conversion, 2),
                                "conversion_factor": conversion,
                                "base_unit": item.get("base_unit", "unit"),
                                "unit_type": "selling_unit"
                            })
        
        # Sort by type then name
        results.sort(key=lambda x: (x.get("type") == "selling_unit", x.get("name", "").lower()))
        
        return results[:50]  # Limit to 50 results

# Initialize cache
cache = OptimizedCache()

# ======================================================
# OPTIMIZED COMPLETE SALE FUNCTION
# ======================================================
class SaleProcessor:
    """Process sales with memory and performance optimizations"""
    
    @staticmethod
    def complete_sale(data):
        """Complete sale with minimal memory footprint"""
        start_time = time.time()
        
        try:
            shop_id = data.get("shop_id")
            seller = data.get("seller", {})
            items = data.get("items", [])
            
            # 🔴 CRITICAL: Validate input size
            if not shop_id or not items:
                return {"success": False, "error": "Missing shop_id or items"}, 400
            
            if len(items) > 10:  # REDUCED FROM 20
                return {"success": False, "error": f"Too many items ({len(items)}). Max: 10"}, 400
            
            print(f"🔄 Sale processing: {len(items)} items for shop {shop_id[:8]}...")
            
            processed_items = []
            batch_operations = []
            total_price = 0
            
            for idx, cart_item in enumerate(items):
                # Process each item with memory cleanup
                result = SaleProcessor._process_item(cart_item, shop_id, seller)
                if not result.get("success"):
                    print(f"❌ Item {idx+1} failed: {result.get('error')}")
                    continue
                
                # Add to batch operations
                item_update = result.get("item_update")
                if item_update:
                    batch_operations.append(item_update)
                
                # Track processed item
                processed_items.append(result.get("item_info", {}))
                total_price += result.get("total_price", 0)
                
                # 🔴 CRITICAL: Force garbage collection every 3 items
                if idx % 3 == 0:
                    gc.collect()
            
            # Execute all batch operations
            if batch_operations:
                try:
                    # Group operations by item to avoid duplicate updates
                    grouped_operations = {}
                    for op in batch_operations:
                        key = f"{op['ref'].path}"
                        if key not in grouped_operations:
                            grouped_operations[key] = op
                        else:
                            # Merge data for same item
                            grouped_operations[key]['data'].update(op['data'])
                    
                    # Execute in batches of 10
                    batch = db.batch()
                    batch_count = 0
                    
                    for op in grouped_operations.values():
                        batch.update(op['ref'], op['data'])
                        batch_count += 1
                        
                        if batch_count >= 10:
                            batch.commit()
                            batch = db.batch()
                            batch_count = 0
                    
                    if batch_count > 0:
                        batch.commit()
                    
                    print(f"✅ Committed {len(grouped_operations)} item updates")
                    
                except Exception as e:
                    print(f"❌ Batch commit failed: {e}")
                    return {"success": False, "error": f"Batch commit failed: {e}"}, 500
            
            processing_time = time.time() - start_time
            
            print(f"✅ Sale complete: {len(processed_items)} items, {processing_time:.2f}s")
            
            return {
                "success": True,
                "updated_items": processed_items,
                "processed": len(processed_items),
                "failed": len(items) - len(processed_items),
                "total_price": total_price,
                "message": f"Processed {len(processed_items)} items successfully",
                "performance": {
                    "time_ms": round(processing_time * 1000, 2)
                }
            }, 200
            
        except Exception as e:
            print(f"🔥 Sale processing error: {e}")
            traceback.print_exc()
            return {"success": False, "error": str(e)}, 500
    
    @staticmethod
    def _process_item(cart_item, shop_id, seller):
        """Process a single sale item - SIMPLIFIED"""
        try:
            # Extract and validate
            item_id = cart_item.get("item_id")
            category_id = cart_item.get("category_id")
            batch_id = cart_item.get("batch_id") or cart_item.get("batchId")
            quantity = float(cart_item.get("quantity", 0))
            item_type = cart_item.get("type", "main_item")
            conversion_factor = float(cart_item.get("conversion_factor", 1))
            
            if not all([item_id, category_id, batch_id]) or quantity <= 0:
                return {"success": False, "error": "Invalid item data"}
            
            # Get item reference
            item_ref = (db.collection("Shops")
                       .document(shop_id)
                       .collection("categories")
                       .document(category_id)
                       .collection("items")
                       .document(item_id))
            
            # Get item data
            item_doc = item_ref.get()
            if not item_doc.exists:
                return {"success": False, "error": f"Item {item_id} not found"}
            
            item_data = item_doc.to_dict()
            
            # Find batch
            batches = item_data.get("batches", [])
            batch_index = -1
            target_batch = None
            
            for i, batch in enumerate(batches):
                if batch.get("id") == batch_id:
                    batch_index = i
                    target_batch = batch
                    break
            
            if batch_index == -1:
                return {"success": False, "error": f"Batch {batch_id} not found"}
            
            # Calculate base quantity
            batch_qty = float(target_batch.get("quantity", 0))
            
            if item_type == "selling_unit":
                base_qty = quantity / conversion_factor if conversion_factor > 0 else quantity
            else:
                base_qty = quantity
            
            # Check stock
            if batch_qty < base_qty:
                return {
                    "success": False,
                    "error": f"Insufficient stock: {batch_qty} available, {base_qty} needed"
                }
            
            # Calculate price
            sell_price = float(target_batch.get("sellPrice", 0) or target_batch.get("sell_price", 0))
            if item_type == "selling_unit":
                unit_price = sell_price / conversion_factor if conversion_factor > 0 else sell_price
                total_price = unit_price * quantity
            else:
                total_price = sell_price * base_qty
            
            # Prepare batch update
            batches[batch_index]["quantity"] = batch_qty - base_qty
            
            # Prepare transaction record
            transaction_id = f"sale_{int(time.time()*1000)}_{item_id[:8]}"
            stock_txn = {
                "id": transaction_id,
                "type": "sale",
                "item_type": item_type,
                "batchId": batch_id,
                "quantity": base_qty,
                "unitPrice": sell_price if item_type != "selling_unit" else unit_price,
                "totalPrice": total_price,
                "timestamp": int(time.time()),
                "performedBy": seller.get("name", "") if isinstance(seller, dict) else str(seller)
            }
            
            # Calculate new total stock
            current_stock = float(item_data.get("stock", 0))
            new_stock = current_stock - base_qty
            
            return {
                "success": True,
                "item_update": {
                    "ref": item_ref,
                    "data": {
                        "batches": batches,
                        "stock": new_stock,
                        "stockTransactions": firestore.ArrayUnion([stock_txn]),
                        "lastStockUpdate": firestore.SERVER_TIMESTAMP
                    }
                },
                "item_info": {
                    "item_id": item_id,
                    "item_type": item_type,
                    "batch_id": batch_id,
                    "quantity_sold": quantity,
                    "base_units_deducted": base_qty,
                    "remaining_batch_quantity": batches[batch_index]["quantity"],
                    "remaining_total_stock": new_stock,
                    "batch_exhausted": batches[batch_index]["quantity"] <= 0.001,
                    "total_price": total_price
                },
                "total_price": total_price
            }
            
        except Exception as e:
            print(f"❌ Item processing error: {e}")
            return {"success": False, "error": str(e)}

# ======================================================
# MIDDLEWARE FOR MEMORY PROTECTION
# ======================================================
# Flag to track first request
_initialized = False
_init_lock = threading.Lock()

@app.before_request
def before_request():
    """Memory check before each request and initialize on first request"""
    global _initialized
    
    # Initialize on first request
    if not _initialized:
        with _init_lock:
            if not _initialized:
                startup()
                _initialized = True
    
    MemoryManager.check_memory_limit()
    
    # Check for large requests
    if request.content_length and request.content_length > 100 * 1024:
        return jsonify({"error": "Request too large (max 100KB)"}), 413

@app.after_request
def after_request(response):
    """Cleanup after each request"""
    # Force garbage collection
    gc.collect()
    return response

# ======================================================
# STARTUP FUNCTION
# ======================================================
def startup():
    """Initial startup - called on first request"""
    print("🚀 Starting optimized Flask app...")
    print("📊 Initial memory usage:", MemoryManager.get_memory_usage(), "MB")
    print("✅ App initialized successfully")

# ======================================================
# OPTIMIZED ROUTES
# ======================================================

# PLANS CONFIG (minimized)
PLANS_CONFIG = {
    "SOLO": {
        "id": "SOLO",
        "name": "Solo",
        "staff_limit": 0,
        "price_kes": 0,
        "description": "Perfect for individual entrepreneurs"
    }
}

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/features")
def features():
    return render_template("features.html")

@app.route("/pricing")
def pricing():
    return render_template("pricing.html", plans=PLANS_CONFIG.values())

@app.route("/testimonials")
def testimonials():
    return render_template("testimonials.html")

@app.route("/story")
def story():
    return render_template("story.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/admin")
def admin():
    return render_template("admindashboard.html")

# ======================================================
# OPTIMIZED SALES SEARCH (SIMPLE VERSION)
# ======================================================
@app.route("/sales", methods=["POST"])
def sales():
    """Optimized sales search - SIMPLIFIED"""
    start_time = time.time()
    
    try:
        data = request.get_json(silent=True) or {}
        query = (data.get("query") or "").strip()
        shop_id = data.get("shop_id")
        
        if not query or not shop_id:
            return jsonify({
                "items": [],
                "meta": {"error": "Missing query or shop_id"}
            }), 400
        
        if len(query) < 2:
            return jsonify({
                "items": [],
                "meta": {"error": "Query too short (min 2 characters)"}
            }), 400
        
        # Use cache for fast search
        results = cache.search_items(shop_id, query)
        processing_time = time.time() - start_time
        
        return jsonify({
            "items": results,
            "meta": {
                "shop_id": shop_id,
                "query": query,
                "results": len(results),
                "processing_time_ms": round(processing_time * 1000, 2)
            }
        })
        
    except Exception as e:
        print(f"❌ Sales search error: {e}")
        return jsonify({
            "items": [],
            "meta": {"error": str(e)}
        }), 500

# ======================================================
# OPTIMIZED COMPLETE SALE ENDPOINT
# ======================================================
@app.route("/complete-sale", methods=["POST"])
def complete_sale():
    """Optimized sale completion"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        
        if not data:
            return jsonify({"success": False, "error": "Invalid JSON"}), 400
        
        result, status_code = SaleProcessor.complete_sale(data)
        return jsonify(result), status_code
        
    except Exception as e:
        print(f"🔥 Complete sale endpoint error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ======================================================
# OPTIMIZED HEALTH CHECK
# ======================================================
@app.route("/health", methods=["GET"])
def health_check():
    """Lightweight health check"""
    memory_usage = MemoryManager.get_memory_usage()
    
    return jsonify({
        "status": "healthy",
        "memory_mb": round(memory_usage, 2),
        "cache_shops": len(cache.shops),
        "timestamp": datetime.now().isoformat()
    })

# ======================================================
# OPTIMIZED PLAN ENDPOINT
# ======================================================
@app.route("/ensure-plan", methods=["POST"])
def ensure_plan():
    """Lightweight plan initialization"""
    try:
        data = request.get_json(silent=True) or {}
        shop_id = data.get("shop_id")
        
        if not shop_id:
            return jsonify({"success": False, "error": "shop_id required"}), 400
        
        plan_ref = db.collection("Shops").document(shop_id).collection("plan").document("default")
        
        if plan_ref.get().exists:
            return jsonify({"success": True, "message": "Plan exists"})
        
        # Create minimal plan
        plan_ref.set({
            "name": "Solo",
            "staffLimit": 0,
            "features": {
                "sell": True,
                "manageStock": True,
                "businessIntelligence": False,
                "settings": True
            },
            "createdAt": firestore.SERVER_TIMESTAMP
        })
        
        return jsonify({"success": True, "message": "Plan created"})
        
    except Exception as e:
        print(f"⚠️ Plan error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ======================================================
# CACHE MANAGEMENT ENDPOINTS
# ======================================================
@app.route("/refresh-cache", methods=["POST"])
def refresh_cache():
    """Manually refresh cache"""
    try:
        data = request.get_json(silent=True) or {}
        shop_id = data.get("shop_id")
        
        if shop_id:
            success = cache.refresh_shop(shop_id)
            message = f"Cache refreshed for shop {shop_id}" if success else "Refresh failed"
        else:
            success = False
            message = "shop_id required"
        
        return jsonify({
            "success": success,
            "message": message,
            "shops_cached": len(cache.shops)
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ======================================================
# ERROR HANDLERS
# ======================================================
@app.errorhandler(413)
def too_large(error):
    return jsonify({"error": "Request too large (max 100KB)"}), 413

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def server_error(error):
    print(f"🔥 Server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

# ======================================================
# ENTRY POINT WITH MEMORY PROTECTION AND URL DISPLAY
# ======================================================
if __name__ == "__main__":
    # Set Flask environment
    os.environ['FLASK_ENV'] = 'development'  # Changed from 'production' to show URL
    
    # Don't disable logging - we want to see the URL
    print("\n" + "="*60)
    print("🚀 STARTING FLASK SERVER")
    print("="*60)
    
    # Start the server
    try:
        # Enable debug mode to see URL
        app.run(
            host='127.0.0.1',  # Changed from '0.0.0.0' to localhost
            port=5000,
            debug=True,  # ✅ ENABLED DEBUG MODE TO SHOW URL
            threaded=True,
            use_reloader=False  # Disable reloader for cleaner output
        )
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped by user")
    except Exception as e:
        print(f"\n❌ Server error: {e}")
        traceback.print_exc()
