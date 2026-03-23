import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

logger = logging.getLogger("baba.database")

# Cek apakah file .env ada, baru di-load (biar nggak error di server)
if os.path.exists(".env"):
    load_dotenv()

# Ngambil URL dan KEY
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")

# Validasi awal: Biar nggak bingung kalau lupa masukin variable di Render
if not url or not key:
    logger.info("SUPABASE_URL atau SUPABASE_KEY belum terpasang; aplikasi berjalan dalam mode fallback")

# Bikin mesin koneksi ke Supabase
try:
    # Pastikan url dan key tidak None sebelum create_client
    if url and key:
        supabase: Client = create_client(url, key)
        logger.info("Berhasil connect ke Supabase")
    else:
        supabase = None
except Exception as e:
    logger.warning("Gagal connect ke Supabase: %s", e)
    supabase = None
