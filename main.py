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
    """Ubah status resi dan tembak notifikasi via bot otomatis"""
    try:
        # Update DB
        supabase.table("orders").update({"status": status_order}).eq("id", order_id).execute()
        logger.info(f"🔄 [ORDER] Status Order ID:{order_id} menjadi {status_order}")
        
        # Eksekusi Notifikasi Background Telegram
        if BOT_AVAILABLE:
            try:
                res_order = supabase.table("orders").select("order_number, customers(telegram_id, full_name)").eq("id", order_id).single().execute()
                if res_order.data and res_order.data.get("customers"):
                    tele_id = res_order.data["customers"]["telegram_id"]
                    cust_name = res_order.data["customers"]["full_name"]
                    no_order = res_order.data["order_number"]
                    
                    pesan_notif = (
                        f"🔔 <b>UPDATE PESANAN BABA PARFUME</b>\n\n"
                        f"Halo kak <b>{cust_name}</b>!\n"
                        f"Status pesanan kamu (<code>{no_order}</code>) sekarang:\n"
                        f"👉 <b>{status_order.upper()}</b>\n\n"
                        f"<i>Terima kasih kak! ✨</i>"
                    )
                    
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
        res = supabase.table("ai_chat_sessions").select("*, customers(full_name, username)").order("created_at", desc=True).execute()
        return api_success(sessions=res.data or [])
    except Exception as e:
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

# ==============================================================================
# ENTRY POINT RUNNER (UVICORN)
# ==============================================================================
if __name__ == "__main__":
    # Mengambil port secara dinamis dari Render/VPS, atau 8000 untuk localhost
    port = int(os.environ.get("PORT", 8000))
    # Bind ke 0.0.0.0 agar bisa diakses public dari cloud
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
