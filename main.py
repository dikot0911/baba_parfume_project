import os
import sys
from typing import Any, List, Optional
from datetime import datetime

from fastapi import FastAPI, Request, Form, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import asyncio

# ==============================================================================
# IMPORT BOT MODULE (Sihir Integrasinya di Sini)
# ==============================================================================
try:
    print("🔍 [DEBUG] Mencoba import modul bot...")
    from bot import bot, dp, router as bot_router, alarm_pesanan_pending
    BOT_AVAILABLE = True
    print("✅ [DEBUG] Modul bot berhasil di-import!")
except Exception as e:
    print(f"❌ [DEBUG] Gagal import bot.py: {e}")
    BOT_AVAILABLE = False

# 2. DEFINISI LIFESPAN (JANTUNG INTEGRASI)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- PROSES STARTUP ---
    print("🚀 [LIFESPAN] Server FastAPI Sedang Start...")
    
    bot_task = None
    if BOT_AVAILABLE:
        try:
            print("🤖 [LIFESPAN] Menghidupkan Mesin Bot...")
            dp.include_router(bot_router)
            await bot.delete_webhook(drop_pending_updates=True)
            
            # Jalanin Polling & Alarm di Background
            asyncio.create_task(alarm_pesanan_pending(bot))
            bot_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
            
            print("✅ [LIFESPAN] Bot Telegram Berhasil Standby!")
        except Exception as e:
            print(f"❌ [LIFESPAN] Error pas nyalain bot: {e}")
    else:
        print("⚠️ [LIFESPAN] Bot tidak dinyalakan karena import gagal.")

    yield # Di sini aplikasi Web lu running

    # --- PROSES SHUTDOWN ---
    print("🛑 [LIFESPAN] Mematikan bot...")
    if bot_task:
        bot_task.cancel()
    await bot.session.close()

# 3. INISIALISASI APP (HANYA BOLEH ADA SATU BARIS INI!)
app = FastAPI(
    title="BABA Parfume Enterprise",
    lifespan=lifespan  # <--- WAJIB ADA INI BIAR BOT NYALA
)

# ==============================================================================
# 1. DATABASE CONNECTION & APP INIT
# ==============================================================================
try:
    from database import supabase
    print("✅ [SYSTEM] Database Supabase Connected!")
except ImportError:
    print("❌ [SYSTEM] File database.py tidak ditemukan!")
    supabase = None

app = FastAPI(
    title="BABA Parfume Enterprise Engine",
    description="Backend Monolith Terstruktur",
    version="3.5.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ==============================================================================
# 2. HELPER & JINJA FILTERS (UI ENHANCER)
# ==============================================================================
def format_currency(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except:
        return "$0.00"

def format_datetime(value: str) -> str:
    if not value: return "-"
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return value

templates.env.filters["currency"] = format_currency
templates.env.filters["datetime"] = format_datetime

# Global Helper buat nyalain Badge Merah di Sidebar HTML lu
def get_pending_count() -> int:
    if not supabase: return 0
    try:
        res = supabase.table("orders").select("id").eq("status", "Menunggu Pembayaran").execute()
        return len(res.data or [])
    except:
        return 0

# ==============================================================================
# 3. DATA SANITIZERS (PEMBERSIH DATA)
# ==============================================================================
def to_list(text: str) -> list:
    if not text or text.strip() == "": return []
    return [x.strip() for x in text.split(",") if x.strip()]

def safe_array(value: Any) -> List[str]:
    if isinstance(value, list): return value
    if isinstance(value, str): return to_list(value)
    return []

def normalize_product(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "name": item.get("name") or "Tanpa Nama",
        "tagline": item.get("tagline") or "-",
        "description": item.get("description") or "",
        "image_url": item.get("image_url") or "https://placehold.co/80x80/101010/D4AF37?text=BABA",
        "original_price": float(item.get("original_price") or 0.0),
        "discounted_price": float(item.get("discounted_price") or 0.0),
        "stock_quantity": int(item.get("stock_quantity") or 0),
        "tags": safe_array(item.get("tags")),
        "top_notes": safe_array(item.get("top_notes")),
        "heart_notes": safe_array(item.get("heart_notes")),
        "base_notes": safe_array(item.get("base_notes")),
        "longevity": item.get("longevity") or "-",
        "recommendation": item.get("recommendation") or "-",
        "is_active": bool(item.get("is_active", True))
    }

# ==============================================================================
# ROUTER 1: CUSTOMER FRONTEND
# ==============================================================================
@app.get("/", response_class=HTMLResponse, tags=["Web Customer"])
async def read_root(request: Request):
    return templates.TemplateResponse("customer/index.html", {"request": request})

# ==============================================================================
# ROUTER 2: ADMIN DASHBOARD
# ==============================================================================
@app.get("/admin", response_class=HTMLResponse, tags=["Admin Core"])
async def admin_dashboard(request: Request):
    stats = {
        "revenue": 0, "pending_revenue": 0, "total_products": 0, 
        "total_customers": 0, "total_orders": 0, "stok_kritis": 0,
        "recent_orders": [], "top_products": []
    }
    
    if supabase:
        try:
            res_produk = supabase.table("products").select("*").order("stock_quantity").execute()
            res_orders = supabase.table("orders").select("*, customers(full_name)").order("created_at", desc=True).execute()
            res_cust = supabase.table("customers").select("id").execute()

            produk_data = res_produk.data or []
            orders_data = res_orders.data or []

            stats["revenue"] = sum(o['total_amount'] for o in orders_data if o['status'] == 'Selesai')
            stats["pending_revenue"] = sum(o['total_amount'] for o in orders_data if o['status'] == 'Menunggu Pembayaran')
            stats["total_products"] = len(produk_data)
            stats["total_customers"] = len(res_cust.data or [])
            stats["total_orders"] = len(orders_data)
            stats["stok_kritis"] = len([p for p in produk_data if p.get('stock_quantity', 0) <= 10])
            stats["recent_orders"] = orders_data[:5]
            stats["top_products"] = produk_data[:5] # Mockup terlaris
        except Exception as e:
            print(f"❌ [ERROR DASHBOARD]: {e}")

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, 
        "stats": stats, 
        "pending_count": get_pending_count()
    })

