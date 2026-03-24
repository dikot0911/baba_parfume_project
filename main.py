import os
import sys
import time
import uuid
import logging
import asyncio
from typing import Any, List, Optional, Dict
from datetime import datetime
from dotenv import load_dotenv

# ==============================================================================
# FASTAPI & ENTERPRISE DEPENDENCIES
# ==============================================================================
from fastapi import FastAPI, Request, Form, HTTPException, status, Depends, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field

# Local Modules
from ai_agent import get_ai_recommendation
import uvicorn

# ==============================================================================
# 0. KONFIGURASI LOGGING & ENVIRONMENT (ENTERPRISE STANDARD)
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("baba.enterprise")

load_dotenv()

# Konfigurasi Keamanan Admin
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "baba2026")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "super-secret-baba-token-777")
COOKIE_NAME = "baba_admin_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
ALLOWED_ADMIN_ROLES = {"super_admin", "oprasional", "marketing", "cs", "visitor"}

# ==============================================================================
# 1. IMPORT BOT MODULE (TELEGRAM INTEGRATION)
# ==============================================================================
try:
    from bot import (
        BOT_RUNTIME_AVAILABLE,
        alarm_pesanan_pending,
        bot,
        dp,
        router as bot_router,
    )
    BOT_AVAILABLE = BOT_RUNTIME_AVAILABLE
    logger.info("Modul Telegram Bot berhasil dimuat.")
except Exception as e:
    logger.warning(f"Modul bot tidak aktif atau error: {e}")
    BOT_AVAILABLE = False
    bot = None
    dp = None
    bot_router = None

# ==============================================================================
# 2. DEFINISI LIFESPAN (JANTUNG BACKGROUND PROCESS)
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 [SYSTEM] Server FastAPI BABA Enterprise Sedang Start...")

    bot_task = None
    alarm_task = None
    if BOT_AVAILABLE and bot and dp and bot_router:
        try:
            if bot_router not in dp.sub_routers:
                dp.include_router(bot_router)
            await bot.delete_webhook(drop_pending_updates=True)

            # Jalankan background tasks
            alarm_task = asyncio.create_task(alarm_pesanan_pending(bot))
            bot_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
            logger.info("🤖 [SYSTEM] Bot Telegram Standby & Listening!")
        except Exception as e:
            logger.error(f"❌ [SYSTEM] Bot Telegram gagal dinyalakan: {e}")
    else:
        logger.info("⚠️ [SYSTEM] Bot Telegram tidak aktif; Web app tetap berjalan di mode Standalone.")

    yield # --- APLIKASI WEB BERJALAN DI SINI ---

    logger.info("🛑 [SYSTEM] Mematikan background task & membersihkan memori...")
    for task in (alarm_task, bot_task):
        if task:
            task.cancel()
    if bot:
        await bot.session.close()
    logger.info("✅ [SYSTEM] Shutdown selesai. Good bye!")

# ==============================================================================
# 3. DATABASE CONNECTION (SUPABASE)
# ==============================================================================
try:
    from database import supabase
    if supabase:
        logger.info("✅ [DATABASE] Supabase berhasil terkoneksi.")
    else:
        logger.warning("⚠️ [DATABASE] Objek supabase None. Cek .env URL/Key.")
except ImportError:
    logger.error("❌ [DATABASE] File database.py tidak ditemukan!")
    supabase = None

# ==============================================================================
# 4. INISIALISASI FASTAPI APP & MIDDLEWARE
# ==============================================================================
app = FastAPI(
    title="BABA Parfume Enterprise Engine",
    description="Backend Monolith Terstruktur dengan Keamanan Tingkat Tinggi",
    version="4.0.0-Enterprise",
    lifespan=lifespan,
    docs_url="/api/docs", # API Documentation
    redoc_url=None
)

# Middleware 1: CORS untuk mengizinkan akses dari Mini App / Web Eksternal
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware 2: Request Logger & Timer (Analytics internal)
class RequestTimerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        # Log jika proses memakan waktu lebih dari 1 detik (Indikator lag)
        if process_time > 1.0:
            logger.warning(f"🐢 [PERFORMANCE] {request.method} {request.url.path} memakan waktu {process_time:.2f} detik")
        return response

app.add_middleware(RequestTimerMiddleware)

# Mount Static & Templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ==============================================================================
# 5. DATA VALIDATION SCHEMAS (PYDANTIC) - ANTI HACK & INJECTION
# ==============================================================================
# Ini adalah fitur Enterprise biar data yang dikirim user otomatis divalidasi
class CheckoutCustomer(BaseModel):
    id: int
    username: Optional[str] = ""
    first_name: Optional[str] = ""
    full_name: str
    address: str

class CheckoutItem(BaseModel):
    id: int
    name: str
    qty: int
    price: float

class CheckoutPayload(BaseModel):
    action: str
    customer: CheckoutCustomer
    items: List[CheckoutItem]
    payment_method: str
    total_amount: float

class ChatSendPayload(BaseModel):
    tele_id: int
    message: str

class ChatFeedbackPayload(BaseModel):
    tele_id: int
    rating: int = Field(ge=1, le=5)
    complaint: Optional[str] = ""

class AdminManualChatPayload(BaseModel):
    session_id: int
    tele_id: int
    message: str

class ChatResetPayload(BaseModel):
    tele_id: int

# ==============================================================================
# 6. AUTHENTICATION ENGINE (SISTEM GEMBOK ADMIN & STAFF) 🔐
# ==============================================================================
import hashlib
import base64


def sanitize_admin_role(role: Optional[str]) -> str:
    normalized_role = (role or "").strip().lower()
    return normalized_role if normalized_role in ALLOWED_ADMIN_ROLES else ""


def decode_admin_cookie(token: str) -> tuple[str, str, str]:
    raw_decoded = base64.b64decode(token).decode()
    username, role, name, signature = raw_decoded.split("|")
    expected_sig = hashlib.sha256(f"{username}|{role}|{name}|{SECRET_TOKEN}".encode()).hexdigest()
    if signature != expected_sig:
        raise ValueError("Signature Cookie Dipalsukan!")
    role = sanitize_admin_role(role)
    if not role:
        raise ValueError("Role admin tidak dikenal.")
    return username.strip(), role, name.strip()

def create_secure_cookie(username: str, role: str, name: str) -> str:
    """Bikin tiket cookie yang dienkripsi biar ga bisa dipalsuin hacker"""
    safe_username = username.strip().lower()
    safe_role = sanitize_admin_role(role)
    if not safe_role:
        raise ValueError("Role admin tidak valid.")
    safe_name = name.strip()
    raw_data = f"{safe_username}|{safe_role}|{safe_name}|{SECRET_TOKEN}"
    signature = hashlib.sha256(raw_data.encode()).hexdigest()
    # Gabungin data asli sama tanda tangannya (signature), lalu ubah ke Base64
    cookie_value = base64.b64encode(f"{safe_username}|{safe_role}|{safe_name}|{signature}".encode()).decode()
    return cookie_value

