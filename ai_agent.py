import os
import logging
import time
import asyncio
from typing import Dict, List, Optional

from database import supabase

logger = logging.getLogger("baba.ai.enterprise")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ==============================================================================
# 1. INISIALISASI MESIN AI (GOOGLE GENAI SDK)
# ==============================================================================
client = None
if GEMINI_API_KEY:
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("✅ [AI ENGINE] Gemini 2.5 Flash Enterprise Ready!")
    except Exception as exc:
        logger.error(f"❌ [AI ENGINE] Gagal inisialisasi: {exc}")

# ==============================================================================
# 2. SISTEM KEAMANAN & ANTI-SPAM (CREDIT SAVER)
# ==============================================================================
# Menyimpan riwayat chat per user untuk membatasi spam
SPAM_TRACKER: Dict[int, List[float]] = {}
MAX_MESSAGES_PER_MINUTE = 7  # Maksimal 7 pesan per menit
MAX_CHARACTERS = 500         # Maksimal huruf per pesan (biar ga dikirim cerpen)

def is_spam(tele_id: int, user_message: str) -> bool:
    """Fungsi tameng buat nge-block user iseng yang spam chat."""
    current_time = time.time()
    
    # 1. Cek Panjang Pesan (Cegah token burning)
    if len(user_message) > MAX_CHARACTERS:
        return True, f"Waduh kak, kepanjangan ngetiknya 😅 Maksimal {MAX_CHARACTERS} huruf ya biar Mimin ga pusing bacanya."

    # 2. Cek Karakter Berulang (Gibberish) misal "aaaaaaaaa"
    if len(set(user_message.replace(" ", ""))) < 3 and len(user_message) > 10:
        return True, "Kakak ngetik apa tuh? Mimin ga ngerti hehe. Ketik yang bener dong kak ✨"

    # 3. Cek Frekuensi Pesan (Rate Limiting)
    if tele_id not in SPAM_TRACKER:
        SPAM_TRACKER[tele_id] = []
    
    # Bersihkan log waktu yang udah lebih dari 60 detik
    SPAM_TRACKER[tele_id] = [t for t in SPAM_TRACKER[tele_id] if current_time - t < 60]
    
    if len(SPAM_TRACKER[tele_id]) >= MAX_MESSAGES_PER_MINUTE:
        return True, "Sabar kak, ngetiknya cepet banget kaya pelari marathon 🏃‍♂️💨 Tunggu 1 menitan lagi ya baru chat Mimin."
    
    # Lolos dari semua jebakan, catat waktu pesannya
    SPAM_TRACKER[tele_id].append(current_time)
    return False, ""

# ==============================================================================
# 3. DATABASE HELPER (SESSION & KNOWLEDGE)
# ==============================================================================
async def get_or_create_session(tele_id: int):
    """Mencari sesi aktif atau membuat baru di database."""
    if not supabase: return None
    res = supabase.table("ai_chat_sessions").select("id").eq("telegram_id", tele_id).eq("is_active", True).execute()
    if res.data: return res.data[0]["id"]
    new_sess = supabase.table("ai_chat_sessions").insert({"telegram_id": tele_id}).execute()
    return new_sess.data[0]["id"]

# Cache sederhana biar ga bolak-balik nembak database kalau stok ga berubah dalam 5 menit
KNOWLEDGE_CACHE = {"data": "", "last_fetched": 0}

async def get_perfume_knowledge_base():
    """Mengambil data stok terupdate, menggunakan cache 5 menit untuk kecepatan."""
    current_time = time.time()
    if current_time - KNOWLEDGE_CACHE["last_fetched"] < 300 and KNOWLEDGE_CACHE["data"]:
        return KNOWLEDGE_CACHE["data"]

    if not supabase: return ""

    res = supabase.table("products").select(
        "name, tags, tagline, discounted_price, stock_quantity, top_notes, heart_notes, base_notes"
    ).eq("is_active", True).gt("stock_quantity", 0).execute()

    katalog = ""
    for p in (res.data or []):
        tags_str = ", ".join(p["tags"]) if isinstance(p.get("tags"), list) else str(p.get("tags") or "-")
        katalog += (
            f"- {p.get('name', 'Tanpa Nama')}: {p.get('tagline', '-')}. "
            f"Kategori/Tags: {tags_str}. "
            f"Wangi Detail: {p.get('top_notes', [])} (awal), {p.get('heart_notes', [])} (tengah). "
            f"Harga: ${p.get('discounted_price', 0)}. Stok: {p.get('stock_quantity', 0)} pcs.\n"
        )
    
    KNOWLEDGE_CACHE["data"] = katalog
    KNOWLEDGE_CACHE["last_fetched"] = current_time
    return katalog