# ==============================================================================
# ROUTER 3: MANAJEMEN STOK (INVENTORY)
# ==============================================================================
@app.get("/admin/stock", response_class=HTMLResponse, tags=["Admin Inventory"])
async def admin_stock(request: Request):
    data_parfum = []
    if supabase:
        try:
            response = supabase.table("products").select("*").order("id").execute()
            data_parfum = [normalize_product(item) for item in (response.data or [])]
        except Exception as e:
            print(f"❌ [ERROR STOK]: {e}")

    return templates.TemplateResponse("admin/stock.html", {
        "request": request, 
        "produk": data_parfum,
        "pending_count": get_pending_count()
    })

@app.post("/admin/add-product", tags=["Admin Inventory"])
async def add_product(
    name: str = Form(...),
    category_id: int = Form(1),
    original_price: float = Form(0.0),
    discounted_price: float = Form(0.0),
    stock_quantity: int = Form(0),
    tags: str = Form(""),
    tagline: str = Form(""),
    description: str = Form(""),
    top_notes: str = Form(""),
    heart_notes: str = Form(""),
    base_notes: str = Form(""),
    longevity: str = Form(""),
    recommendation: str = Form(""),
    image_url: str = Form("")
):
    try:
        data_input = {
            "name": name, "category_id": category_id,
            "original_price": original_price, "discounted_price": discounted_price,
            "stock_quantity": stock_quantity, "tagline": tagline,
            "description": description, "image_url": image_url,
            "longevity": longevity, "recommendation": recommendation,
            "is_active": True,
            "tags": to_list(tags), "top_notes": to_list(top_notes),
            "heart_notes": to_list(heart_notes), "base_notes": to_list(base_notes)
        }
        supabase.table("products").insert(data_input).execute()
        return RedirectResponse(url="/admin/stock", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        print(f"❌ [GAGAL SIMPAN PRODUK]: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/stock/edit/{pid}", tags=["Admin Inventory"])
async def edit_product(pid: int, name: str = Form(...), stock_quantity: int = Form(...), discounted_price: float = Form(...)):
    try:
        supabase.table("products").update({
            "name": name, "stock_quantity": stock_quantity, "discounted_price": discounted_price
        }).eq("id", pid).execute()
        return RedirectResponse(url="/admin/stock", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/stock/delete/{pid}", tags=["Admin Inventory"])
async def delete_product(pid: int):
    try:
        supabase.table("products").delete().eq("id", pid).execute()
        return RedirectResponse(url="/admin/stock", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ==============================================================================
# ROUTER 4: PESANAN & PELANGGAN (CRM)
# ==============================================================================
@app.get("/admin/orders", response_class=HTMLResponse, tags=["Admin CRM"])
async def admin_orders(request: Request):
    pesanan = []
    if supabase:
        try:
            res = supabase.table("orders").select("*, customers(full_name, phone, username, default_address)").order("created_at", desc=True).execute()
            pesanan = res.data or []
        except Exception as e:
            print(f"❌ [ERROR PESANAN]: {e}")
            
    return templates.TemplateResponse("admin/orders.html", {
        "request": request, "pesanan": pesanan, "pending_count": get_pending_count()
    })

@app.post("/admin/update-order-status", tags=["Admin CRM"])
async def update_order_status(order_id: str = Form(...), status_order: str = Form(..., alias="status")):
    try:
        supabase.table("orders").update({"status": status_order}).eq("id", order_id).execute()
        return RedirectResponse(url="/admin/orders", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/customers", response_class=HTMLResponse, tags=["Admin CRM"])
async def admin_customers(request: Request):
    pelanggan = []
    if supabase:
        try:
            res = supabase.table("customers").select("*").order("created_at", desc=True).execute()
            pelanggan = res.data or []
        except Exception as e:
            print(f"❌ [ERROR PELANGGAN]: {e}")
            
    return templates.TemplateResponse("admin/customers.html", {
        "request": request, "pelanggan": pelanggan, "pending_count": get_pending_count()
    })
# ==============================================================================
# ROUTER 5: PENGATURAN (SETTINGS)
# ==============================================================================
@app.get("/admin/settings", response_class=HTMLResponse, tags=["Admin Settings"])
async def admin_settings(request: Request):
    settings_data = {
        "store_name": "BABA Parfume", 
        "admin_whatsapp": "", 
        "checkout_message": "Halo, saya mau pesan...",
        "is_bot_active": True
    }
    if supabase:
        try:
            res = supabase.table("store_settings").select("*").eq("id", 1).single().execute()
            if res.data: settings_data = res.data
        except Exception as e:
            print(f"⚠️ [INFO SETTING]: Belum ada data setting, pake default.")
            
    return templates.TemplateResponse("admin/settings.html", {
        "request": request, "settings": settings_data, "pending_count": get_pending_count()
    })

@app.post("/admin/settings/update", tags=["Admin Settings"])
async def update_settings(
    store_name: str = Form(...),
    admin_whatsapp: str = Form(""),
    checkout_message: str = Form(""),
    is_bot_active: str = Form("false") # Nangkep checkbox dari HTML
):
    try:
        # Konversi string "true"/"on" dari checkbox jadi Boolean
        bot_status = True if is_bot_active.lower() in ['true', 'on', '1'] else False
        
        payload = {
            "store_name": store_name,
            "admin_whatsapp": admin_whatsapp,
            "checkout_message": checkout_message,
            "is_bot_active": bot_status
        }
        supabase.table("store_settings").upsert({**payload, "id": 1}).execute()
        print("✅ [SUKSES] Setting berhasil di-update!")
        return RedirectResponse(url="/admin/settings", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        print(f"❌ [ERROR SETTING]: {e}")
        raise HTTPException(status_code=500, detail="Gagal menyimpan pengaturan.")

# ==============================================================================
# ROUTER 6: API EXTERNAL (BUAT FRONTEND / MINI APP TELEGRAM)
# ==============================================================================
@app.get("/api/v1/products/live", tags=["API External"])
async def api_get_live_products():
    """Jalur pipa khusus biar index.html bisa nyedot data stok realtime"""
    if not supabase:
        return JSONResponse(status_code=500, content={"error": "Database tidak terhubung"})
    try:
        # Tarik semua produk yang statusnya aktif
        res = supabase.table("products").select("*").eq("is_active", True).order("id").execute()
        
        # Pake fungsi pembersih data (normalize_product) biar ga error di frontend
        data_bersih = [normalize_product(p) for p in (res.data or [])]
        
        return {"status": "success", "data": data_bersih}
    except Exception as e:
        print(f"❌ [ERROR API PRODUK]: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    
# ==============================================================================
# EKSEKUSI SERVER
# ==============================================================================
if __name__ == "__main__":
    print("\n" + "=".center(60, "="))
    print("🚀 BABA PARFUME ENTERPRISE ENGINE".center(60))
    print("=".center(60, "="))
    print("🌐 Web Pelanggan   : http://localhost:8000/")
    print("🛠️  Panel Admin     : http://localhost:8000/admin")
    print("=".center(60, "=") + "\n")
    
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