async def verify_admin(request: Request):
    """Dependency: Mengamankan HTML Admin & Ngebaca Jabatan (Role)"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    
    try:
        username, role, name = decode_admin_cookie(token)

        if role == "super_admin":
            if username != ADMIN_USER.strip().lower():
                raise ValueError("Username super admin tidak cocok dengan .env.")
            current_name = "Dewa BABA (Super Admin)"
        else:
            if not supabase:
                raise ValueError("Database admin tidak tersedia.")

            admin_res = (
                supabase.table("admins")
                .select("username, full_name, role")
                .eq("username", username)
                .limit(1)
                .execute()
            )
            if not admin_res.data:
                raise ValueError("Akun admin tidak ditemukan lagi.")

            admin_data = admin_res.data[0]
            db_role = sanitize_admin_role(admin_data.get("role"))
            if db_role != role:
                raise ValueError("Role admin sudah berubah atau tidak valid.")
            current_name = admin_data.get("full_name") or name or username

        # Simpan data profil ke 'state' biar bisa dibaca sama Jinja2 HTML
        request.state.admin_user = username
        request.state.admin_role = role
        request.state.admin_name = current_name
        return True
    except Exception as e:
        logger.warning(f"🔒 [AUTH HACK ATTEMPT] Cookie gak valid: {e}")
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})

async def verify_admin_api(request: Request):
    """Dependency: Mengamankan API Admin"""
    await verify_admin(request) # Pake logika yang sama aja
    return True


def require_admin_roles(*allowed_roles: str):
    normalized_roles = {sanitize_admin_role(role) for role in allowed_roles}
    normalized_roles.discard("")

    async def dependency(request: Request):
        await verify_admin(request)
        current_role = getattr(request.state, "admin_role", "")
        if normalized_roles and current_role not in normalized_roles:
            raise HTTPException(status_code=403, detail="Akses admin ditolak untuk halaman ini.")
        return True

    return Depends(dependency)

# ==============================================================================
# 7. UTILITY & HELPER FUNCTIONS
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


def render_admin_template(request: Request, template_name: str, **context):
    admin_context = {
        "request": request,
        "admin_name": getattr(request.state, "admin_name", "Admin BABA"),
        "admin_role": getattr(request.state, "admin_role", ""),
        "pending_count": get_pending_count(),
    }
    admin_context.update(context)
    return templates.TemplateResponse(request=request, name=template_name, context=admin_context)

def get_pending_count() -> int:
    """Mengambil jumlah order pending untuk notif merah di sidebar admin"""
    if not supabase: return 0
    try:
        res = supabase.table("orders").select("id").eq("status", "Menunggu Pembayaran").execute()
        return len(res.data or [])
    except:
        return 0

def api_success(**payload):
    """Format standar balasan API sukses"""
    return {"status": "success", **payload}

def api_error(message: str, status_code: int = 400, **payload):
    """Format standar balasan API error"""
    return JSONResponse(status_code=status_code, content={"status": "error", "message": message, **payload})

def to_list(text: str) -> list:
    """Membersihkan string jadi list array (Pemisah koma)"""
    if not text or str(text).strip() == "": return []
    return [x.strip() for x in str(text).split(",") if x.strip()]

def safe_array(value: Any) -> List[str]:
    if isinstance(value, list): return value
    if isinstance(value, str): return to_list(value)
    return []

def normalize_product(item: dict) -> dict:
    """Standarisasi format produk dari database untuk dikonsumsi Frontend & AI"""
    return {
        "id": item.get("id"),
        "name": item.get("name") or "Tanpa Nama",
        "tagline": item.get("tagline") or "-",
        "description": item.get("description") or "",
        "image_url": item.get("image_url") or "https://placehold.co/400x500/101010/D4AF37?text=BABA",
        "original_price": float(item.get("original_price") or 0.0),
        "discounted_price": float(item.get("discounted_price") or 0.0),
        "stock_quantity": int(item.get("stock_quantity") or 0),
        "tags": safe_array(item.get("tags")),
        "top_notes": safe_array(item.get("top_notes")),
        "heart_notes": safe_array(item.get("heart_notes")),
        "base_notes": safe_array(item.get("base_notes")),
        "longevity": item.get("longevity") or "8-12 Jam",
        "recommendation": item.get("recommendation") or "All Day",
        "is_active": bool(item.get("is_active", True))
    }

# ==============================================================================
# 8. PYDANTIC SCHEMAS KHUSUS FINANCE & BELANJA
# ==============================================================================
class ManualTransactionPayload(BaseModel):
    account_id: int
    category_id: int
    transaction_type: str
    amount: float = Field(..., gt=0)
    description: str

class PurchaseItemPayload(BaseModel):
    product_id: Optional[int] = None
    item_name: str
    quantity: int = Field(..., gt=0)
    capital_price_per_unit: float = Field(..., ge=0)

class PurchaseOrderPayload(BaseModel):
    account_id: int
    shipping_cost: float = Field(default=0.0)
    notes: str
    items: List[PurchaseItemPayload]
    
class TransferPayload(BaseModel):
    from_account_id: int
    to_account_id: int
    amount_out: float = Field(..., gt=0)
    exchange_rate: float = Field(..., gt=0)
    amount_in: float = Field(..., gt=0)
    description: str

# ==============================================================================
# ROUTER 0: LOGIN & LOGOUT ADMIN (PINTU GERBANG)
# ==============================================================================
@app.get("/admin/login", response_class=HTMLResponse, tags=["Admin Auth"])
async def login_page(request: Request):
    if request.cookies.get(COOKIE_NAME):
        try:
            await verify_admin(request)
            return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
        except HTTPException:
            pass
    return templates.TemplateResponse(request=request, name="admin/login.html")

@app.post("/admin/login", response_class=HTMLResponse, tags=["Admin Auth"])
async def do_login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Memproses Login: Cek Super Admin dulu, kalau bukan baru cari di Database Staff"""
    username = username.strip().lower()
    role = ""
    name = ""
    
    # 1. CEK SUPER ADMIN (Data dari .env)
    if username == ADMIN_USER.strip().lower() and password == ADMIN_PASS:
        role = "super_admin"
        name = "Dewa BABA (Super Admin)"
        logger.info(f"🔓 [AUTH] SUPER ADMIN berhasil login.")
        
    # 2. CEK STAFF BIASA (Data dari Tabel 'admins' di Database)
    else:
        if not supabase:
            return templates.TemplateResponse(request=request, name="admin/login.html", context={"error": "Sistem Database Offline!"})
        
        # Hash password input buat dicocokin sama hash di database
        hashed_pw = hashlib.sha256(password.encode()).hexdigest()
        res = (
            supabase.table("admins")
            .select("*")
            .eq("username", username)
            .eq("password_hash", hashed_pw)
            .limit(1)
            .execute()
        )
        
        if not res.data:
            logger.warning(f"🚨 [AUTH] Gagal login username: {username}")
            return templates.TemplateResponse(request=request, name="admin/login.html", context={"error": "Username atau Password salah bre!"})
        
        staff_data = res.data[0]
        role = sanitize_admin_role(staff_data.get('role'))
        if not role or role == "super_admin":
            logger.warning(f"🚨 [AUTH] Role staff tidak valid buat username: {username}")
            return templates.TemplateResponse(request=request, name="admin/login.html", context={"error": "Role admin tidak valid."})
        name = staff_data['full_name']
        logger.info(f"🔓 [AUTH] STAFF '{name}' ({role}) berhasil login.")

    # 3. TERBITKAN TIKET COOKIE AMAN
    cookie_val = create_secure_cookie(username, role, name)
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_val,
        httponly=True,
        max_age=43200,
        secure=COOKIE_SECURE,
        samesite="lax"
    ) # 12 Jam
    return response

@app.get("/admin/logout", tags=["Admin Auth"])
async def do_logout():
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response

# ==============================================================================
# ROUTER 1: CUSTOMER FRONTEND (MINI APP & EXTERNAL API)
# ==============================================================================
@app.get("/profile", response_class=HTMLResponse, tags=["Web Customer"])
async def customer_profile_page(request: Request, tele_id: Optional[int] = None):
    """
    Halaman Profil Customer: Menampilkan statistik koleksi botol & profil aroma.
    Tanpa nominal uang, fokus ke kebanggaan koleksi (Gamifikasi).
    """
    if not supabase:
        raise HTTPException(status_code=503, detail="Database sedang offline bre!")

    # 1. Default Data (Jika user belum terdaftar atau tele_id ga ada)
    cust_data = None
    stats = {
        "total_orders": 0,
        "total_bottles": 0,
        "favorite_tags": []
    }
    history_orders = []

    if tele_id:
        try:
            # 2. Ambil Data Dasar Customer
            res_cust = supabase.table("customers").select("*").eq("telegram_id", tele_id).single().execute()
            
            if res_cust.data:
                cust_data = res_cust.data
                cust_uuid = cust_data.get("id")

                # 3. Tarik Riwayat Pesanan (Urutkan dari yang terbaru)
                # Kita cuma butuh ID, Nomor, Status, dan Tanggal. Duitnya kita cuekin!
                res_orders = supabase.table("orders").select(
                    "id, order_number, status, created_at"
                ).eq("customer_id", cust_uuid).order("created_at", desc=True).execute()
                
                raw_orders = res_orders.data or []
                stats["total_orders"] = len(raw_orders)

                if raw_orders:
                    # Ambil semua order_id buat narik detail item sekaligus (Bulk Select)
                    order_ids = [o["id"] for o in raw_orders]
                    
                    # 4. Tarik Detail Item & Join ke Produk buat ambil Tags (buat AI Profiling)
                    res_items = supabase.table("order_items").select(
                        "order_id, quantity, products(name, image_url, tags)"
                    ).in_("order_id", order_ids).execute()
                    
                    all_items = res_items.data or []
                    
                    # 5. Logic God Mode: Hitung Total Botol & Analisis Aroma Favorit
                    tag_counter = {}
                    
                    for order in raw_orders:
                        order["items"] = []
                        for item in all_items:
                            if item["order_id"] == order["id"]:
                                qty = item["quantity"]
                                prod = item.get("products") or {}
                                
                                # Tambahin rincian barang ke list pesanan
                                order["items"].append({
                                    "name": prod.get("name", "Varian BABA"),
                                    "image_url": prod.get("image_url", ""),
                                    "qty": qty
                                })
                                
                                # Update Statistik Koleksi (Pride Meter)
                                stats["total_bottles"] += qty
                                
                                # Scan Tags buat pembelajaran AI / Profil Selera
                                p_tags = safe_array(prod.get("tags"))
                                for tag in p_tags:
                                    t_up = tag.upper().strip()
                                    tag_counter[t_up] = tag_counter.get(t_up, 0) + qty

                    history_orders = raw_orders
                    
                    # 6. Ambil 3 Aroma Teratas (Signature Style si User)
                    if tag_counter:
                        # Sortir dari yang paling banyak dibeli
                        sorted_tags = sorted(tag_counter.items(), key=lambda x: x[1], reverse=True)
                        stats["favorite_tags"] = [t[0] for t in sorted_tags[:3]]

            logger.info(f"👤 [PROFILE] User ID:{tele_id} mengintip koleksi ({stats['total_bottles']} botol).")

        except Exception as e:
            logger.error(f"❌ [PROFILE FETCH ERROR]: {e}")
            # Kita biarin tetep render pake data default biar gak crash putih layarnya

    return templates.TemplateResponse("customer/profile.html", {
        "request": request,
        "customer": cust_data,
        "stats": stats,
        "orders": history_orders
    })
    