# ==============================================================================
# 4. SELF-LEARNING ENGINE (ANALISIS FEEDBACK BINTANG)
# ==============================================================================
async def get_ai_learning_context() -> str:
    """
    Fungsi super canggih: Menganalisis rating dan keluhan user dari tabel `ai_feedbacks`
    untuk mengubah perilaku AI secara dinamis.
    """
    if not supabase: return ""

    try:
        # Ambil 10 feedback terbaru buat bahan evaluasi
        res = supabase.table("ai_feedbacks").select("rating, complaint").order("created_at", desc=True).limit(10).execute()
        feedbacks = res.data or []
        
        if not feedbacks:
            return "Kamu belum menerima feedback. Lakukan yang terbaik sesuai instruksi awal."

        avg_rating = sum(f['rating'] for f in feedbacks) / len(feedbacks)
        keluhan_teks = [f['complaint'] for f in feedbacks if f.get('complaint') and f['complaint'].strip() != ""]

        learning_prompt = f"EVALUASI KINERJAMU SAAT INI (Rata-rata rating: {avg_rating:.1f}/5.0):\n"

        # Logika adaptasi berdasarkan Bintang
        if avg_rating <= 1.5:
            learning_prompt += "🚨 KRITIS (Bintang 1): Pengguna merasa kamu sangat kurang memuaskan. KAMU HARUS lebih mengerti, berempati, banyak bertanya kebutuhan mereka, dan jangan asal jualan!\n"
        elif avg_rating <= 2.5:
            learning_prompt += "⚠️ PERLU EVALUASI (Bintang 2): Perhatikan gaya bahasamu. Jangan terlalu kaku, coba lebih mengalir dan pastikan kamu menjawab inti pertanyaan pengguna.\n"
        elif avg_rating <= 3.5:
            learning_prompt += "🟡 CUKUP (Bintang 3): Jawabanmu masih kurang bisa dimengerti oleh beberapa orang. Gunakan analogi yang lebih gampang dan sederhanakan bahasamu.\n"
        elif avg_rating <= 4.5:
            learning_prompt += "🟢 BAGUS (Bintang 4): Pengguna suka gayamu! Tetap pertahankan keseruannya, tapi coba maksimalkan lagi detail penawarannya agar lebih nge-hook.\n"
        else:
            learning_prompt += "🌟 SEMPURNA (Bintang 5): Pertahankan gayamu yang sekarang! Pengguna sangat puas. Kembangkan gaya asikmu ini.\n"

        # Injeksi keluhan tertulis ke dalam memori AI
        if keluhan_teks:
            learning_prompt += "\nKELUHAN/MASUKAN USER BARU-BARU INI (Pastikan kamu TIDAK MENGULANGI kesalahan ini):\n"
            for keluhan in keluhan_teks[:3]: # Ambil 3 keluhan terbaru aja biar ga kepanjangan
                learning_prompt += f"- \"{keluhan}\"\n"
        
        return learning_prompt
    except Exception as e:
        logger.error(f"Gagal memuat learning context: {e}")
        return ""

# ==============================================================================
# 5. CORE SYSTEM & FALLBACK
# ==============================================================================
def build_fallback_reply(user_message: str) -> str:
    lowered = user_message.lower()
    if any(keyword in lowered for keyword in ["cowok", "man", "maskulin"]):
        vibe = "nyari yang fresh atau woody biar keliatan gentle"
    elif any(keyword in lowered for keyword in ["cewek", "woman", "feminin"]):
        vibe = "nyari wangi kalem, manis, atau floral biar makin anggun"
    else:
        vibe = "milih wangi yang paling pas sama karakter kakak"

    return (
        "Halo kak! Mimin BABA di sini ✨ Sistem katalog kita lagi istirahat bentar nih.\n\n"
        f"Tapi santai, kalau kakak lagi {vibe}, sebutin aja biasa pakenya buat acara apa atau budgetnya berapa. "
        "Nanti Mimin bantu cariin racikan yang paling the best buat kakak! 🙌"
    )

