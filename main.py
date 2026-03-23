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
from ai_agent import get_ai_recommendation
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
    version="3.5.0",
    lifespan=lifespan
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
# ROUTER 1: CUSTOMER FRONTEND (MINI APP / WEB CUSTOMER)
# ==============================================================================
@app.get("/", response_class=HTMLResponse, tags=["Web Customer"])
async def read_root(request: Request):
    # Default setting kalau database lagi ngadat
    settings_data = {
        "store_name": "BABA Parfume", 
        "admin_whatsapp": "", 
        "checkout_message": "Halo BABA Parfume, saya mau pesan..."
    }
    produk_aktif = []

    if supabase:
        try:
            # 1. Tarik Info Toko (Nama, WA Admin) dari tabel store_settings
            res_set = supabase.table("store_settings").select("*").eq("id", 1).single().execute()
            if res_set.data: 
                settings_data = res_set.data
            
            # 2. Tarik Katalog Produk (Cuma nampilin yang is_active = True aja)
            res_prod = supabase.table("products").select("*").eq("is_active", True).order("id").execute()
            
            # Bersihin datanya pake fungsi normalize_product yang udah kita bikin di atas
            produk_aktif = [normalize_product(p) for p in (res_prod.data or [])]
            
        except Exception as e:
            print(f"❌ [ERROR LOAD FRONTEND CUSTOMER]: {e}")

    # 3. Lempar semua data mateng ke file index.html
    return templates.TemplateResponse(request=request, name="customer/index.html", context={
        "request": request, 
        "settings": settings_data, 
        "produk": produk_aktif
    })

