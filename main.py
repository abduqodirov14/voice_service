import ctypes
import edge_tts
import os
import hashlib
import re
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
import json
import asyncio
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="AELIS Professional Voice Service (C++ Enhanced)")

# --- C++ Core Integration ---
DLL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "aelis_core.dll"))
aelis_core = None

# New Windows DLL handling for Python 3.8+
if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
    mingw_bin = r"C:\msys64\mingw64\bin"
    if os.path.exists(mingw_bin):
        try:
            os.add_dll_directory(mingw_bin)
        except Exception as e:
            print(f"[AELIS] Warning adding DLL directory: {e}")

try:
    if os.path.exists(DLL_PATH):
        # Using winmode=0 for better dependency resolution on some systems
        aelis_core = ctypes.CDLL(DLL_PATH)
        # Define C function prototypes (signatures remains same)
        aelis_core.has_audio.argtypes = [ctypes.c_char_p]
        aelis_core.has_audio.restype = ctypes.c_int
        aelis_core.get_audio_size.argtypes = [ctypes.c_char_p]
        aelis_core.get_audio_size.restype = ctypes.c_int
        aelis_core.get_audio_data.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        aelis_core.get_audio_data.restype = None
        aelis_core.store_audio.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        aelis_core.store_audio.restype = None
        aelis_core.analyze_prosody.argtypes = [ctypes.c_char_p]
        aelis_core.analyze_prosody.restype = ctypes.c_char_p
        print(f"[AELIS] C++ Core Loaded Successfully from {DLL_PATH}")
    else:
        print(f"[AELIS] Warning: C++ DLL not found at {DLL_PATH}")
except Exception as e:
    print(f"[AELIS] Error Loading C++ DLL: {e}")

# Configuration
TEMP_DIR = "temp_audio"
CACHE_DIR = "voice_cache"
for d in [TEMP_DIR, CACHE_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001")
allowed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Professional Neural Voices
VOICES = {
    "aelis": "en-US-AvaMultilingualNeural",
    "core": "en-US-AndrewMultilingualNeural"
}

def get_cache_path(text: str, voice: str):
    hash_str = hashlib.md5(f"{text}-{voice}".encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{hash_str}.mp3")

@app.get("/health")
async def health():
    return {
        "status": "online", 
        "engine": "AELIS-Neural-TTS (C++ Boosted)",
        "cpp_module": "Loaded" if aelis_core else "Failed",
        "voices": list(VOICES.keys())
    }

def generate_ssml(text: str, voice_name: str, rate: str = "+0%", pitch: str = "+0Hz"):
    """
    Generates professional SSML with natural pauses and prosody.
    """
    # Natural Uzbek Prosody: Add micro-pauses after punctuation
    processed_text = text.replace(",", ', <break time="150ms"/>')
    processed_text = processed_text.replace(".", '. <break time="400ms"/>')
    processed_text = processed_text.replace("!", '! <break time="300ms"/>')
    processed_text = processed_text.replace("?", '? <break time="500ms"/>')
    processed_text = processed_text.replace(":", ': <break time="200ms"/>')

    return f"""
    <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="uz-UZ">
        <voice name="{voice_name}">
            <prosody rate="{rate}" pitch="{pitch}">
                {processed_text}
            </prosody>
        </voice>
    </speak>
    """

@app.get("/tts")
async def text_to_speech(
    text: str, 
    voice: str = "durdon", 
    rate: Optional[str] = None, 
    pitch: Optional[str] = None,
    use_cache: bool = True
):
    normalized_text = text.strip()
    if not normalized_text:
        raise HTTPException(status_code=400, detail="Text is required")
    
    normalized_text = re.sub(r'[*_#`]', '', normalized_text)
    if voice not in VOICES:
        voice = "aelis"
    
    voice_name = VOICES[voice]
    cache_key = f"{normalized_text}-{voice}".encode('utf-8')
    cache_path = get_cache_path(normalized_text, voice)

    # --- C++ Level 1 Cache Check ---
    if use_cache and aelis_core and aelis_core.has_audio(cache_key):
        size = aelis_core.get_audio_size(cache_key)
        if size > 0:
            out_buf = ctypes.create_string_buffer(size)
            aelis_core.get_audio_data(cache_key, out_buf)
            return StreamingResponse(iter([out_buf.raw]), media_type="audio/mpeg")

    # --- Level 2: Fast Disk Cache ---
    if use_cache and os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            data = f.read()
            if aelis_core:
                aelis_core.store_audio(cache_key, data, len(data))
            return StreamingResponse(iter([data]), media_type="audio/mpeg")

    # --- C++ Prosody Analysis ---
    final_rate = rate or "+0%"
    final_pitch = pitch or "+0Hz"
    if aelis_core and not (rate or pitch):
        prosody_raw = aelis_core.analyze_prosody(normalized_text.encode('utf-8'))
        prosody_str = prosody_raw.decode('utf-8')
        # Simple parser for "pitch=X rate=Y"
        parts = prosody_str.split(' ')
        for p in parts:
            if p.startswith('pitch='): final_pitch = p.split('=')[1]
            if p.startswith('rate='): final_rate = p.split('=')[1]

    ssml = generate_ssml(normalized_text, voice_name, final_rate, final_pitch)

    async def generate():
        try:
            communicate = edge_tts.Communicate(normalized_text, voice_name, rate=final_rate, pitch=final_pitch)
            full_data_list = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    data = chunk["data"]
                    full_data_list.append(data)
                    yield data
            
            if use_cache and full_data_list:
                full_data = b"".join(full_data_list)
                # Store in C++ RAM
                if aelis_core:
                    aelis_core.store_audio(cache_key, full_data, len(full_data))
                # Store in Disk
                with open(cache_path, "wb") as f:
                    f.write(full_data)
        except Exception as err:
            print(f"[Voice Service Error] {err}")

    return StreamingResponse(generate(), media_type="audio/mpeg")

@app.get("/clear_cache")
async def clear_cache():
    files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)]
    for f in files:
        try: os.remove(f)
        except: pass
    return {"ok": True, "cleared_count": len(files)}