@app.get("/", response_class=HTMLResponse, tags=["Web Customer"])
async def read_root(request: Request):
    """Endpoint Utama: Menampilkan Katalog Belanja ke Customer"""
    settings_data = {
        "store_name": "BABA Parfume", 
        "admin_whatsapp": "", 
        "checkout_message": "Halo BABA Parfume, saya mau pesan..."
    }
    produk_aktif = []

    if supabase:
        try:
            # Mengambil pengaturan toko global
            res_set = supabase.table("store_settings").select("*").eq("id", 1).single().execute()
            if res_set.data: 
                settings_data = res_set.data
            
            # Mengambil produk yang siap jual (is_active = true)
            res_prod = supabase.table("products").select("*").eq("is_active", True).order("id").execute()
            produk_aktif = [normalize_product(p) for p in (res_prod.data or [])]
            
        except Exception as e:
            logger.error(f"❌ [FRONTEND] Gagal meload data awal: {e}")

    return templates.TemplateResponse(request=request, name="customer/index.html", context={
        "request": request, 
        "settings": settings_data, 
        "produk": produk_aktif
    })


@app.post("/api/v1/checkout", tags=["API Customer"])
async def api_process_checkout(payload: CheckoutPayload): # <- Pake Pydantic Model!
    """Jalur API Aman & Divalidasi untuk memproses orderan dari keranjang"""
    try:
        if payload.action != "checkout":
            return api_error("Action payload tidak dikenali", 400)

        tele_id = payload.customer.id
        address = payload.customer.address
        total_amount = payload.total_amount
        payment_method = payload.payment_method
        full_name = payload.customer.full_name

        # 1. Generate Order Number Unik
        order_number = f"ORD-{datetime.now().strftime('%y%m%d')}-{str(tele_id)[-4:]}-{str(uuid.uuid4())[:4].upper()}"

        if supabase:
            # 2. Upsert Customer Data
            supabase.table("customers").upsert({
                "telegram_id": tele_id,
                "full_name": full_name,
                "default_address": address,
                "username": payload.customer.username
            }, on_conflict="telegram_id").execute()

            # 3. Get Customer UUID
            cust_db = supabase.table("customers").select("id").eq("telegram_id", tele_id).single().execute()
            cust_uuid = cust_db.data.get("id")

            # 4. Buat Record Order
            order_res = supabase.table("orders").insert({
                "order_number": order_number,
                "customer_id": cust_uuid,
                "shipping_address": address,
                "total_amount": total_amount,
                "status": "Menunggu Pembayaran",
                "order_source": "Telegram WebApp",
                "payment_method": payment_method
            }).execute()
            
            order_uuid = order_res.data[0].get("id")

            # 5. Proses Items & Potong Stok menggunakan perulangan aman
            for item in payload.items:
                # Simpan Rincian
                supabase.table("order_items").insert({
                    "order_id": order_uuid,
                    "product_id": item.id,
                    "quantity": item.qty,
                    "price_at_time": item.price
                }).execute()

                # Potong Stok Realtime
                prod_data = supabase.table("products").select("stock_quantity").eq("id", item.id).single().execute()
                current_stock = prod_data.data.get("stock_quantity", 0)
                new_stock = max(0, current_stock - item.qty)
                supabase.table("products").update({"stock_quantity": new_stock}).eq("id", item.id).execute()

        # 6. Notifikasi Asinkron ke Bot
        if BOT_AVAILABLE:
            from bot import bot as bot_instance
            
            # Struk ke Pembeli
            struk_belanja = (
                f"✅ <b>YAY! PESANAN BERHASIL DIBUAT!</b>\n\n"
                f"Terima kasih kak <b>{full_name}</b>!\n"
                f"Nomor Pesanan: <code>{order_number}</code>\n"
                f"Total Tagihan: <b>${total_amount:.2f}</b>\n"
                f"Metode Bayar: <b>{payment_method}</b>\n\n"
                f"<i>Silakan tunggu sebentar ya, tim Admin BABA akan segera memproses.</i> 🚀"
            )
            asyncio.create_task(bot_instance.send_message(chat_id=tele_id, text=struk_belanja, parse_mode="HTML"))
            
            # Peringatan ke Admin
            ADMIN_ID = os.getenv("ADMIN_ID")
            if ADMIN_ID:
                alert_admin = (
                    f"🚨 <b>BOS ADA ORDERAN BARU!</b> 🚨\n\n"
                    f"Dari: {full_name}\n"
                    f"Nilai: ${total_amount:.2f} ({payment_method})\n"
                    f"Order ID: <code>{order_number}</code>\n\n"
                    f"Cek Dashboard Web sekarang!"
                )
                asyncio.create_task(bot_instance.send_message(chat_id=ADMIN_ID, text=alert_admin, parse_mode="HTML"))

        logger.info(f"✅ [CHECKOUT] Order {order_number} sukses dibuat oleh ID:{tele_id}")
        return api_success(order_number=order_number)

    except Exception as e:
        logger.error(f"❌ [CHECKOUT ERROR]: {e}")
        return api_error(f"Gagal memproses checkout: {str(e)}", status_code=500)


@app.get("/api/v1/products/live", tags=["API Customer"])
async def api_get_live_products():
    """API Backend untuk menyuplai data katalog terkini ke HTML SPA"""
    if not supabase:
        return api_error("Database offline", status_code=503)
    try:
        res = supabase.table("products").select("*").eq("is_active", True).order("id").execute()
        data_bersih = [normalize_product(p) for p in (res.data or [])]
        return api_success(data=data_bersih)
    except Exception as e:
        logger.error(f"❌ [API PRODUCTS ERROR]: {e}")
        return api_error(str(e), status_code=500)


# ==============================================================================
# ROUTER 2: CUSTOMER AI AGENT (CS ENGINE)
# ==============================================================================
@app.get("/cs", response_class=HTMLResponse, tags=["Web Customer"])
async def chat_ai_page(request: Request):
    """Menampilkan antarmuka obrolan Mimin AI"""
    return templates.TemplateResponse(request=request, name="customer/cs.html", context={"request": request})

@app.get("/api/v1/chat/history", tags=["API AI"])
async def get_chat_history(tele_id: int):
    """Memanggil kembali memori percakapan user dari database"""
    try:
        if not supabase: return api_success(history=[])
        res_sess = supabase.table("ai_chat_sessions").select("id").eq("telegram_id", tele_id).eq("is_active", True).execute()
        if not res_sess.data:
            return api_success(history=[])
            
        sid = res_sess.data[0]['id']
        res_msg = supabase.table("ai_chat_messages").select("role, content").eq("session_id", sid).order("created_at", desc=False).execute()
        return api_success(history=res_msg.data or [])
    except Exception as e:
        logger.warning(f"Error memuat history AI: {e}")
        return api_success(history=[])

@app.post("/api/v1/chat/send", tags=["API AI"])
async def chat_ai_send(payload: ChatSendPayload):
    """Menerima pesan dari user, memproses via Google GenAI, dan mengembalikan jawaban"""
    if not payload.message.strip():
        return api_error("Pesan kosong tidak bisa diproses", 400)

    try:
        # get_ai_recommendation dipanggil dari ai_agent.py lu
        ai_reply = await get_ai_recommendation(payload.tele_id, payload.message)
        return api_success(reply=ai_reply)
    except Exception as e:
        logger.error(f"❌ [AI GENERATION ERROR]: {e}")
        return api_error("Sistem AI sedang kelebihan beban", 500)

@app.post("/api/v1/chat/reset", tags=["API AI"])
async def chat_reset(payload: ChatResetPayload):
    """Menonaktifkan memori sesi AI (ketika user klik Mulai Baru)"""
    try:
        if supabase:
            supabase.table("ai_chat_sessions").update({"is_active": False}).eq("telegram_id", payload.tele_id).execute()
        logger.info(f"Sesi chat ID:{payload.tele_id} telah direset.")
        return api_success(message="Sesi berhasil direstart")
    except Exception as e:
        logger.error(f"❌ [AI RESET ERROR]: {e}")
        return api_error("Gagal mereset sesi", status_code=500)

@app.post("/api/v1/chat/feedback", tags=["API AI"])
async def submit_ai_feedback(payload: ChatFeedbackPayload):
    """Menyimpan Rating Bintang dan Keluhan untuk melatih ulang AI"""
    try:
        if supabase:
            supabase.table("ai_feedbacks").insert({
                "telegram_id": payload.tele_id,
                "rating": payload.rating,
                "complaint": payload.complaint
            }).execute()
            logger.info(f"🌟 [AI FEEDBACK] ID:{payload.tele_id} memberi Bintang {payload.rating}")
        return api_success(message="Feedback disimpan!")
    except Exception as e:
        logger.error(f"❌ [AI FEEDBACK ERROR]: {e}")
        return api_error(str(e), status_code=500)


