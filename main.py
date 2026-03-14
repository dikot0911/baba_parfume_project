from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn

# IMPORT SATPAM DATABASE KITA
from database import supabase

# ==========================================
# 1. INISIASI APLIKASI FASTAPI
# ==========================================
app = FastAPI(
    title="Baba Parfume API",
    description="Sistem Backend terintegrasi untuk Web & Bot Telegram Baba Parfume",
    version="1.0.0"
)

# ==========================================
# 2. SETUP FOLDER ASSET & TEMPLATE
# ==========================================
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ==========================================
# 3. ROUTE / HALAMAN CUSTOMER
# ==========================================
@app.get("/", response_class=HTMLResponse, tags=["Customer"])
async def read_root(request: Request):
    """Halaman utama katalog BABA Parfume untuk pelanggan"""
    return templates.TemplateResponse("customer/index.html", {"request": request})


# ==========================================
# 4. ROUTE / HALAMAN ADMIN PANEL (FULL DB CONTROL)
# ==========================================
@app.get("/admin", response_class=HTMLResponse, tags=["Admin"])
async def admin_dashboard(request: Request):
    """Halaman Dashboard - Narik total statistik dari Supabase"""
    try:
        # Menghitung jumlah data buat ditampilin di dashboard
        total_produk = len(supabase.table("products").select("id").execute().data)
        total_pelanggan = len(supabase.table("customers").select("id").execute().data)
        total_pesanan = len(supabase.table("orders").select("id").execute().data)
    except Exception as e:
        print(f"Error Supabase (Dashboard): {e}")
        total_produk, total_pelanggan, total_pesanan = 0, 0, 0

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, 
        "total_produk": total_produk,
        "total_pelanggan": total_pelanggan,
        "total_pesanan": total_pesanan
    })

@app.get("/admin/stock", response_class=HTMLResponse, tags=["Admin"])
async def admin_stock(request: Request):
    try:
        # Narik data
        response = supabase.table("products").select("*").execute()
        
        # Simpan ke variabel
        data_parfum = response.data
        
        # Cek di terminal (liat log pas lu refresh web)
        print(f"DEBUG DATA: {data_parfum}") 
        
    except Exception as e:
        print(f"❌ Error: {e}")
        data_parfum = []

    return templates.TemplateResponse("admin/stock.html", {
        "request": request,
        "produk": data_parfum  ### PENTING: Nama 'produk' ini harus sama dengan di HTML
    })

@app.post("/admin/add-product",response_class=HTMLResponse, tags=["Admin"])
async def add_product(
    name: str = Form(...),
    original_price: float = Form(...),
    discounted_price: float = Form(...),
    stock_quantity: int = Form(...),
    category_id: int = Form(1),
    tags: str = Form(None),
    tagline: str = Form(None),
    description: str = Form(None),
    top_notes: str = Form(None),
    heart_notes: str = Form(None),
    base_notes: str = Form(None),
    longevity: str = Form(None),
    recommendation: str = Form(None),
    image_url: str = Form(None)
):
    try:
        # Proses String (koma) jadi List/Array buat Supabase
        def to_list(text):
            return [x.strip() for x in text.split(",")] if text else []

        data_input = {
            "name": name,
            "original_price": original_price,
            "discounted_price": discounted_price,
            "stock_quantity": stock_quantity,
            "category_id": category_id,
            "tagline": tagline,
            "description": description,
            "image_url": image_url,
            "longevity": longevity,
            "recommendation": recommendation,
            "is_active": True,
            # Ini penting: kolom ARRAY harus dikirim dalam bentuk LIST []
            "tags": to_list(tags),
            "top_notes": to_list(top_notes),
            "heart_notes": to_list(heart_notes),
            "base_notes": to_list(base_notes)
        }

        # Eksekusi ke Supabase
        supabase.table("products").insert(data_input).execute()
        
        # Redirect balik ke halaman stok setelah berhasil
        return RedirectResponse(url="/admin/stock", status_code=303)

    except Exception as e:
        print(f"❌ Error Pas Input Data: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/admin/orders", response_class=HTMLResponse, tags=["Admin"])
async def admin_orders(request: Request):
    """Halaman Order - Narik data pesanan"""
    try:
        # Ambil pesanan dan gabungin sama nama pelanggannya
        response = supabase.table("orders").select("*, customers(full_name)").order("created_at", desc=True).execute()
        order_list = response.data
    except Exception as e:
        print(f"Error Supabase (Orders): {e}")
        order_list = []

    return templates.TemplateResponse("admin/orders.html", {
        "request": request,
        "pesanan": order_list
    })

@app.get("/admin/customers", response_class=HTMLResponse, tags=["Admin"])
async def admin_customers(request: Request):
    """Halaman Pelanggan - Narik data user bot/web"""
    try:
        response = supabase.table("customers").select("*").order("created_at", desc=True).execute()
        pelanggan_list = response.data
    except Exception as e:
        print(f"Error Supabase (Customers): {e}")
        pelanggan_list = []

    return templates.TemplateResponse("admin/customers.html", {
        "request": request,
        "pelanggan": pelanggan_list
    })

@app.get("/admin/settings", response_class=HTMLResponse, tags=["Admin"])
async def admin_settings(request: Request):
    """Halaman Pengaturan Sistem"""
    return templates.TemplateResponse("admin/settings.html", {"request": request})


# ==========================================
# 5. MESIN SERVER (UVICORN)
# ==========================================
if __name__ == "__main__":
    print("==================================================")
    print("🚀 SERVER BABA PARFUME BERHASIL NYALA!")
    print("🌐 Web Customer    : http://localhost:8000/")
    print("🛠️  Panel Admin     : http://localhost:8000/admin")
    print("📖 Dokumentasi API : http://localhost:8000/docs")
    print("==================================================")
    
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)