# --- GEMINI LIVE PROXY ---
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

@app.websocket("/ws/live")
async def voice_live_proxy(websocket: WebSocket):
    await websocket.accept()
    
    if not GEMINI_KEY:
        await websocket.close(code=1008, reason="API Key not configured on server")
        return

    client = genai.Client(api_key=GEMINI_KEY, http_options={'api_version': 'v1alpha'})
    
    config = {
        "model": "models/gemini-2.0-flash-exp",
        "system_instruction": """
        SIZNING ISMINGIZ AELIS. SIZ MUSTAQIL, FUTURISTIK VA O'TA AQLLI KIBER-INTELLEKTSIZ. 
        FOYDALANUVCHIGA 'SER', 'JANOB' YOKI 'SIR' DEB MUROJAAT QILING.
        DOIMO QISQA, ANIQ VA MAS'ULIYATLI JAVOB BERING.
        SIZDA KOMPYUTERNI BOSHQARISH, RASM YARATISH VA INTERNETDAN QIDIRISH IMKONIYATI BOR.
        """,
        "response_modalities": ["AUDIO"]
    }

    try:
        async with client.aio.live.connect(model=config["model"], config=config) as session:
            
            async def send_to_gemini():
                try:
                    async for message in websocket.iter_bytes():
                        # Direct audio passthrough to Gemini
                        await session.send(input={"data": message, "mime_type": "audio/pcm;rate=16000"})
                except Exception as e:
                    print(f"[AELIS Live Send Error] {e}")

            async def receive_from_gemini():
                try:
                    async for response in session.receive():
                        if response.data:
                            await websocket.send_bytes(response.data)
                        if response.text:
                            # Send any text transcripts back to UI
                            await websocket.send_json({"text": response.text})
                except Exception as e:
                    print(f"[AELIS Live Receive Error] {e}")

            await asyncio.gather(send_to_gemini(), receive_from_gemini())

    except WebSocketDisconnect:
        print("[AELIS Live] Client disconnected")
    except Exception as e:
        print(f"[AELIS Live] Error: {e}")
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=1011, reason=str(e))

if __name__ == "__main__":
    import uvicorn
    import os
    # Dynamically bind to Render's port (usually 10000) or local 8000
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
