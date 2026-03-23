import os
import logging

from database import supabase

logger = logging.getLogger("baba.ai")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
model = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
    except Exception as exc:
        logger.warning("Library Gemini tidak tersedia: %s", exc)


async def get_or_create_session(tele_id: int):
    """Mencari sesi aktif atau membuat baru di database."""
    if not supabase:
        return None

    res = supabase.table("ai_chat_sessions").select("id").eq("telegram_id", tele_id).eq("is_active", True).execute()
    if res.data:
        return res.data[0]["id"]

    new_sess = supabase.table("ai_chat_sessions").insert({"telegram_id": tele_id}).execute()
    return new_sess.data[0]["id"]


async def get_perfume_knowledge_base():
    """Mengambil data stok terupdate sebagai panduan AI."""
    if not supabase:
        return ""

    res = supabase.table("products").select(
        "name, tags, tagline, discounted_price, stock_quantity, top_notes, heart_notes, base_notes"
    ).eq("is_active", True).gt("stock_quantity", 0).execute()

    katalog = ""
    for p in (res.data or []):
        tags_str = ", ".join(p["tags"]) if isinstance(p.get("tags"), list) else str(p.get("tags") or "-")
        katalog += (
            f"- {p.get('name', 'Tanpa Nama')}: {p.get('tagline', '-')}. "
            f"Cocok untuk: {tags_str}. "
            f"Wangi: {p.get('top_notes', [])} (awal), {p.get('heart_notes', [])} (tengah). "
            f"Harga: ${p.get('discounted_price', 0)}. Stok: {p.get('stock_quantity', 0)} pcs.\n"
        )
    return katalog


def build_fallback_reply(user_message: str) -> str:
    lowered = user_message.lower()
    if any(keyword in lowered for keyword in ["cowok", "man", "maskulin"]):
        vibe = "cari varian dengan nuansa fresh, woody, atau aromatic"
    elif any(keyword in lowered for keyword in ["cewek", "woman", "feminin"]):
        vibe = "cari varian dengan nuansa floral, fruity, atau sweet"
    else:
        vibe = "pilih parfum berdasarkan karakter wangi yang kakak suka"

    return (
        "Halo kak, sistem rekomendasi realtime lagi pakai mode aman dulu. "
        f"Saran awalnya, {vibe}. "
        "Kalau kakak mau, sebutkan target aroma, budget, dan acara pemakaian; nanti sistem akan bantu arahkan lagi tanpa mengganggu checkout."
    )


async def get_ai_recommendation(tele_id: int, user_message: str):
    """Fungsi utama untuk memproses chat user dengan otak Gemini."""
    try:
        sid = await get_or_create_session(tele_id)

        if supabase and sid:
            supabase.table("ai_chat_messages").insert({
                "session_id": sid, "role": "user", "content": user_message
            }).execute()

        chat_context = ""
        if supabase and sid:
            history_res = supabase.table("ai_chat_messages").select("role, content").eq("session_id", sid).order("created_at", desc=False).limit(10).execute()
            for h in (history_res.data or []):
                role_name = "User" if h["role"] == "user" else "AI"
                chat_context += f"{role_name}: {h['content']}\n"

        stok_realtime = await get_perfume_knowledge_base()

        if model and stok_realtime:
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
            response = model.generate_content(system_prompt + f"\nPertanyaan User Baru: {user_message}")
            ai_reply = response.text
        else:
            ai_reply = build_fallback_reply(user_message)

        if supabase and sid:
            supabase.table("ai_chat_messages").insert({
                "session_id": sid, "role": "model", "content": ai_reply
            }).execute()

        return ai_reply

    except Exception as e:
        logger.warning("AI agent fallback aktif: %s", e)
        return build_fallback_reply(user_message)
