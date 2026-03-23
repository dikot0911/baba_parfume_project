import os
import google.generativeai as genai
from database import supabase
from datetime import datetime

# Konfigurasi Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Model flash biar respon secepat kilat
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    print("⚠️ [AI AGENT] API Key Gemini belum dipasang di .env!")

async def get_or_create_session(tele_id: int):
    """Mencari sesi aktif atau membuat baru di database"""
    res = supabase.table("ai_chat_sessions").select("id").eq("telegram_id", tele_id).eq("is_active", True).execute()
    if res.data:
        return res.data[0]['id']
    else:
        new_sess = supabase.table("ai_chat_sessions").insert({"telegram_id": tele_id}).execute()
        return new_sess.data[0]['id']

async def get_perfume_knowledge_base():
    """Mengambil data stok terupdate sebagai panduan AI"""
    res = supabase.table("products").select(
        "name, tags, tagline, discounted_price, stock_quantity, top_notes, heart_notes, base_notes"
    ).eq("is_active", True).gt("stock_quantity", 0).execute()
    
    katalog = ""
    for p in (res.data or []):
        tags_str = ", ".join(p['tags']) if isinstance(p['tags'], list) else str(p['tags'])
        katalog += (
            f"- {p['name']}: {p['tagline']}. "
            f"Cocok untuk: {tags_str}. "
            f"Wangi: {p['top_notes']} (awal), {p['heart_notes']} (tengah). "
            f"Harga: ${p['discounted_price']}. Stok: {p['stock_quantity']} pcs.\n"
        )
    return katalog

async def get_ai_recommendation(tele_id: int, user_message: str):
    """Fungsi utama untuk memproses chat user dengan otak Gemini"""
    try:
        sid = await get_or_create_session(tele_id)
        
        # 1. Simpan pesan user ke database
        supabase.table("ai_chat_messages").insert({
            "session_id": sid, "role": "user", "content": user_message
        }).execute()

        # 2. Tarik Riwayat Chat (Memory) biar AI ingat obrolan sebelumnya
        history_res = supabase.table("ai_chat_messages").select("role, content").eq("session_id", sid).order("created_at", desc=False).limit(10).execute()
        chat_context = ""
        for h in (history_res.data or []):
            role_name = "User" if h['role'] == 'user' else "AI"
            chat_context += f"{role_name}: {h['content']}\n"

        # 3. Tarik Data Produk (Knowledge Base)
        stok_realtime = await get_perfume_knowledge_base()

        # 4. Rakit System Prompt yang Gacor
        system_prompt = f"""
        Kamu adalah 'BABA AI Expert', konsultan parfum profesional dari BABA Parfume.
        Tugasmu: Memberikan rekomendasi parfum terbaik berdasarkan stok yang tersedia.

        DATA STOK REALTIME (Hanya boleh rekomendasikan yang ada di sini):
        {stok_realtime}

        ATURAN KOMUNIKASI:
        1. Gunakan bahasa santai tapi sopan, panggil 'kak'.
        2. Jika user ingin parfum yang stoknya 0 atau tidak ada di list, katakan sedang kosong dan tawarkan yang mirip.
        3. Jelaskan piramida aroma (top/heart/base notes) biar user tergiur.
        4. Jangan pernah menyarankan produk yang tidak ada di list DATA STOK.
        5. Lihat riwayat chat di bawah untuk memahami konteks.

        RIWAYAT CHAT SEBELUMNYA:
        {chat_context}
        """

        # 5. Eksekusi ke Gemini
        response = model.generate_content(system_prompt + f"\nPertanyaan User Baru: {user_message}")
        ai_reply = response.text

        # 6. Simpan jawaban AI ke database
        supabase.table("ai_chat_messages").insert({
            "session_id": sid, "role": "model", "content": ai_reply
        }).execute()

        return ai_reply

    except Exception as e:
        print(f"❌ [AI AGENT ERROR]: {e}")
        return "Aduh kak, sistem AI BABA lagi agak pusing nih. Coba tanya lagi ya!"