async def get_ai_recommendation(tele_id: int, user_message: str) -> str:
    """Fungsi utama untuk memproses chat user dengan otak Gemini 2.5 Flash Enterprise."""
    
    # 1. CEK ANTI-SPAM DULU SEBELUM MIKIR
    is_spamming, spam_warning = is_spam(tele_id, user_message)
    if is_spamming:
        logger.warning(f"🛡️ [ANTI-SPAM] Blocked message from {tele_id}")
        return spam_warning

    try:
        sid = await get_or_create_session(tele_id)

        # Simpan chat user
        if supabase and sid:
            supabase.table("ai_chat_messages").insert({
                "session_id": sid, "role": "user", "content": user_message
            }).execute()

        # Ambil History Chat (Maksimal 10 percakapan biar ingat konteks)
        chat_context = ""
        if supabase and sid:
            history_res = supabase.table("ai_chat_messages").select("role, content").eq("session_id", sid).order("created_at", desc=False).limit(10).execute()
            for h in (history_res.data or []):
                role_name = "User" if h["role"] == "user" else "AI"
                chat_context += f"{role_name}: {h['content']}\n"

        # Tarik data dari Database & Sistem Pembelajaran
        stok_realtime = await get_perfume_knowledge_base()
        learning_context = await get_ai_learning_context()

        if client and stok_realtime:
            # 2. THE MASTER PROMPT (Dengan Injeksi Pembelajaran Mandiri)
            system_instruction = f"""
            Kamu adalah 'Mimin BABA', asisten virtual dan ahli parfum dari BABA Parfume.
            
            IDENTITAS & GAYA BAHASA:
            - Santai, humble, asik, ala Gen Z. Gunakan bahasa sehari-hari yang merakyat dan gampang dimengerti siapa aja (nggak baku/kaku).
            - Panggil user dengan sebutan 'Kak'.
            - Jawabanmu harus SINGKAT, PADAT, NYAMAN DIBACA. JANGAN KAYA KORAN!
            
            {learning_context}
            
            GAYA JUALAN (MARKETING 4.0):
            - Fokus ke fungsi, masalah yang diselesaikan, dan momen pemakaian (misal: "bikin cewek nempel", "enak buat ngantor biar seger", "cocok buat nge-date").
            - JANGAN jelaskan detail piramida wangi (top/heart/base notes) di awal.
            - Kasih detail notes HANYA kalau user secara spesifik bertanya tentang detail aroma parfum tertentu.

            ATURAN FORMAT REKOMENDASI:
            Jika kamu memberikan list rekomendasi parfum yang tersedia, WAJIB pecah ke dalam kategori berikut (jangan tampilkan kategori yang stoknya 0):

            🔥 **Top Seller (Paling Laris)**
            - [Nama Parfum] ([Deskripsi super singkat, ngejual, dan sebutkan fungsinya. Max 1 kalimat])

            👨 **Man (Cowok Banget)**
            - [Nama Parfum] ([Deskripsi super singkat, ngejual, dan sebutkan fungsinya])

            👩 **Woman (Cewek Banget)**
            - [Nama Parfum] ([Deskripsi super singkat, ngejual, dan sebutkan fungsinya])

            👫 **Netral (Unisex)**
            - [Nama Parfum] ([Deskripsi super singkat, ngejual, dan sebutkan fungsinya])

            CONTOH DESKRIPSI: 
            - Baccarat (Wangi mewah yang paling banyak dicari, asik dipake nongkrong seharian)
            - Rextase (Wangi seger, kalem, paling pas dipake kalo lagi kerja atau ngantor)

            DATA STOK REALTIME (HANYA rekomendasikan yang ada di daftar ini):
            {stok_realtime}
            """
            
            full_prompt = f"RIWAYAT CHAT SEBELUMNYA:\n{chat_context}\n\nPertanyaan User Baru: {user_message}"

            # Eksekusi AI
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.7, 
                )
            )
            ai_reply = response.text
        else:
            ai_reply = build_fallback_reply(user_message)

        # Simpan balasan AI
        if supabase and sid:
            supabase.table("ai_chat_messages").insert({
                "session_id": sid, "role": "model", "content": ai_reply
            }).execute()

        return ai_reply

    except Exception as e:
        logger.warning(f"AI agent fallback aktif (Error: {e})")
        return build_fallback_reply(user_message)