@app.post("/api/v1/checkout", tags=["API External"])
async def api_process_checkout(request: Request):
    """Jalur API Gacor untuk nangkep pesanan dari Mini App"""
    try:
        data = await request.json()
        if data.get("action") != "checkout":
            return JSONResponse(status_code=400, content={"error": "Aksi tidak valid"})

        cust_info = data.get("customer", {})
        items = data.get("items", [])
        total_amount = data.get("total_amount", 0)
        payment_method = data.get("payment_method", "Tidak Diketahui")
        address = cust_info.get("address", "")
        tele_id = cust_info.get("id")

        # Generate Nomor Resi
        order_number = f"ORD-{datetime.now().strftime('%y%m%d')}-{str(tele_id)[-4:]}"

        if supabase:
            # 1. Update alamat & nama pelanggan di database
            supabase.table("customers").update({
                "default_address": address,
                "full_name": cust_info.get('full_name')
            }).eq("telegram_id", tele_id).execute()

            # 2. Ambil ID (UUID) pelanggan
            cust_db = supabase.table("customers").select("id").eq("telegram_id", tele_id).single().execute()
            cust_uuid = cust_db.data.get("id")

            # 3. Simpan ke tabel Orders
            order_payload = {
                "order_number": order_number,
                "customer_id": cust_uuid,
                "shipping_address": address,
                "total_amount": total_amount,
                "status": "Menunggu Pembayaran",
                "order_source": "Telegram Mini App",
                "payment_method": payment_method
            }
            order_res = supabase.table("orders").insert(order_payload).execute()
            order_uuid = order_res.data[0].get("id")

            # 4. Simpan rincian barang (Items) & Potong Stok Realtime
            for item in items:
                supabase.table("order_items").insert({
                    "order_id": order_uuid,
                    "product_id": item['id'],
                    "quantity": item['qty'],
                    "price_at_time": item['price']
                }).execute()

                # Potong Stok
                prod_data = supabase.table("products").select("stock_quantity").eq("id", item['id']).single().execute()
                new_stock = max(0, prod_data.data.get("stock_quantity", 0) - item['qty'])
                supabase.table("products").update({"stock_quantity": new_stock}).eq("id", item['id']).execute()

        # 5. BLASTER RESI & NOTIFIKASI KE TELEGRAM (Customer & Admin)
        if BOT_AVAILABLE:
            from bot import bot as bot_instance
            import asyncio
            import os
            
            # Resi buat pembeli
            struk_belanja = (
                f"✅ <b>YAY! PESANAN BERHASIL DIBUAT!</b>\n\n"
                f"Terima kasih kak <b>{cust_info.get('full_name')}</b>!\n"
                f"Nomor Pesanan: <code>{order_number}</code>\n"
                f"Total Tagihan: <b>${total_amount:.2f}</b>\n"
                f"Metode Bayar: <b>{payment_method}</b>\n\n"
                f"<i>Silakan tunggu sebentar ya, tim Admin BABA akan segera menghubungi kakak.</i> 🚀"
            )
            # Jalankan di background biar web loadingnya cepet
            asyncio.create_task(bot_instance.send_message(chat_id=tele_id, text=struk_belanja, parse_mode="HTML"))
            
            # Alarm buat bos (Lu)
            ADMIN_ID = os.getenv("ADMIN_ID")
            if ADMIN_ID:
                alert_admin = (
                    f"🚨 <b>BOS ADA ORDERAN BARU MASUK!</b> 🚨\n\n"
                    f"Dari: {cust_info.get('full_name')} (@{cust_info.get('username')})\n"
                    f"Nilai Order: ${total_amount:.2f}\n"
                    f"Alamat: {address}\n\n"
                    f"Cek Dashboard Web sekarang buat diproses!"
                )
                asyncio.create_task(bot_instance.send_message(chat_id=ADMIN_ID, text=alert_admin, parse_mode="HTML"))

        return {"status": "success", "order_number": order_number}

    except Exception as e:
        print(f"❌ [API CHECKOUT ERROR]: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
        
# ==============================================================================
# ROUTER 2: ADMIN DASHBOARD (ANALYTICS ENGINE)
# ==============================================================================
@app.get("/admin", response_class=HTMLResponse, tags=["Admin Core"])
async def admin_dashboard(request: Request):
    # Bikin blueprint kerangka datanya
    metrics = {
        "total_revenue": 0.0, "revenue_growth": 12.5, # Growth di-mock 12.5% dulu
        "total_orders": 0, "completed_orders": 0,
        "total_customers": 0, "new_customers": 0,
        "low_stock_count": 0,
        "cat_man": 0, "cat_woman": 0, "cat_netral": 0
    }
    recent_orders = []
    top_products = []
    
    if supabase:
        try:
            # 1. Tarik Data Database
            res_produk = supabase.table("products").select("*").execute()
            res_orders = supabase.table("orders").select("*, customers(full_name)").order("created_at", desc=True).execute()
            res_cust = supabase.table("customers").select("id, created_at").execute()

            produk_data = res_produk.data or []
            orders_data = res_orders.data or []
            cust_data = res_cust.data or []

            # 2. Kalkulasi Metrik Produk (Stok & Kategori)
            for p in produk_data:
                tags = [t.upper() for t in safe_array(p.get("tags"))]
                stok = int(p.get("stock_quantity", 0))
                
                # Alarm Varian Habis
                if stok <= 5: 
                    metrics["low_stock_count"] += 1

                # Hitung Distribusi Kategori
                if "MAN" in tags and "WOMAN" not in tags:
                    metrics["cat_man"] += stok
                elif "WOMAN" in tags:
                    metrics["cat_woman"] += stok
                elif "NETRAL" in tags or "UNISEX" in tags:
                    metrics["cat_netral"] += stok

            # 3. Kalkulasi Metrik Order & Omset
            metrics["total_orders"] = len(orders_data)
            for o in orders_data:
                if o.get("status") == "Selesai":
                    metrics["completed_orders"] += 1
                    metrics["total_revenue"] += float(o.get("total_amount", 0))

            # 4. Kalkulasi Customer Baru (Bulan Ini)
            metrics["total_customers"] = len(cust_data)
            current_month = datetime.now().month
            # Ngitung berapa orang yg join bulan ini
            new_cust = [c for c in cust_data if datetime.fromisoformat(c['created_at'].replace('Z', '+00:00')).month == current_month]
            metrics["new_customers"] = len(new_cust)

            # 5. Data Tambahan buat List
            recent_orders = orders_data[:3] # Ambil 3 order terbaru aja buat di halaman depan
            
            # Simulasi Top Products (Diambil dari barang yang stoknya paling laku/dikit)
            top_products = sorted(produk_data, key=lambda x: x.get('stock_quantity', 0))[:3]

        except Exception as e:
            print(f"❌ [ERROR DASHBOARD]: {e}")

    # Lempar ke dashboard.html yang baru!
    return templates.TemplateResponse(request=request, name="admin/dashboard.html", context={
        "request": request, 
        "metrics": metrics, 
        "recent_orders": recent_orders,
        "top_products": top_products,
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
async def edit_product(
    pid: int, 
    name: str = Form(...), 
    stock_quantity: int = Form(...), 
    discounted_price: float = Form(...),
    # 3 Variabel baru buat nangkep logic dari HTML:
    stock_action: str = Form("tetap"), 
    adj_amount: int = Form(0), 
    stock_reason: str = Form("")
):
    try:
        # 1. Update data utama ke tabel products (Nama, Harga, Sisa Stok)
        supabase.table("products").update({
            "name": name, 
            "stock_quantity": stock_quantity, 
            "discounted_price": discounted_price
        }).eq("id", pid).execute()

        # 2. Catat Sejarah (Audit Log) kalau ada aksi tambah/kurang
        if stock_action in ['tambah', 'kurang'] and adj_amount > 0:
            log_payload = {
                "product_id": pid,
                "action": stock_action,
                "adjustment_amount": adj_amount,
                "final_stock": stock_quantity,
                "reason": stock_reason if stock_action == 'kurang' else "Restock persediaan"
            }
            # Simpan ke tabel stock_logs di Supabase
            supabase.table("stock_logs").insert(log_payload).execute()
            print(f"✅ [AUDIT LOG] Berhasil {stock_action} {adj_amount} pcs untuk parfum ID {pid}")

        return RedirectResponse(url="/admin/stock", status_code=status.HTTP_303_SEE_OTHER)
    
    except Exception as e:
        print(f"❌ [ERROR EDIT STOK]: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/stock/delete/{pid}", tags=["Admin Inventory"])
async def delete_product(pid: int):
    try:
        supabase.table("products").delete().eq("id", pid).execute()
        return RedirectResponse(url="/admin/stock", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ==============================================================================
# ROUTER 4: PESANAN & PELANGGAN (CRM ENTERPRISE)
# ==============================================================================

@app.get("/admin/orders", response_class=HTMLResponse, tags=["Admin CRM"])
async def admin_orders(request: Request):
    pesanan = []
    if supabase:
        try:
            # Tarik data order + info customer + rincian barang yang dibeli (order_items & products)
            res = supabase.table("orders").select(
                "*, customers(full_name, phone, username, default_address, telegram_id), order_items(*, products(name, image_url))"
            ).order("created_at", desc=True).execute()
            pesanan = res.data or []
        except Exception as e:
            print(f"❌ [ERROR PESANAN]: {e}")
            
    return templates.TemplateResponse("admin/orders.html", {
        "request": request, 
        "pesanan": pesanan, 
        "pending_count": get_pending_count()
    })

@app.post("/admin/update-order-status", tags=["Admin CRM"])
async def update_order_status(order_id: str = Form(...), status_order: str = Form(..., alias="status")):
    try:
        # 1. Update status di database
        supabase.table("orders").update({"status": status_order}).eq("id", order_id).execute()
        
        # 2. FITUR AUTO-NOTIFIKASI KE TELEGRAM PELANGGAN
        if BOT_AVAILABLE:
            try:
                # Cari tau ID Telegram pelanggannya dari order ini
                res_order = supabase.table("orders").select("order_number, customers(telegram_id, full_name)").eq("id", order_id).single().execute()
                if res_order.data and res_order.data.get("customers"):
                    tele_id = res_order.data["customers"]["telegram_id"]
                    cust_name = res_order.data["customers"]["full_name"]
                    no_order = res_order.data["order_number"]
                    
                    # Rangkai pesan otomatis
                    pesan_notif = (
                        f"🔔 <b>UPDATE PESANAN BABA PARFUME</b>\n\n"
                        f"Halo kak <b>{cust_name}</b>!\n"
                        f"Status pesanan kamu (<code>{no_order}</code>) sekarang berubah menjadi:\n"
                        f"👉 <b>{status_order.upper()}</b>\n\n"
                        f"<i>Terima kasih sudah berbelanja di BABA Parfume! ✨</i>"
                    )
                    
                    # Kirim lewat bot
                    from bot import bot as bot_instance
                    import asyncio
                    asyncio.create_task(bot_instance.send_message(chat_id=tele_id, text=pesan_notif))
                    print(f"✅ [NOTIF BOT] Berhasil kirim update status ke {cust_name}")
            except Exception as e:
                print(f"⚠️ [NOTIF BOT ERROR] Gagal kirim notif telegram: {e}")

        return RedirectResponse(url="/admin/orders", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        print(f"❌ [ERROR UPDATE STATUS]: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/customers", response_class=HTMLResponse, tags=["Admin CRM"])
async def admin_customers(request: Request):
    pelanggan = []
    if supabase:
        try:
            # 1. Ambil data mentah pelanggan
            res_cust = supabase.table("customers").select("*").order("created_at", desc=True).execute()
            pelanggan = res_cust.data or []
            
            # 2. Ambil data orderan yang valid (bukan yang cuma iseng klik/belum bayar) buat ngitung omset
            res_orders = supabase.table("orders").select("customer_id, total_amount").neq("status", "Menunggu Pembayaran").execute()
            orders_data = res_orders.data or []

            # 3. Aggregasi (Menyatukan data order ke masing-masing pelanggan)
            for c in pelanggan:
                c_orders = [o for o in orders_data if o['customer_id'] == c['id']]
                c['calc_total_orders'] = len(c_orders)
                c['calc_total_spent'] = sum(float(o['total_amount']) for o in c_orders)
                
        except Exception as e:
            print(f"❌ [ERROR PELANGGAN]: {e}")
            
    return templates.TemplateResponse("admin/customers.html", {
        "request": request, 
        "pelanggan": pelanggan, 
        "pending_count": get_pending_count()
    })

@app.post("/admin/customers/edit/{cid}", tags=["Admin CRM"])
async def edit_customer(
    cid: str, 
    full_name: str = Form(...), 
    phone: str = Form(""), 
    default_address: str = Form("")
):
    """Rute untuk menerima update data dari pop-up modal di HTML Customers"""
    try:
        supabase.table("customers").update({
            "full_name": full_name,
            "phone": phone,
            "default_address": default_address
        }).eq("id", cid).execute()
        
        return RedirectResponse(url="/admin/customers", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        print(f"❌ [ERROR EDIT PELANGGAN]: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
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
# ROUTER: CUSTOMER AI AGENT (GEMINI ENGINE)
# ==============================================================================

@app.get("/cs", response_class=HTMLResponse, tags=["Web Customer"])
async def chat_ai_page(request: Request):
    """Nampilin halaman chat AI buat pelanggan"""
    return templates.TemplateResponse(request=request, name="customer/cs.html", context={"request": request})

@app.get("/api/v1/chat/history")
async def get_chat_history(tele_id: int):
    """Narik riwayat chat aktif biar AI punya ingatan"""
    try:
        # Cari ID sesi yang aktif (is_active = True)
        res_sess = supabase.table("ai_chat_sessions").select("id").eq("telegram_id", tele_id).eq("is_active", True).execute()
        if not res_sess.data:
            return {"status": "success", "history": []}
            
        sid = res_sess.data[0]['id']
        res_msg = supabase.table("ai_chat_messages").select("role, content").eq("session_id", sid).order("created_at", desc=False).execute()
        return {"status": "success", "history": res_msg.data or []}
    except:
        return {"status": "success", "history": []}

@app.post("/api/v1/chat/send")
async def chat_ai_send(request: Request):
    data = await request.json()
    tele_id = data.get("tele_id")
    user_msg = data.get("message")
    
    # VALIDASI KRUSIAL: Cek apakah tele_id beneran ada angkanya
    if not tele_id or str(tele_id).strip() == "":
        return JSONResponse(
            status_code=400, 
            content={"status": "error", "message": "ID Telegram tidak valid (kosong)"}
        )
    
    # Lanjut panggil ai_agent
    ai_reply = await get_ai_recommendation(int(tele_id), user_msg)
    return {"status": "success", "reply": ai_reply}

@app.post("/api/v1/chat/reset")
async def chat_reset(request: Request):
    """Ngereset sesi chatan (user klik tombol 'Akhiri')"""
    data = await request.json()
    tele_id = data.get("tele_id")
    try:
        supabase.table("ai_chat_sessions").update({"is_active": False}).eq("telegram_id", tele_id).execute()
        return {"status": "success"}
    except:
        return {"status": "error"}

# ==============================================================================
# ROUTER: ADMIN CS PANEL (INTERCEPT MODE)
# ==============================================================================

@app.get("/admin/cs", response_class=HTMLResponse, tags=["Admin CRM"])
async def admin_cs_panel(request: Request):
    """Halaman Dashboard CS buat lu mantau AI"""
    return templates.TemplateResponse(request=request, name="admin/cs_management.html", context={
        "request": request, 
        "pending_count": get_pending_count()
    })

@app.get("/api/v1/admin/cs/sessions")
async def api_admin_get_sessions():
    """Admin narik daftar semua orang yang lagi chatan"""
    try:
        # Join ke tabel customers biar dapet nama asli si user
        res = supabase.table("ai_chat_sessions").select("*, customers(full_name, username)").order("created_at", desc=True).execute()
        return {"status": "success", "sessions": res.data or []}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/v1/admin/cs/messages")
async def api_admin_get_messages(session_id: int):
    """Admin ngintip isi percakapan per orang"""
    try:
        res = supabase.table("ai_chat_messages").select("*").eq("session_id", session_id).order("created_at", desc=False).execute()
        return {"status": "success", "messages": res.data or []}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/v1/admin/cs/send-manual")
async def api_admin_send_manual(request: Request):
    """Fitur Dewa: Lu bales chatan secara manual via Bot"""
    data = await request.json()
    sid = data.get("session_id")
    tele_id = data.get("tele_id")
    msg_text = data.get("message")

    try:
        # 1. Catat ke DB sebagai 'admin' biar ada history-nya
        supabase.table("ai_chat_messages").insert({
            "session_id": sid, "role": "admin", "content": msg_text
        }).execute()

        # 2. Kirim beneran ke Telegram si user
        if BOT_AVAILABLE:
            from bot import bot as bot_instance
            # Kirim pake label Admin biar usernya tau itu lu yang bales
            await bot_instance.send_message(chat_id=tele_id, text=f"👨‍💻 <b>Admin BABA:</b>\n{msg_text}", parse_mode="HTML")
        
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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
    # AMBIL PORT DARI RENDER
    port = int(os.environ.get("PORT", 8000))
    # JALANKAN DENGAN HOST 0.0.0.0
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