# ==============================================================================
# ROUTER 3: ADMIN DASHBOARD & ANALYTICS (TERGEMBOK 🔐)
# ==============================================================================
@app.get("/admin", response_class=HTMLResponse, tags=["Admin Core"], dependencies=[Depends(verify_admin)])
async def admin_dashboard(request: Request):
    """Pusat Komando: Kalkulasi metrik omset, jumlah order, dan pelanggan"""
    metrics = {
        "total_revenue": 0.0, "revenue_growth": 0.0, 
        "total_orders": 0, "completed_orders": 0,
        "total_customers": 0, "new_customers": 0,
        "low_stock_count": 0,
        "cat_man": 0, "cat_woman": 0, "cat_netral": 0
    }
    recent_orders = []
    top_products = []
    
    if supabase:
        try:
            # Ambil Raw Data
            res_produk = supabase.table("products").select("*").execute()
            res_orders = supabase.table("orders").select("*, customers(full_name)").order("created_at", desc=True).execute()
            res_cust = supabase.table("customers").select("id, created_at").execute()

            produk_data = res_produk.data or []
            orders_data = res_orders.data or []
            cust_data = res_cust.data or []

            # 1. Analisis Inventaris (Kategori & Alert Stok)
            for p in produk_data:
                tags = [t.upper() for t in safe_array(p.get("tags"))]
                stok = int(p.get("stock_quantity", 0))
                
                if stok <= 5 and p.get("is_active", True): 
                    metrics["low_stock_count"] += 1

                if "MAN" in tags and "WOMAN" not in tags: metrics["cat_man"] += stok
                elif "WOMAN" in tags: metrics["cat_woman"] += stok
                elif "NETRAL" in tags or "UNISEX" in tags: metrics["cat_netral"] += stok

            # 2. Analisis Finansial
            metrics["total_orders"] = len(orders_data)
            for o in orders_data:
                if o.get("status") in ["Selesai", "Dikirim", "Diproses"]: # Hitung omset dari yang udah jalan
                    metrics["completed_orders"] += 1
                    metrics["total_revenue"] += float(o.get("total_amount", 0))

            # 3. Analisis Demografi
            metrics["total_customers"] = len(cust_data)
            current_month = datetime.now().month
            new_cust = [c for c in cust_data if datetime.fromisoformat(c['created_at'].replace('Z', '+00:00')).month == current_month]
            metrics["new_customers"] = len(new_cust)

            # Slicing untuk tampilan UI
            recent_orders = orders_data[:5] # Tampilkan 5 terbaru
            top_products = sorted(produk_data, key=lambda x: x.get('stock_quantity', 0))[:4] # 4 Barang stok menipis

        except Exception as e:
            logger.error(f"❌ [ADMIN DASHBOARD ERROR]: {e}")

    return render_admin_template(
        request,
        "admin/dashboard.html",
        metrics=metrics,
        recent_orders=recent_orders,
        top_products=top_products
    )


