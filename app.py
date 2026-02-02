# super_optimized_app.py - FIXED FOR RENDER
import os
import ssl

# 🔴 CRITICAL FIX: SSL issues with Python 3.13 + Firebase
os.environ['GRPC_VERBOSITY'] = 'ERROR'
os.environ['GRPC_DNS_RESOLVER'] = 'native'

# Fix SSL for Firebase gRPC on Render
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

from flask import Flask, render_template, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
import time
import json
import traceback
from datetime import datetime
from flask_cors import CORS

# ======================================================
# APP INIT - SIMPLIFIED FOR RENDER
# ======================================================
app = Flask(__name__)
CORS(app)  # Allow all origins for production

# 🔴 REMOVED: Memory limits (causing issues on Render)
# 🔴 REMOVED: numpy, psutil, threading (heavy for free tier)

# ======================================================
# FIREBASE CONFIG - WITH SSL FIXES
# ======================================================
if not firebase_admin._apps:
    try:
        # Try multiple credential sources for Render
        cred_paths = [
            "serviceAccountKey.json",
            os.environ.get("FIREBASE_CREDENTIALS", ""),
            "/etc/secrets/serviceAccountKey.json"
        ]
        
        cred = None
        for path in cred_paths:
            try:
                if path and os.path.exists(path):
                    cred = credentials.Certificate(path)
                    print(f"✅ Firebase credentials from: {path}")
                    break
            except:
                continue
        
        if not cred:
            # Try environment variable as JSON
            firebase_key_json = os.environ.get("FIREBASE_KEY_JSON")
            if firebase_key_json:
                cred = credentials.Certificate(json.loads(firebase_key_json))
                print("✅ Firebase credentials from env var")
            else:
                # Try simple key
                firebase_key = os.environ.get("FIREBASE_KEY")
                if firebase_key:
                    cred = credentials.Certificate(json.loads(firebase_key))
                    print("✅ Firebase credentials from FIREBASE_KEY")
                else:
                    raise Exception("No Firebase credentials found")
        
        # 🔴 CRITICAL: Initialize with gRPC options for Python 3.13 compatibility
        firebase_admin.initialize_app(cred, options={
            'projectId': cred.project_id,
            'grpc.max_receive_message_length': 10 * 1024 * 1024,  # 10MB limit for free tier
            'grpc.keepalive_time_ms': 30000,
        })
        
        print(f"✅ Firebase initialized for project: {cred.project_id}")
        
    except Exception as e:
        print(f"❌ Firebase initialization failed: {e}")
        # Set flag for disabled Firebase
        app.config['FIREBASE_DISABLED'] = True
        raise RuntimeError(f"Could not initialize Firebase: {e}")

db = firestore.client()

# ======================================================
# LIGHTWEIGHT CACHE - OPTIMIZED FOR RENDER FREE TIER
# ======================================================
class LightweightCache:
    """Simple cache that doesn't overload memory"""
    
    def __init__(self):
        self.cache = {}
        self.max_shops = 2  # Limit for free tier
        self.max_items_per_shop = 20
    
    def search_items(self, shop_id, query):
        """Direct Firestore search - minimal caching"""
        try:
            query = query.lower().strip()
            if len(query) < 2:
                return []
            
            # Check cache first (simple in-memory)
            cache_key = f"{shop_id}_{query}"
            if cache_key in self.cache:
                cached_time, results = self.cache[cache_key]
                if time.time() - cached_time < 30:  # 30 second cache
                    return results
            
            results = []
            
            # Direct Firestore query (lightweight)
            categories_ref = db.collection("Shops").document(shop_id).collection("categories")
            categories = list(categories_ref.limit(5).stream())  # Limit categories
            
            for cat_doc in categories:
                cat_id = cat_doc.id
                cat_data = cat_doc.to_dict()
                
                items_ref = categories_ref.document(cat_id).collection("items")
                items = list(items_ref.limit(10).stream())  # Limit items
                
                for item_doc in items:
                    item_data = item_doc.to_dict()
                    item_name = item_data.get("name", "").lower()
                    
                    if query in item_name:
                        # Get first active batch
                        batches = item_data.get("batches", [])
                        active_batch = None
                        for batch in batches[:3]:  # Check only first 3 batches
                            qty = float(batch.get("quantity", 0))
                            if qty > 0:
                                active_batch = batch
                                break
                        
                        if active_batch:
                            results.append({
                                "type": "main_item",
                                "item_id": item_doc.id,
                                "category_id": cat_id,
                                "category_name": cat_data.get("name", ""),
                                "name": item_data.get("name", ""),
                                "display_name": item_data.get("name", ""),
                                "batch_id": active_batch.get("id", ""),
                                "batch_name": active_batch.get("batchName", "Batch"),
                                "batch_remaining": float(active_batch.get("quantity", 0)),
                                "price": float(active_batch.get("sellPrice", 0) or active_batch.get("sell_price", 0)),
                                "base_unit": item_data.get("baseUnit", "unit"),
                                "unit_type": "base"
                            })
            
            # Cache results (limited size)
            if len(self.cache) > 50:  # Clear old cache if too large
                oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][0])
                del self.cache[oldest_key]
            
            self.cache[cache_key] = (time.time(), results[:15])  # Cache 15 results max
            
            return results[:15]  # Return max 15 results
            
        except Exception as e:
            print(f"❌ Search error: {e}")
            return []

cache = LightweightCache()

