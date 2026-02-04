// completeSale.js - FRONTEND SALE COMPLETION MODULE
// Replaces the Flask backend route with pure frontend logic

import { 
    doc, 
    getDoc, 
    updateDoc, 
    arrayUnion,
    serverTimestamp 
} from "https://www.gstatic.com/firebasejs/9.23.0/firebase-firestore.js";
import { db } from "./firebase-config.js";

// ====================================================
// CONFIGURATION
// ====================================================

const DEBUG_MODE = true;

// ====================================================
// UTILITY FUNCTIONS
// ====================================================

function debugLog(message, data = null) {
    if (DEBUG_MODE) {
        console.log(`🔥 ${message}`, data || '');
    }
}

function generateTransactionId() {
    return `sale_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
}

function getCurrentTimestamp() {
    return Math.floor(Date.now() / 1000);
}

// ====================================================
// SALE COMPLETION LOGIC (REPLACING BACKEND)
// ====================================================

/**
 * Complete sale directly from frontend
 * @param {Object} saleData - Sale data from cart
 * @returns {Object} Result of sale completion
 */
async function completeSaleFrontend(saleData) {
    debugLog('STARTING FRONTEND SALE COMPLETION', saleData);
    
    try {
        const { shop_id, seller, items } = saleData;
        
        if (!shop_id || !items || !Array.isArray(items) || items.length === 0) {
            throw new Error('Missing shop_id or items');
        }
        
        const updatedItems = [];
        const errors = [];
        
        debugLog(`Shop ID: ${shop_id} | Items: ${items.length}`);
        
        // Process each item sequentially
        for (let idx = 0; idx < items.length; idx++) {
            const cartItem = items[idx];
            
            try {
                const result = await processSaleItem(shop_id, seller, cartItem, idx);
                updatedItems.push(result);
                debugLog(`✅ Item ${idx + 1} processed successfully`);
            } catch (error) {
                errors.push({
                    item: cartItem,
                    error: error.message,
                    index: idx
                });
                debugLog(`❌ Item ${idx + 1} failed:`, error.message);
            }
        }
        
        // If any items failed, rollback successful ones
        if (errors.length > 0) {
            debugLog('Rolling back successful items due to errors', errors);
            
            // Attempt rollback for successful items
            for (const item of updatedItems) {
                if (item.rolled_back !== true) {
                    try {
                        await rollbackItemDeduction(item);
                    } catch (rollbackError) {
                        console.error('Rollback failed:', rollbackError);
                    }
                }
            }
            
            throw new Error(`Sale partially failed: ${errors.length} item(s) could not be processed. ${errors[0].error}`);
        }
        
        // Create sale record
        const saleRecord = await createSaleRecord(shop_id, seller, items, updatedItems);
        
        debugLog('🎉 SALE COMPLETED SUCCESSFULLY', {
            items_processed: updatedItems.length,
            sale_id: saleRecord.id
        });
        
        return {
            success: true,
            updated_items: updatedItems,
            sale_record: saleRecord,
            message: `Sale completed successfully. ${updatedItems.length} item(s) processed.`
        };
        
    } catch (error) {
        debugLog('🔥 SALE COMPLETION ERROR:', error);
        return {
            success: false,
            error: error.message,
            trace: error.stack
        };
    }
}

/**
 * Process a single sale item
 */
async function processSaleItem(shop_id, seller, cartItem, index) {
    debugLog(`\n📦 Processing item ${index + 1}`, cartItem);
    
    const {
        item_id,
        category_id,
        batch_id,
        quantity,
        unit = "unit",
        conversion_factor = 1,
        type = "main_item"
    } = cartItem;
    
    // Validate required fields
    if (!item_id || !category_id || !batch_id || !quantity || quantity <= 0) {
        throw new Error(`Invalid item data: ${JSON.stringify({ item_id, category_id, batch_id, quantity })}`);
    }
    
    const quantityNum = parseFloat(quantity);
    const conversionFactorNum = parseFloat(conversion_factor);
    
    debugLog(`   Type: ${type} | Qty: ${quantityNum} | Conv: ${conversionFactorNum}`);
    
    // Get item reference
    const itemRef = doc(
        db,
        "Shops",
        shop_id,
        "categories",
        category_id,
        "items",
        item_id
    );
    
    const itemDoc = await getDoc(itemRef);
    
    if (!itemDoc.exists()) {
        throw new Error(`Item ${item_id} not found in category ${category_id}`);
    }
    
    const itemData = itemDoc.data();
    const batches = itemData.batches || [];
    const totalStock = parseFloat(itemData.stock || 0);
    
    // Find target batch
    const batchIndex = batches.findIndex(b => b.id === batch_id);
    if (batchIndex === -1) {
        throw new Error(`Batch ${batch_id} not found for item ${itemData.name}`);
    }
    
    const batch = batches[batchIndex];
    const batchQty = parseFloat(batch.quantity || 0);
    const sellPrice = parseFloat(batch.sellPrice || batch.sell_price || 0);
    
    debugLog(`   Batch available: ${batchQty} base units`);
    
    // Calculate base quantity (critical conversion logic)
    let baseQty;
    let unitPrice;
    let totalPrice;
    
    if (type === "selling_unit") {
        // Selling units: quantity ÷ conversion_factor
        baseQty = quantityNum / conversionFactorNum;
        unitPrice = sellPrice / conversionFactorNum;
        totalPrice = unitPrice * quantityNum;
        
        debugLog(`   Selling unit: ${quantityNum} units ÷ ${conversionFactorNum} = ${baseQty} base units`);
        debugLog(`   Unit price: $${sellPrice} ÷ ${conversionFactorNum} = $${unitPrice}`);
    } else {
        // Main item: no conversion needed
        baseQty = quantityNum;
        unitPrice = sellPrice;
        totalPrice = sellPrice * baseQty;
        
        debugLog(`   Main item: ${quantityNum} base units`);
    }
    
    debugLog(`   Required to deduct: ${baseQty} base units`);
    
    // Validate stock availability
    if (batchQty < baseQty) {
        throw new Error(
            `Insufficient stock in batch ${batch_id}. ` +
            `Available: ${batchQty} base units, Requested: ${baseQty} base units`
        );
    }
    
    // Calculate new quantities
    const newBatchQty = batchQty - baseQty;
    const newTotalStock = totalStock - baseQty;
    
    // Create stock transaction
    const stockTxn = {
        id: generateTransactionId(),
        type: "sale",
        item_type: type,
        batchId: batch_id,
        quantity: baseQty,
        selling_units_quantity: type === "selling_unit" ? quantityNum : null,
        unit: unit,
        sellPrice: sellPrice,
        unitPrice: unitPrice,
        totalPrice: totalPrice,
        timestamp: getCurrentTimestamp(),
        performedBy: seller,
        conversion_factor: type === "selling_unit" ? conversionFactorNum : null
    };
    
    // Update batch quantity
    const updatedBatches = [...batches];
    updatedBatches[batchIndex] = {
        ...updatedBatches[batchIndex],
        quantity: newBatchQty
    };
    
    // Get existing stock transactions
    const stockTransactions = itemData.stockTransactions || [];
    
    // Update Firestore document
    await updateDoc(itemRef, {
        batches: updatedBatches,
        stock: newTotalStock,
        stockTransactions: arrayUnion(stockTxn),
        lastStockUpdate: serverTimestamp(),
        lastTransactionId: stockTxn.id
    });
    
    debugLog(`   ✅ Deducted: ${baseQty} base units`);
    debugLog(`   ✅ Remaining in batch: ${newBatchQty}`);
    debugLog(`   ✅ Total price: $${totalPrice}`);
    
    return {
        item_id,
        item_type: type,
        batch_id,
        quantity_sold: quantityNum,
        base_units_deducted: baseQty,
        remaining_batch_quantity: newBatchQty,
        remaining_total_stock: newTotalStock,
        batch_exhausted: newBatchQty === 0,
        total_price: totalPrice,
        unit_price: unitPrice,
        transaction_id: stockTxn.id,
        item_ref: itemRef, // For potential rollback
        original_batch_qty: batchQty,
        original_total_stock: totalStock
    };
}

/**
 * Rollback item deduction (for error recovery)
 */
async function rollbackItemDeduction(itemResult) {
    try {
        const { item_ref, original_batch_qty, original_total_stock, batch_id, base_units_deducted } = itemResult;
        
        const itemDoc = await getDoc(item_ref);
        if (!itemDoc.exists()) return;
        
        const itemData = itemDoc.data();
        const batches = itemData.batches || [];
        const batchIndex = batches.findIndex(b => b.id === batch_id);
        
        if (batchIndex !== -1) {
            const updatedBatches = [...batches];
            updatedBatches[batchIndex] = {
                ...updatedBatches[batchIndex],
                quantity: original_batch_qty
            };
            
            await updateDoc(item_ref, {
                batches: updatedBatches,
                stock: original_total_stock,
                lastStockUpdate: serverTimestamp()
            });
            
            itemResult.rolled_back = true;
            debugLog(`🔄 Rolled back ${base_units_deducted} units for item`);
        }
    } catch (error) {
        console.error('Rollback error:', error);
    }
}

/**
 * Create sale record document
 */
async function createSaleRecord(shop_id, seller, items, updatedItems) {
    const saleId = generateTransactionId();
    const totalAmount = updatedItems.reduce((sum, item) => sum + item.total_price, 0);
    
    const saleRecord = {
        id: saleId,
        shop_id,
        seller,
        items: items.map(item => ({
            ...item,
            processed_at: new Date().toISOString()
        })),
        processed_items: updatedItems,
        total_amount: totalAmount,
        timestamp: new Date().toISOString(),
        created_at: serverTimestamp(),
        status: 'completed',
        payment_method: 'cash', // Default, can be extended
        transaction_count: updatedItems.length
    };
    
    // Save to Firestore in a sales collection
    const saleRef = doc(db, "Shops", shop_id, "sales", saleId);
    
    try {
        // Using setDoc instead of updateDoc since it's a new document
        const { setDoc } = await import("https://www.gstatic.com/firebasejs/9.23.0/firebase-firestore.js");
        await setDoc(saleRef, saleRecord);
        debugLog('📝 Sale record created:', saleId);
    } catch (error) {
        console.warn('Could not save sale record:', error);
        // Sale still successful, just record creation failed
    }
    
    return saleRecord;
}

// ====================================================
// INTEGRATION WITH EXISTING CART SYSTEM
// ====================================================

/**
 * Enhanced completeSale function that replaces the old backend call
 */
async function completeSmartSale(paymentDetails = {}) {
    debugLog('🛒 SMART SALE COMPLETION STARTED');
    
    // Get cart from localStorage
    const cart = JSON.parse(localStorage.getItem('smart_sales_cart') || '[]');
    const auth = JSON.parse(localStorage.getItem('firebase:authUser:AIzaSyA7Xr1qT5AKbWlS3xW5Kl1vAKrHrHq9kNY:[DEFAULT]') || '{}');
    
    if (cart.length === 0) {
        throw new Error("Cart is empty!");
    }
    
    if (!auth.uid) {
        throw new Error("Please login first");
    }
    
    const shop_id = auth.uid; // Using user ID as shop ID (adjust as needed)
    const seller = {
        type: localStorage.getItem("sessionType") || "owner",
        authUid: auth.uid,
        name: auth.displayName || "",
        email: auth.email || ""
    };
    
    // Transform cart items to sale format
    const saleItems = cart.map(item => ({
        item_id: item.item_id,
        main_item_id: item.main_item_id || item.item_id,
        category_id: item.category_id || 'unknown',
        name: item.name,
        display_name: item.display_name || item.name,
        type: item.type || "main_item",
        quantity: item.quantity,
        price: item.price || item.sellPrice || item.sell_price || 0,
        sellPrice: item.price || item.sellPrice || item.sell_price || 0,
        batch_id: item.batch_id,
        batchId: item.batch_id,
        batch_remaining: item.batch_remaining || 0,
        can_fulfill: item.can_fulfill !== undefined ? item.can_fulfill : true,
        batch_switch_required: item.batch_switch_required || false,
        real_available: item.real_available,
        sell_unit_id: item.sell_unit_id,
        conversion_factor: item.conversion_factor || 1,
        unit: item.type === 'selling_unit' ? (item.display_name || 'unit') : 'unit'
    }));
    
    const saleData = {
        shop_id,
        seller,
        items: saleItems,
        payment: paymentDetails,
        cart_id: localStorage.getItem('current_cart_id')
    };
    
    debugLog('Sending to frontend processor:', saleData);
    
    // Process sale using frontend logic
    const result = await completeSaleFrontend(saleData);
    
    if (result.success) {
        // Clear cart on success
        localStorage.removeItem('smart_sales_cart');
        localStorage.removeItem('current_cart_id');
        
        // Show success message
        showNotification('✅ Sale completed successfully!', 'success', 5000);
        
        // Trigger cart update if cartIcon exists
        if (window.cartIcon && window.cartIcon.updateIcon) {
            window.cartIcon.updateIcon();
        }
    } else {
        throw new Error(result.error || 'Sale failed');
    }
    
    return result;
}

// ====================================================
// NOTIFICATION HELPER (from cart-icon.js)
// ====================================================

function showNotification(message, type = 'info', duration = 3000) {
    // Reuse the notification system from cart-icon.js or create simple one
    if (window.showNotification) {
        window.showNotification(message, type, duration);
        return;
    }
    
    // Simple fallback notification
    const existing = document.getElementById('sale-notification');
    if (existing) existing.remove();
    
    const colors = {
        info: { bg: '#3498db', icon: 'ℹ️' },
        success: { bg: '#2ecc71', icon: '✅' },
        error: { bg: '#e74c3c', icon: '❌' }
    };
    
    const config = colors[type] || colors.info;
    
    const notification = document.createElement('div');
    notification.id = 'sale-notification';
    notification.style.cssText = `
        position: fixed;
        top: 80px;
        right: 20px;
        background: ${config.bg};
        color: white;
        padding: 12px 20px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        z-index: 10001;
        display: flex;
        align-items: center;
        gap: 10px;
        max-width: 400px;
        animation: slideIn 0.3s ease;
    `;
    
    notification.innerHTML = `
        <span style="font-size: 18px;">${config.icon}</span>
        <span style="font-size: 14px; font-weight: 500;">${message}</span>
    `;
    
    document.body.appendChild(notification);
    
    // Add animation styles if needed
    if (!document.querySelector('#sale-notification-styles')) {
        const style = document.createElement('style');
        style.id = 'sale-notification-styles';
        style.textContent = `
            @keyframes slideIn {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slideOut {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(100%); opacity: 0; }
            }
        `;
        document.head.appendChild(style);
    }
    
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, duration);
}

// ====================================================
// EXPORT AND INITIALIZATION
// ====================================================

// Expose functions globally
window.saleProcessor = {
    completeSale: completeSmartSale,
    completeSaleFrontend: completeSaleFrontend,
    processSaleItem: processSaleItem,
    rollbackItemDeduction: rollbackItemDeduction,
    createSaleRecord: createSaleRecord,
    debug: debugLog
};

// Auto-initialize
document.addEventListener("DOMContentLoaded", () => {
    debugLog('🎯 Frontend Sale Processor Ready!');
    console.log(`
╔═══════════════════════════════════════════╗
║     🧠 FRONTEND SALE PROCESSOR READY     ║
╠═══════════════════════════════════════════╣
║ • Replaces Flask /complete-sale route    ║
║ • Smart batch tracking                   ║
║ • Real-time stock updates                ║
║ • Error recovery & rollback              ║
║ • Sale record creation                   ║
╚═══════════════════════════════════════════╝
`);
});

// Export for module usage
export { 
    completeSmartSale, 
    completeSaleFrontend, 
    processSaleItem,
    rollbackItemDeduction,
    createSaleRecord 
};