# ==============================================================================
# ROUTER 4: MANAJEMEN INVENTARIS STOK (TERGEMBOK 🔐)
# ==============================================================================
@app.get("/admin/stock", response_class=HTMLResponse, tags=["Admin Inventory"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def admin_stock(request: Request):
    """Menampilkan halaman manajer produk"""
    data_parfum = []
    if supabase:
        try:
            response = supabase.table("products").select("*").order("id").execute()
            data_parfum = [normalize_product(item) for item in (response.data or [])]
        except Exception as e:
            logger.error(f"❌ [ADMIN STOCK ERROR]: {e}")

    return render_admin_template(request, "admin/stock.html", produk=data_parfum)

@app.post("/admin/add-product", tags=["Admin Inventory"], dependencies=[require_admin_roles("super_admin", "oprasional")])
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
    """Menambahkan varian parfum baru ke database"""
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
        logger.info(f"📦 [INVENTORY] Produk baru ditambahkan: {name}")
        return RedirectResponse(url="/admin/stock", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error(f"❌ [INVENTORY ADD ERROR]: {e}")
        raise HTTPException(status_code=500, detail="Gagal menyimpan produk baru")

@app.post("/admin/stock/edit/{pid}", tags=["Admin Inventory"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def edit_product(
    pid: int, 
    name: str = Form(...), 
    stock_quantity: int = Form(...), 
    discounted_price: float = Form(...),
    stock_action: str = Form("tetap"), 
    adj_amount: int = Form(0), 
    stock_reason: str = Form("")
):
    """Mengupdate informasi produk sekaligus mencatat audit log mutasi stok"""
    try:
        # Update Core Data
        supabase.table("products").update({
            "name": name, 
            "stock_quantity": stock_quantity, 
            "discounted_price": discounted_price
        }).eq("id", pid).execute()

        # Rekam Log Mutasi jika ada perubahan inventaris fisik
        if stock_action in ['tambah', 'kurang'] and adj_amount > 0:
            log_payload = {
                "product_id": pid,
                "action": stock_action,
                "adjustment_amount": adj_amount,
                "final_stock": stock_quantity,
                "reason": stock_reason if stock_action == 'kurang' else "Restock Inbound"
            }
            supabase.table("stock_logs").insert(log_payload).execute()
            logger.info(f"📝 [AUDIT LOG] Stok Produk ID:{pid} diubah: {stock_action} {adj_amount}")

        return RedirectResponse(url="/admin/stock", status_code=status.HTTP_303_SEE_OTHER)
    
    except Exception as e:
        logger.error(f"❌ [INVENTORY EDIT ERROR]: {e}")
        raise HTTPException(status_code=500, detail="Gagal mengupdate produk")

@app.get("/admin/stock/delete/{pid}", tags=["Admin Inventory"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def delete_product(pid: int):
    """Menghapus (atau menyembunyikan) varian dari database"""
    try:
        # Opsional: Harusnya update is_active = False biar history order gak error
        # Tapi karena ini logic asli lu, kita execute delete langsung
        supabase.table("products").delete().eq("id", pid).execute()
        logger.info(f"🗑️ [INVENTORY] Produk ID:{pid} dihapus.")
        return RedirectResponse(url="/admin/stock", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error(f"❌ [INVENTORY DELETE ERROR]: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    

# ==============================================================================
# ROUTER 5: CRM (CUSTOMER & ORDER MANAGEMENT) (TERGEMBOK 🔐)
# ==============================================================================
@app.get("/admin/orders", response_class=HTMLResponse, tags=["Admin CRM"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def admin_orders(request: Request):
    """Pusat manajemen pesanan yang masuk"""
    pesanan = []
    if supabase:
        try:
            # Query complex untuk narik order, customer info, dan item secara nested
            res = supabase.table("orders").select(
                "*, customers(full_name, phone, username, default_address, telegram_id), order_items(*, products(name, image_url))"
            ).order("created_at", desc=True).execute()
            pesanan = res.data or []
        except Exception as e:
            logger.error(f"❌ [ADMIN ORDERS ERROR]: {e}")
            
    return render_admin_template(request, "admin/orders.html", pesanan=pesanan)

@app.post("/admin/update-order-status", tags=["Admin CRM"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def update_order_status(order_id: str = Form(...), status_order: str = Form(..., alias="status")):
    """Ubah status resi, eksekusi finansial otomatis, pengembalian stok jika batal, dan notifikasi bot"""
    try:
        # 0. Tarik Data Order Lama (Sebelum di-update) buat ngecek state sebelumnya
        res_old_order = supabase.table("orders").select("status, total_amount, order_number, payment_method").eq("id", order_id).single().execute()
        if not res_old_order.data:
            raise HTTPException(status_code=404, detail="Order tidak ditemukan")
        
        old_status = res_old_order.data.get("status", "").lower()
        new_status = status_order.lower()
        omset = float(res_old_order.data.get("total_amount", 0))
        no_order = res_old_order.data.get("order_number")
        payment_method = res_old_order.data.get("payment_method", "Cash")

        # 1. Update DB Pesanan Utama
        supabase.table("orders").update({"status": status_order}).eq("id", order_id).execute()
        logger.info(f"🔄 [ORDER] Status Order ID:{order_id} berubah dari {old_status} menjadi {status_order}")

        # ==========================================================
        # 💸 MAGIC AUTOPILOT 1: PEMASUKAN DANA (IN)
        # ==========================================================
        # Jika orderan diproses/selesai, dan sebelumnya BUKAN diproses/selesai
        if new_status in ["diproses", "selesai"] and old_status not in ["diproses", "selesai"]:
            
            # Cek apakah transaksi ini udah pernah dicatat di mutasi biar gak dobel
            cek_mutasi = supabase.table("finance_mutations").select("id").eq("reference_order_id", order_id).eq("transaction_type", "IN").execute()
            
            if not cek_mutasi.data: # Kalau belum ada di buku kas
                # Coba cari Bank ID berdasarkan payment_method (Misal bayar via "BCA", dia otomatis masuk bank BCA)
                res_bank_search = supabase.table("finance_accounts").select("id, current_balance").ilike("bank_name", f"%{payment_method}%").execute()
                if res_bank_search.data:
                    target_bank_id = res_bank_search.data[0]["id"]
                    saldo_skrg = float(res_bank_search.data[0]["current_balance"])
                else:
                    # Default Fallback (Misal Cash Laci)
                    target_bank_id = 1 
                    res_bank = supabase.table("finance_accounts").select("current_balance").eq("id", target_bank_id).single().execute()
                    saldo_skrg = float(res_bank.data.get("current_balance", 0)) if res_bank.data else 0

                saldo_baru = saldo_skrg + omset
                # Update Saldo Bank
                supabase.table("finance_accounts").update({"current_balance": saldo_baru}).eq("id", target_bank_id).execute()

                # Cari kategori 'Penjualan'
                cat_res = supabase.table("finance_categories").select("id").ilike("category_name", "%penjualan%").limit(1).execute()
                cat_id = cat_res.data[0].get("id") if cat_res.data else 1

                # Catat ke Mutasi Buku Kas
                supabase.table("finance_mutations").insert({
                    "account_id": target_bank_id,
                    "category_id": cat_id,
                    "transaction_type": "IN",
                    "amount": omset,
                    "balance_after": saldo_baru,
                    "description": f"Penerimaan dana otomatis pesanan {no_order} via {payment_method}",
                    "reference_order_id": order_id
                }).execute()
                logger.info(f"💰 [FINANCE] Duit Rp {omset} dari {no_order} otomatis masuk ke Kas (Bank ID: {target_bank_id})!")

        # ==========================================================
        # 📦 MAGIC AUTOPILOT 2: KEMBALIKAN STOK & REFUND (JIKA DIBATALKAN)
        # ==========================================================
        elif new_status == "dibatalkan" and old_status != "dibatalkan":
            
            # A. KEMBALIKAN STOK BARANG (RESTOCK)
            res_items = supabase.table("order_items").select("product_id, quantity").eq("order_id", order_id).execute()
            for item in (res_items.data or []):
                pid = item["product_id"]
                qty_to_restore = item["quantity"]
                
                # Cek stok barang saat ini
                res_prod = supabase.table("products").select("stock_quantity").eq("id", pid).single().execute()
                if res_prod.data:
                    current_stock = int(res_prod.data.get("stock_quantity", 0))
                    restored_stock = current_stock + qty_to_restore
                    
                    # Balikin stok fisik
                    supabase.table("products").update({"stock_quantity": restored_stock}).eq("id", pid).execute()
                    # Catat ke log stok audit
                    supabase.table("stock_logs").insert({
                        "product_id": pid,
                        "action": "RESTORE_BATAL",
                        "adjustment_amount": qty_to_restore,
                        "final_stock": restored_stock,
                        "reason": f"Pengembalian stok dari pesanan batal: {no_order}"
                    }).execute()
            logger.info(f"📦 [INVENTORY] Stok barang untuk pesanan {no_order} berhasil dikembalikan ke gudang.")

            # B. TARIK KEMBALI DANA JIKA SEBELUMNYA SUDAH MASUK BUKU KAS (REFUND)
            cek_mutasi_masuk = supabase.table("finance_mutations").select("account_id").eq("reference_order_id", order_id).eq("transaction_type", "IN").execute()
            cek_mutasi_keluar = supabase.table("finance_mutations").select("id").eq("reference_order_id", order_id).eq("transaction_type", "OUT").execute()
            
            # Jika dulu duitnya udah sempat masuk (status sempat diproses), tapi sekarang dibatalin
            if cek_mutasi_masuk.data and not cek_mutasi_keluar.data:
                bank_refund_id = cek_mutasi_masuk.data[0]["account_id"]
                
                res_bank = supabase.table("finance_accounts").select("current_balance").eq("id", bank_refund_id).single().execute()
                if res_bank.data:
                    saldo_skrg = float(res_bank.data.get("current_balance", 0))
                    saldo_baru = saldo_skrg - omset # Tarik duitnya
                    
                    # Update Saldo Bank
                    supabase.table("finance_accounts").update({"current_balance": saldo_baru}).eq("id", bank_refund_id).execute()
                    
                    # Catat Pengeluaran Refund
                    cat_res = supabase.table("finance_categories").select("id").ilike("category_name", "%refund%").limit(1).execute()
                    cat_id = cat_res.data[0].get("id") if cat_res.data else 1

                    supabase.table("finance_mutations").insert({
                        "account_id": bank_refund_id,
                        "category_id": cat_id,
                        "transaction_type": "OUT",
                        "amount": omset,
                        "balance_after": saldo_baru,
                        "description": f"Koreksi/Refund dana pesanan batal {no_order}",
                        "reference_order_id": order_id
                    }).execute()
                    logger.info(f"💸 [FINANCE REFUND] Dana Rp {omset} ditarik kembali karena {no_order} dibatalkan!")

        # ==========================================================
        # 🤖 3. Notifikasi Background Telegram (Dengan UX Baru)
        # ==========================================================
        if BOT_AVAILABLE:
            try:
                res_order_cust = supabase.table("orders").select("customers(telegram_id, full_name)").eq("id", order_id).single().execute()
                if res_order_cust.data and res_order_cust.data.get("customers"):
                    tele_id = res_order_cust.data["customers"]["telegram_id"]
                    cust_name = res_order_cust.data["customers"]["full_name"]
                    
                    # Emoji Dinamis biar Telegram pesannya lebih asik
                    emoji_status = "✅" if new_status == "selesai" else "🚚" if new_status == "dikirim" else "❌" if new_status == "dibatalkan" else "👉"
                    
                    pesan_notif = (
                        f"🔔 <b>UPDATE PESANAN BABA PARFUME</b>\n\n"
                        f"Halo kak <b>{cust_name}</b>!\n"
                        f"Status pesanan kamu (<code>{no_order}</code>) sekarang:\n"
                        f"{emoji_status} <b>{status_order.upper()}</b>\n\n"
                    )
                    if new_status == "dibatalkan":
                        pesan_notif += "<i>Mohon maaf ya kak pesanan ini dibatalkan. Hubungi admin via bot jika ada kendala.</i>"
                    else:
                        pesan_notif += "<i>Terima kasih kak! ✨</i>"
                        
                    from bot import bot as bot_instance
                    asyncio.create_task(bot_instance.send_message(chat_id=tele_id, text=pesan_notif, parse_mode="HTML"))
            except Exception as e:
                logger.warning(f"⚠️ [NOTIF BOT ERROR] Gagal mengirim info resi: {e}")

        return RedirectResponse(url="/admin/orders", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error(f"❌ [UPDATE STATUS ERROR]: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/customers", response_class=HTMLResponse, tags=["Admin CRM"], dependencies=[require_admin_roles("super_admin", "marketing")])
async def admin_customers(request: Request):
    """Menampilkan direktori klien/pelanggan beserta rekam jejak LTV (Lifetime Value)"""
    pelanggan = []
    if supabase:
        try:
            res_cust = supabase.table("customers").select("*").order("created_at", desc=True).execute()
            pelanggan = res_cust.data or []
            
            res_orders = supabase.table("orders").select("customer_id, total_amount").neq("status", "Menunggu Pembayaran").execute()
            orders_data = res_orders.data or []

            # Mapping LTV Data manual
            for c in pelanggan:
                c_orders = [o for o in orders_data if o['customer_id'] == c['id']]
                c['calc_total_orders'] = len(c_orders)
                c['calc_total_spent'] = sum(float(o['total_amount']) for o in c_orders)
                
        except Exception as e:
            logger.error(f"❌ [ADMIN CUSTOMERS ERROR]: {e}")
            
    return render_admin_template(request, "admin/customers.html", pelanggan=pelanggan)

@app.post("/admin/customers/edit/{cid}", tags=["Admin CRM"], dependencies=[require_admin_roles("super_admin", "marketing")])
async def edit_customer(
    cid: str, 
    full_name: str = Form(...), 
    phone: str = Form(""), 
    default_address: str = Form("")
):
    try:
        supabase.table("customers").update({
            "full_name": full_name,
            "phone": phone,
            "default_address": default_address
        }).eq("id", cid).execute()
        return RedirectResponse(url="/admin/customers", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error(f"❌ [EDIT CUSTOMER ERROR]: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
# ==============================================================================
# ROUTER 6: SISTEM PENGATURAN TOKO (TERGEMBOK 🔐)
# ==============================================================================
@app.get("/admin/settings", response_class=HTMLResponse, tags=["Admin Settings"], dependencies=[require_admin_roles("super_admin")])
async def admin_settings(request: Request):
    """Panel konfigurasi web dan chatbot"""
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
            logger.info("Store settings default di-load")
            
    return render_admin_template(request, "admin/settings.html", settings=settings_data)

@app.post("/admin/settings/update", tags=["Admin Settings"], dependencies=[require_admin_roles("super_admin")])
async def update_settings(
    store_name: str = Form(...),
    admin_whatsapp: str = Form(""),
    checkout_message: str = Form(""),
    is_bot_active: str = Form("false")
):
    try:
        bot_status = True if is_bot_active.lower() in ['true', 'on', '1'] else False
        payload = {
            "store_name": store_name,
            "admin_whatsapp": admin_whatsapp,
            "checkout_message": checkout_message,
            "is_bot_active": bot_status
        }
        supabase.table("store_settings").upsert({**payload, "id": 1}).execute()
        logger.info("⚙️ [SETTINGS] Pengaturan toko diperbarui.")
        return RedirectResponse(url="/admin/settings", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error(f"❌ [UPDATE SETTINGS ERROR]: {e}")
        raise HTTPException(status_code=500, detail="Gagal menyimpan pengaturan.")

# ==============================================================================
# ROUTER 7: ADMIN CS PANEL (SADAP CHAT AI) (TERGEMBOK API 🔐)
# ==============================================================================
@app.get("/admin/cs", response_class=HTMLResponse, tags=["Admin CRM"], dependencies=[require_admin_roles("super_admin", "marketing", "cs")])
async def admin_cs_panel(request: Request):
    """Menampilkan Control Room untuk memantau semua obrolan AI pelanggan"""
    return render_admin_template(request, "admin/cs_management.html")

@app.get("/api/v1/admin/cs/sessions", tags=["API Admin CRM"], dependencies=[require_admin_roles("super_admin", "marketing", "cs")])
async def api_admin_get_sessions():
    """Mengambil daftar list obrolan yang sedang aktif/riwayat"""
    try:
        if not supabase: return api_success(sessions=[])
        
        # 1. Tarik data sesi (TANPA JOIN LANGSUNG KARENA GA ADA FK DI DB)
        res_sess = supabase.table("ai_chat_sessions").select("*").order("created_at", desc=True).execute()
        sessions = res_sess.data or []
        
        # 2. Kalau ada sesi, tarik data customer manual trus gabungin
        if sessions:
            tele_ids = list(set([s["telegram_id"] for s in sessions]))
            # Tarik nama pelanggan berdasarkan telegram_id yang lagi nge-chat
            res_cust = supabase.table("customers").select("telegram_id, full_name, username").in_("telegram_id", tele_ids).execute()
            
            # Bikin kamus (map) buat nyocokin data
            cust_map = {c["telegram_id"]: {"full_name": c.get("full_name"), "username": c.get("username")} for c in (res_cust.data or [])}
            
            # Tempelin nama customer ke masing-masing sesi chat
            for s in sessions:
                s["customers"] = cust_map.get(s["telegram_id"], {"full_name": "Pelanggan Baru", "username": "Anonymous"})
                
        return api_success(sessions=sessions)
        
    except Exception as e:
        logger.error(f"❌ [CS SESSIONS ERROR]: {e}")
        return api_error(str(e), status_code=500)

@app.get("/api/v1/admin/cs/messages", tags=["API Admin CRM"], dependencies=[require_admin_roles("super_admin", "marketing", "cs")])
async def api_admin_get_messages(session_id: int):
    """Intip isi percakapan satu sesi tertentu"""
    try:
        if not supabase: return api_success(messages=[])
        res = supabase.table("ai_chat_messages").select("*").eq("session_id", session_id).order("created_at", desc=False).execute()
        return api_success(messages=res.data or [])
    except Exception as e:
        return api_error(str(e), status_code=500)

@app.post("/api/v1/admin/cs/send-manual", tags=["API Admin CRM"], dependencies=[require_admin_roles("super_admin", "marketing", "cs")])
async def api_admin_send_manual(payload: AdminManualChatPayload):
    """Fungsi pengambilalihan kendali: Lu (Admin) balas chat user secara paksa"""
    try:
        if not supabase:
            return api_error("Database chat belum terhubung", status_code=503)

        # 1. Simpan sbg log admin
        supabase.table("ai_chat_messages").insert({
            "session_id": payload.session_id, 
            "role": "admin", 
            "content": payload.message
        }).execute()

        # 2. Tembak ke Bot
        if BOT_AVAILABLE:
            from bot import bot as bot_instance
            await bot_instance.send_message(chat_id=payload.tele_id, text=f"👨‍💻 <b>Admin BABA:</b>\n{payload.message}", parse_mode="HTML")
        
        logger.info(f"🗣️ [MANUAL CHAT] Admin membalas ID:{payload.tele_id}")
        return api_success(message="Pesan terkirim!")
    except Exception as e:
        logger.error(f"❌ [MANUAL CHAT ERROR]: {e}")
        return api_error("Gagal mengirim pesan manual", status_code=500)

# ==============================================================================
# ROUTER 8: MANAJEMEN STAFF & HAK AKSES (ZONA DEWA 🔐)
# ==============================================================================
@app.get("/admin/staff", response_class=HTMLResponse, tags=["Admin Dewa"], dependencies=[require_admin_roles("super_admin")])
async def admin_staff_page(request: Request):
    """Menampilkan daftar karyawan BABA, khusus Super Admin"""
    staff_list = []
    if supabase:
        try:
            res = supabase.table("admins").select("*").order("created_at", desc=True).execute()
            staff_list = res.data or []
        except Exception as e:
            logger.error(f"❌ [STAFF DB ERROR]: {e}")

    return render_admin_template(request, "admin/staff.html", staffs=staff_list)

@app.post("/admin/staff/add", tags=["Admin Dewa"], dependencies=[require_admin_roles("super_admin")])
async def add_new_staff(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...)
):
    # Enkripsi password sebelum masuk DB
    import hashlib
    hashed_pw = hashlib.sha256(password.encode()).hexdigest()
    safe_role = sanitize_admin_role(role)
    safe_username = username.lower().strip()
    safe_name = full_name.strip()

    if safe_role in {"", "super_admin"}:
        raise HTTPException(status_code=400, detail="Role staff tidak valid.")
    if safe_username == ADMIN_USER.strip().lower():
        raise HTTPException(status_code=400, detail="Username bentrok dengan super admin dari .env.")
    if not supabase:
        raise HTTPException(status_code=503, detail="Database admin tidak tersedia.")

    try:
        supabase.table("admins").insert({
            "username": safe_username,
            "password_hash": hashed_pw,
            "full_name": safe_name,
            "role": safe_role
        }).execute()
        logger.info(f"👮 [STAFF] Akun baru dibuat: {safe_username} sebagai {safe_role}")
        return RedirectResponse(url="/admin/staff", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/staff/delete/{admin_id}", tags=["Admin Dewa"], dependencies=[require_admin_roles("super_admin")])
async def delete_staff(request: Request, admin_id: int):
    if not supabase:
        raise HTTPException(status_code=503, detail="Database admin tidak tersedia.")
    try:
        supabase.table("admins").delete().eq("id", admin_id).execute()
        return RedirectResponse(url="/admin/staff", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/profile", response_class=HTMLResponse, tags=["Admin Settings"], dependencies=[Depends(verify_admin)])
async def admin_profile_page(request: Request):
    """Halaman buat ngecek detail akun si admin yang lagi login"""
    # Ambil data tambahan dari database kalau dia bukan super_admin
    admin_detail = {}
    if getattr(request.state, 'admin_role', '') != 'super_admin' and supabase:
        try:
            res = supabase.table("admins").select("*").eq("username", request.state.admin_user).single().execute()
            if res.data:
                admin_detail = res.data
        except Exception as e:
            logger.error(f"❌ [PROFILE FETCH ERROR]: {e}")

    return render_admin_template(
        request, 
        "admin/profile.html", 
        admin_detail=admin_detail
    )

# ==============================================================================
# ROUTER 9: MODUL ASET KEUANGAN (ZONA DEWA & OPRASIONAL 🔐)
# ==============================================================================
@app.get("/admin/finance/aset", response_class=HTMLResponse, tags=["Admin Finance"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def admin_finance_aset(request: Request):
    """Menampilkan Dashboard Aset & Dompet (Mobile Banking Style)"""
    accounts = []
    total_liquid = 0.0
    total_inventory = 0.0
    recent_mutations = []
    categories_in = []
    categories_out = []

    if supabase:
        try:
            # 1. Tarik Data Rekening Bank
            res_acc = supabase.table("finance_accounts").select("*").eq("is_active", True).order("id").execute()
            accounts = res_acc.data or []
            total_liquid = sum(float(acc.get("current_balance", 0)) for acc in accounts)

            # 2. Hitung Nilai Aset Barang Fisik (Stok * Harga Modal/Jual)
            res_prod = supabase.table("products").select("stock_quantity, original_price").eq("is_active", True).execute()
            for p in (res_prod.data or []):
                total_inventory += float(p.get("stock_quantity", 0)) * float(p.get("original_price", 0))

            # 3. Tarik Riwayat Mutasi Terakhir
            res_mut = supabase.table("finance_mutations").select(
                "*, finance_accounts(bank_name), finance_categories(category_name)"
            ).order("created_at", desc=True).limit(5).execute()
            recent_mutations = res_mut.data or []

            # 4. Tarik Kategori Transaksi
            res_cat = supabase.table("finance_categories").select("*").execute()
            categories = res_cat.data or []
            categories_in = [c for c in categories if c.get("type") == "INCOME"]
            categories_out = [c for c in categories if c.get("type") == "EXPENSE"]

        except Exception as e:
            logger.error(f"❌ [FINANCE ASET ERROR]: {e}")

    return render_admin_template(
        request, "admin/finance_aset.html",
        accounts=accounts,
        total_liquid=total_liquid,
        total_inventory=total_inventory,
        total_aset=total_liquid + total_inventory,
        recent_mutations=recent_mutations,
        categories_in=categories_in,
        categories_out=categories_out
    )

@app.post("/api/v1/finance/transaction", tags=["API Finance"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def api_manual_transaction(request: Request, payload: ManualTransactionPayload):
    """Mencatat Pemasukan/Pengeluaran manual (Suntikan modal, bayar listrik, dll)"""
    if not supabase: return api_error("Database offline", 503)
    
    admin_id = None # Idealnya ditarik dari request.state jika id admin dilacak
    
    try:
        # 1. Cek saldo akun saat ini
        res_acc = supabase.table("finance_accounts").select("current_balance").eq("id", payload.account_id).single().execute()
        if not res_acc.data:
            return api_error("Rekening tidak ditemukan")
        
        current_balance = float(res_acc.data.get("current_balance", 0))
        amount = float(payload.amount)

        # 2. Hitung saldo baru
        if payload.transaction_type == "IN":
            new_balance = current_balance + amount
        else:
            if current_balance < amount:
                return api_error("Saldo tidak cukup untuk pengeluaran ini!", 400)
            new_balance = current_balance - amount

        # 3. Update Saldo Rekening
        supabase.table("finance_accounts").update({"current_balance": new_balance}).eq("id", payload.account_id).execute()

        # 4. Catat ke Buku Besar (Mutasi)
        supabase.table("finance_mutations").insert({
            "account_id": payload.account_id,
            "category_id": payload.category_id,
            "transaction_type": payload.transaction_type,
            "amount": amount,
            "balance_after": new_balance,
            "description": payload.description
        }).execute()

        logger.info(f"💸 [FINANCE] Transaksi {payload.transaction_type} senilai {amount} berhasil di akun {payload.account_id}")
        return api_success(message="Transaksi berhasil dicatat", new_balance=new_balance)

    except Exception as e:
        logger.error(f"❌ [API TRX ERROR]: {e}")
        return api_error("Gagal mencatat transaksi", 500)

@app.post("/api/v1/finance/transfer", tags=["API Finance"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def api_transfer_transaction(request: Request, payload: TransferPayload):
    """Mencatat Pindah Kas / Switch Money Antar Rekening (Mendukung Beda Mata Uang)"""
    if not supabase: return api_error("Database offline", 503)
    
    try:
        # 1. Cek Rekening Sumber (From)
        res_from = supabase.table("finance_accounts").select("current_balance, bank_name, currency").eq("id", payload.from_account_id).single().execute()
        if not res_from.data: return api_error("Rekening sumber tidak ditemukan")
        
        # 2. Cek Rekening Tujuan (To)
        res_to = supabase.table("finance_accounts").select("current_balance, bank_name, currency").eq("id", payload.to_account_id).single().execute()
        if not res_to.data: return api_error("Rekening tujuan tidak ditemukan")

        balance_from = float(res_from.data.get("current_balance", 0))
        balance_to = float(res_to.data.get("current_balance", 0))

        # 3. Validasi Saldo Sumber
        if balance_from < payload.amount_out:
            return api_error(f"Saldo {res_from.data.get('bank_name')} tidak cukup! Saldo: {balance_from}", 400)

        # 4. Hitung Saldo Baru
        new_balance_from = balance_from - payload.amount_out
        new_balance_to = balance_to + payload.amount_in

        # 5. Cari Kategori "Pindah Kas" atau "Transfer"
        # Kalau gak ada, kita pake ID 1 aja sebagai fallback
        cat_res = supabase.table("finance_categories").select("id").ilike("category_name", "%pindah%").limit(1).execute()
        if not cat_res.data:
            cat_res = supabase.table("finance_categories").select("id").ilike("category_name", "%transfer%").limit(1).execute()
        
        cat_id = cat_res.data[0].get("id") if cat_res.data else 1

        # =======================================================
        # EKSEKUSI DATABASE (POTONG -> TAMBAH -> LOG MUTASI)
        # =======================================================
        
        # Bikin UUID unik untuk referensi transaksi ini (biar gampang dilacak)
        transfer_ref = f"TF-{datetime.now().strftime('%y%m%d%H%M')}"
        deskripsi_lengkap = f"[{transfer_ref}] {payload.description} (Rate: {payload.exchange_rate})"

        # A. UPDATE & LOG REKENING SUMBER (KELUAR)
        supabase.table("finance_accounts").update({"current_balance": new_balance_from}).eq("id", payload.from_account_id).execute()
        supabase.table("finance_mutations").insert({
            "account_id": payload.from_account_id,
            "category_id": cat_id,
            "transaction_type": "OUT",
            "amount": payload.amount_out,
            "balance_after": new_balance_from,
            "description": f"Pindah kas keluar ke {res_to.data.get('bank_name')} - {deskripsi_lengkap}"
        }).execute()

        # B. UPDATE & LOG REKENING TUJUAN (MASUK)
        supabase.table("finance_accounts").update({"current_balance": new_balance_to}).eq("id", payload.to_account_id).execute()
        supabase.table("finance_mutations").insert({
            "account_id": payload.to_account_id,
            "category_id": cat_id,
            "transaction_type": "IN",
            "amount": payload.amount_in,
            "balance_after": new_balance_to,
            "description": f"Terima pindah kas dari {res_from.data.get('bank_name')} - {deskripsi_lengkap}"
        }).execute()

        logger.info(f"💱 [FINANCE TRANSFER] {payload.amount_out} {res_from.data.get('currency')} dipindah ke {res_to.data.get('currency')} jadi {payload.amount_in}")
        return api_success(message="Pindah kas berhasil diproses!")

    except Exception as e:
        logger.error(f"❌ [API TRANSFER ERROR]: {e}")
        return api_error("Gagal memproses pindah kas", 500)

# ==============================================================================
# ROUTER 10: MUTASI BUKU BESAR (ZONA DEWA 🔐)
# ==============================================================================
@app.get("/admin/finance/mutasi", response_class=HTMLResponse, tags=["Admin Finance"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def admin_finance_mutasi(request: Request):
    """Menampilkan Ledger / Riwayat Buku Besar Keseluruhan"""
    mutations = []
    accounts = []
    if supabase:
        try:
            res_acc = supabase.table("finance_accounts").select("id, bank_name").execute()
            accounts = res_acc.data or []

            res_mut = supabase.table("finance_mutations").select(
                "*, finance_accounts(bank_name), finance_categories(category_name, type)"
            ).order("created_at", desc=True).limit(500).execute() # Limit agar tidak berat
            mutations = res_mut.data or []
        except Exception as e:
            logger.error(f"❌ [FINANCE MUTASI ERROR]: {e}")

    return render_admin_template(
        request, "admin/finance_mutasi.html", 
        mutations=mutations, accounts=accounts
    )

# ==============================================================================
# ROUTER 11: LAPORAN LABA RUGI (HANYA SUPER ADMIN 🔐)
# ==============================================================================
@app.get("/admin/finance/report", response_class=HTMLResponse, tags=["Admin Finance"], dependencies=[require_admin_roles("super_admin")])
async def admin_finance_report(request: Request, month: Optional[str] = None, year: Optional[str] = None):
    """
    Generate Profit & Loss (P&L) Statement Terlengkap.
    Mempertahankan logic asli untuk memastikan tidak ada efek domino,
    ditambah dengan analisis tingkat lanjut (Kategori, Tren Harian, OpEx terbanyak).
    """
    
    # 1. STRUKTUR ASLI (DIPERTAHANKAN) + EKSPANSI METRIK BARU
    report_data = {
        "total_revenue": 0.0,
        "total_hpp": 0.0,
        "total_opex": 0.0,
        "gross_profit": 0.0,
        "net_profit": 0.0,
        "margin": 0.0,
        # --- Ekstra Metrik Dewa ---
        "total_trx_in": 0,          # Jumlah transaksi masuk
        "total_trx_out": 0,         # Jumlah transaksi keluar
        "avg_revenue_per_day": 0.0, # Rata-rata omset harian
        "biggest_expense_cat": "",  # Biaya paling bengkak bulan ini
        "biggest_expense_amt": 0.0
    }
    
    # 2. STRUKTUR UNTUK RINCIAN KATEGORI & GRAFIK (Untuk dikirim ke HTML)
    categories_breakdown = {
        "income": {},
        "hpp": {},
        "opex": {}
    }
    daily_trends = {}
    raw_mutations = []
    
    if supabase:
        try:
            # 3. Tentukan Periode (Bisa nerima parameter filter dari URL, atau default bulan ini)
            now = datetime.now()
            target_month = month if month else now.strftime("%m")
            target_year = year if year else now.strftime("%Y")
            period_prefix = f"{target_year}-{target_month}"

            # 4. Mengambil mutasi sesuai periode (LOGIC ASLI DIPERTAHANKAN)
            res_mut = supabase.table("finance_mutations").select(
                "id, amount, transaction_type, created_at, description, finance_categories(category_name, type), finance_accounts(bank_name)"
            ).like("created_at", f"{period_prefix}%").order("created_at", desc=False).execute()
            
            raw_mutations = res_mut.data or []
            
            # 5. PROSES KALKULASI MENDALAM
            for m in raw_mutations:
                # Persiapan Data Iterasi
                cat_info = m.get("finance_categories") or {}
                raw_cat_name = cat_info.get("category_name", "Tanpa Kategori")
                cat_name = str(raw_cat_name).lower()
                
                amt = float(m.get("amount", 0))
                trx_date = m.get("created_at", "").split("T")[0] # Ambil YYYY-MM-DD
                
                # Setup Daily Trend awal jika belum ada
                if trx_date not in daily_trends:
                    daily_trends[trx_date] = {"in": 0.0, "out": 0.0}
                
                # === A. LOGIC PEMASUKAN (IN) ===
                if m.get("transaction_type") == "IN":
                    report_data["total_revenue"] += amt
                    report_data["total_trx_in"] += 1
                    daily_trends[trx_date]["in"] += amt
                    
                    # Masukkan ke rincian kategori Income
                    categories_breakdown["income"][raw_cat_name] = categories_breakdown["income"].get(raw_cat_name, 0) + amt

                # === B. LOGIC PENGELUARAN (OUT) ===
                elif m.get("transaction_type") == "OUT":
                    report_data["total_trx_out"] += 1
                    daily_trends[trx_date]["out"] += amt
                    
                    # Identifikasi HPP (Belanja Stok & Ongkir impor) -> LOGIC ASLI TETAP UTUH
                    is_hpp = any(keyword in cat_name for keyword in ["stok", "belanja", "jastip", "ongkir", "biang", "botol", "lakban"])
                    
                    if is_hpp:
                        report_data["total_hpp"] += amt
                        # Masukkan ke rincian kategori HPP
                        categories_breakdown["hpp"][raw_cat_name] = categories_breakdown["hpp"].get(raw_cat_name, 0) + amt
                    else:
                        report_data["total_opex"] += amt
                        # Masukkan ke rincian kategori OpEx
                        categories_breakdown["opex"][raw_cat_name] = categories_breakdown["opex"].get(raw_cat_name, 0) + amt
                        
                        # Deteksi Pengeluaran Operasional Terbesar (Biar lu tau duit bocor di mana)
                        if categories_breakdown["opex"][raw_cat_name] > report_data["biggest_expense_amt"]:
                            report_data["biggest_expense_amt"] = categories_breakdown["opex"][raw_cat_name]
                            report_data["biggest_expense_cat"] = raw_cat_name

            # 6. KALKULASI HASIL AKHIR (LOGIC ASLI)
            report_data["gross_profit"] = report_data["total_revenue"] - report_data["total_hpp"]
            report_data["net_profit"] = report_data["gross_profit"] - report_data["total_opex"]
            
            if report_data["total_revenue"] > 0:
                report_data["margin"] = round((report_data["net_profit"] / report_data["total_revenue"]) * 100, 2)
            
            # Hitung rata-rata omset harian bulan ini
            active_days = len(daily_trends) if len(daily_trends) > 0 else 1
            report_data["avg_revenue_per_day"] = round(report_data["total_revenue"] / active_days, 2)

            logger.info(f"📊 [FINANCE REPORT] Kalkulasi selesai. Omset: {report_data['total_revenue']} | Profit: {report_data['net_profit']}")

        except Exception as e:
            logger.error(f"❌ [FINANCE REPORT ERROR]: {e}")

    # Kembalikan semua data komprehensif ini ke template
    return render_admin_template(
        request, 
        "admin/finance_report.html", 
        report=report_data,                      # Data Asli yang di-upgrade
        breakdown=categories_breakdown,          # Rincian per kategori buat tabel
        daily_trends=daily_trends,               # Data mentah buat grafik Chart.js
        mutations=raw_mutations,                 # Raw data buat script Alpine.js
        period_text=f"{target_month}/{target_year}"
    )

# ==============================================================================
# ROUTER 12: MODUL BELANJA STOK (KULAKAN & JASTIP)
# ==============================================================================
@app.get("/admin/stock/belanja", response_class=HTMLResponse, tags=["Admin Inventory"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def admin_stock_belanja(request: Request):
    """Halaman rekap dan pembuatan Purchase Order (PO)"""
    purchases = []
    accounts = []
    products = [] # <--- Tambahin ini biar Autocomplete JS jalan
    
    if supabase:
        try:
            res_po = supabase.table("stock_purchases").select(
                "*, finance_accounts(bank_name), stock_purchase_items(item_name)"
            ).order("created_at", desc=True).execute()
            purchases = res_po.data or []

            res_acc = supabase.table("finance_accounts").select("id, bank_name, currency").eq("is_active", True).execute()
            accounts = res_acc.data or []
            
            # --- TAMBAHAN WAJIB UNTUK AUTOCOMPLETE ---
            res_prod = supabase.table("products").select("id, name, original_price, stock_quantity").eq("is_active", True).execute()
            products = res_prod.data or []
            # ------------------------------------------

        except Exception as e:
            logger.error(f"❌ [STOCK BELANJA ERROR]: {e}")

    return render_admin_template(
        request, 
        "admin/stock_belanja.html", 
        purchases=purchases, 
        accounts=accounts,
        products=products # <--- Jangan lupa di passing ke template
    )

@app.post("/api/v1/stock/belanja/process", tags=["API Inventory"], dependencies=[require_admin_roles("super_admin", "oprasional")])
async def api_process_purchase_order(payload: PurchaseOrderPayload):
    """CORE ENGINE: Memproses PO, potong uang bank, dan nambah stok fisik otomatis"""
    if not supabase: return api_error("Database offline", 503)

    try:
        # 1. Validasi Saldo Bank Dulu
        res_acc = supabase.table("finance_accounts").select("current_balance").eq("id", payload.account_id).single().execute()
        if not res_acc.data: return api_error("Rekening tidak ditemukan")
        
        current_balance = float(res_acc.data.get("current_balance", 0))
        total_items_cost = sum(i.quantity * i.capital_price_per_unit for i in payload.items)
        grand_total = total_items_cost + payload.shipping_cost

        if current_balance < grand_total:
            return api_error(f"Saldo rekening tidak mencukupi! Butuh: {format_currency(grand_total)}")

        # 2. Generate PO Number
        po_number = f"PO-{datetime.now().strftime('%y%m')}-{str(uuid.uuid4())[:5].upper()}"

        # 3. Insert Tabel `stock_purchases`
        po_res = supabase.table("stock_purchases").insert({
            "purchase_number": po_number,
            "account_id": payload.account_id,
            "total_items_cost": total_items_cost,
            "shipping_cost": payload.shipping_cost,
            "grand_total": grand_total,
            "notes": payload.notes
        }).execute()
        po_id = po_res.data[0].get("id")

        # 4. Insert Items & Tambah Stok Fisik
        for item in payload.items:
            subtotal = item.quantity * item.capital_price_per_unit
            supabase.table("stock_purchase_items").insert({
                "purchase_id": po_id,
                "product_id": item.product_id,
                "item_name": item.item_name,
                "quantity": item.quantity,
                "capital_price_per_unit": item.capital_price_per_unit,
                "subtotal": subtotal
            }).execute()

            # Jika barang terkait dengan produk di etalase, tambah stoknya!
            if item.product_id:
                res_prod = supabase.table("products").select("stock_quantity").eq("id", item.product_id).single().execute()
                if res_prod.data:
                    new_stock = int(res_prod.data.get("stock_quantity", 0)) + item.quantity
                    supabase.table("products").update({"stock_quantity": new_stock}).eq("id", item.product_id).execute()
                    
                    # Log penambahan stok
                    supabase.table("stock_logs").insert({
                        "product_id": item.product_id,
                        "action": "BELANJA_INBOUND",
                        "adjustment_amount": item.quantity,
                        "final_stock": new_stock,
                        "reason": f"Masuk dari PO: {po_number}"
                    }).execute()

        # 5. Potong Saldo Bank
        new_balance = current_balance - grand_total
        supabase.table("finance_accounts").update({"current_balance": new_balance}).eq("id", payload.account_id).execute()

        # 6. Catat Ledger Pengeluaran (Mutasi)
        # Cari Kategori 'Belanja Stok'
        cat_res = supabase.table("finance_categories").select("id").ilike("category_name", "%belanja%").limit(1).execute()
        cat_id = cat_res.data[0].get("id") if cat_res.data else 1 # Fallback ID 1 jika tidak nemu

        supabase.table("finance_mutations").insert({
            "account_id": payload.account_id,
            "category_id": cat_id,
            "transaction_type": "OUT",
            "amount": grand_total,
            "balance_after": new_balance,
            "description": f"Pembayaran {po_number}. {payload.notes}",
            "reference_purchase_id": po_id
        }).execute()

        logger.info(f"🚚 [PROCUREMENT] Purchase Order {po_number} senilai {grand_total} berhasil di-deploy!")
        return api_success(message="PO Berhasil diproses", po_number=po_number)

    except Exception as e:
        logger.error(f"❌ [API PO ERROR]: {e}")
        # Idealnya ada mekanisme manual rollback Supabase di sini jika gagal di tengah jalan
        return api_error(f"Gagal memproses PO: {str(e)}", 500)

# ==============================================================================
# ENTRY POINT RUNNER (UVICORN)
# ==============================================================================
if __name__ == "__main__":
    # Mengambil port secara dinamis dari Render/VPS, atau 8000 untuk localhost
    port = int(os.environ.get("PORT", 8000))
    # Bind ke 0.0.0.0 agar bisa diakses public dari cloud
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