# ======================================================
# SIMPLE SALE PROCESSOR
# ======================================================
class SimpleSaleProcessor:
    """Simple sale processing for Render"""
    
    @staticmethod
    def complete_sale(data):
        """Complete sale with minimal operations"""
        try:
            shop_id = data.get("shop_id")
            items = data.get("items", [])
            
            if not shop_id or not items:
                return {"success": False, "error": "Missing shop_id or items"}
            
            if len(items) > 5:  # Small limit for free tier
                return {"success": False, "error": f"Too many items ({len(items)}). Max: 5"}
            
            print(f"🔄 Sale processing: {len(items)} items")
            
            processed = 0
            total_price = 0
            
            for cart_item in items:
                try:
                    # Process single item
                    result = SimpleSaleProcessor._process_item(cart_item, shop_id)
                    if result["success"]:
                        processed += 1
                        total_price += result.get("total_price", 0)
                except Exception as e:
                    print(f"❌ Item error: {e}")
                    continue
            
            return {
                "success": True,
                "processed": processed,
                "total_price": total_price,
                "message": f"Processed {processed} items"
            }
            
        except Exception as e:
            print(f"🔥 Sale error: {e}")
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def _process_item(cart_item, shop_id):
        """Process single item"""
        try:
            item_id = cart_item.get("item_id")
            category_id = cart_item.get("category_id")
            batch_id = cart_item.get("batch_id")
            quantity = float(cart_item.get("quantity", 1))
            
            if not all([item_id, category_id, batch_id]):
                return {"success": False, "error": "Missing item data"}
            
            # Get item reference
            item_ref = db.collection("Shops").document(shop_id) \
                .collection("categories").document(category_id) \
                .collection("items").document(item_id)
            
            item_doc = item_ref.get()
            if not item_doc.exists:
                return {"success": False, "error": "Item not found"}
            
            item_data = item_doc.to_dict()
            batches = item_data.get("batches", [])
            
            # Find batch
            for i, batch in enumerate(batches):
                if batch.get("id") == batch_id:
                    batch_qty = float(batch.get("quantity", 0))
                    if batch_qty >= quantity:
                        # Update batch
                        batches[i]["quantity"] = batch_qty - quantity
                        
                        # Update Firestore
                        item_ref.update({
                            "batches": batches,
                            "stock": float(item_data.get("stock", 0)) - quantity,
                            "lastStockUpdate": firestore.SERVER_TIMESTAMP
                        })
                        
                        price = float(batch.get("sellPrice", 0) or 0)
                        return {
                            "success": True,
                            "total_price": price * quantity
                        }
                    else:
                        return {"success": False, "error": "Insufficient stock"}
            
            return {"success": False, "error": "Batch not found"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}

# ======================================================
# BASIC MIDDLEWARE
# ======================================================
@app.before_request
def before_request():
    """Simple pre-request check"""
    # Limit request size
    if request.content_length and request.content_length > 50 * 1024:  # 50KB max
        return jsonify({"error": "Request too large"}), 413

# ======================================================
# ROUTES - SIMPLIFIED
# ======================================================

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/features")
def features():
    return render_template("features.html")

@app.route("/pricing")
def pricing():
    return render_template("pricing.html")

@app.route("/testimonials")
def testimonials():
    return render_template("testimonials.html")

@app.route("/story")
def story():
    return render_template("story.html")

@app.route("/admin")
def admin():
    return render_template("admindashboard.html")

@app.route("/health")
def health():
    """Lightweight health check"""
    return jsonify({
        "status": "healthy",
        "service": "shoptest",
        "timestamp": datetime.now().isoformat(),
        "python_version": os.environ.get("PYTHON_VERSION", "unknown")
    })

@app.route("/sales", methods=["POST"])
def sales():
    """Search endpoint"""
    try:
        data = request.get_json() or {}
        query = data.get("query", "").strip()
        shop_id = data.get("shop_id", "")
        
        if not query or not shop_id:
            return jsonify({"items": [], "meta": {"error": "Missing data"}})
        
        if len(query) < 2:
            return jsonify({"items": [], "meta": {"error": "Query too short"}})
        
        results = cache.search_items(shop_id, query)
        
        return jsonify({
            "items": results,
            "meta": {
                "query": query,
                "results": len(results)
            }
        })
        
    except Exception as e:
        return jsonify({"items": [], "meta": {"error": str(e)}})

@app.route("/complete-sale", methods=["POST"])
def complete_sale():
    """Sale completion endpoint"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400
        
        result = SimpleSaleProcessor.complete_sale(data)
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/ensure-plan", methods=["POST"])
def ensure_plan():
    """Simple plan endpoint"""
    try:
        data = request.get_json() or {}
        shop_id = data.get("shop_id")
        
        if not shop_id:
            return jsonify({"success": False, "error": "shop_id required"}), 400
        
        plan_ref = db.collection("Shops").document(shop_id).collection("plan").document("default")
        
        if not plan_ref.get().exists:
            plan_ref.set({
                "name": "Solo",
                "staffLimit": 0,
                "features": {"sell": True, "manageStock": True},
                "createdAt": firestore.SERVER_TIMESTAMP
            })
        
        return jsonify({"success": True})
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ======================================================
# ERROR HANDLERS
# ======================================================
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500

# ======================================================
# ENTRY POINT FOR RENDER
# ======================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Starting Flask app for Render on port {port}")
    print(f"🔧 Python version: {os.environ.get('PYTHON_VERSION', 'unknown')}")
    print(f"🔧 Firebase: {'ENABLED'}")
    app.run(host="0.0.0.0", port=port, debug=False)
