import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Cek apakah file .env ada, baru di-load (biar nggak error di server)
if os.path.exists(".env"):
    load_dotenv()

# Ngambil URL dan KEY
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")

# Validasi awal: Biar nggak bingung kalau lupa masukin variable di Render
if not url or not key:
    print("⚠️ [WARNING] SUPABASE_URL atau SUPABASE_KEY nggak ketemu!")
    print("Pastikan sudah isi di Dashboard Render > Environment")

# Bikin mesin koneksi ke Supabase
try:
    # Pastikan url dan key tidak None sebelum create_client
    if url and key:
        supabase: Client = create_client(url, key)
        print("✅ Berhasil connect ke brankas Supabase!")
    else:
        supabase = None
except Exception as e:
    print(f"❌ Gagal connect ke Supabase: {e}")
    supabase = None
