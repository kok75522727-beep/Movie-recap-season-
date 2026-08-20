import base64
import os
from io import BytesIO
import subprocess
import tempfile
import wave
from pathlib import Path

import streamlit as st
from google import genai
from PIL import Image
from streamlit_drawable_canvas import st_canvas

st.set_page_config(page_title="RecapLab · Gemini Movie Recap", page_icon="🎬", layout="wide")

VOICE_OPTIONS = {
    "Kore": "Calm narrator",
    "Puck": "Bright and energetic",
    "Aoede": "Expressive storyteller",
    "Charon": "Deep cinematic narrator",
    "Fenrir": "Dramatic narrator",
    "Enceladus": "Warm and breathy",
    "Orus": "Clear and steady",
    "Schedar": "Confident presenter",
}
LANGUAGES = ["Burmese (မြန်မာ)", "English", "Thai", "Indonesian", "Vietnamese"]


def get_api_key() -> str:
    try:
        secret_key = st.secrets.get("GOOGLE_AI_API_KEY", "")
    except Exception:
        secret_key = ""
    return st.session_state.get("google_ai_key") or secret_key or os.getenv("GOOGLE_AI_API_KEY", "")


def get_client() -> genai.Client:
    api_key = get_api_key().strip()
    if not api_key:
        raise ValueError("Google AI Studio API key မထည့်ရသေးပါ။ API Settings ထဲမှာ ထည့်ပါ။")
    return genai.Client(api_key=api_key)


def api_error_message(error: Exception) -> str:
    message = str(error)
    lowered = message.lower()
    quota_error = any(token in lowered for token in ["429", "quota", "resource_exhausted", "rate limit", "too many requests"])
    key_error = any(token in lowered for token in ["401", "403", "api key", "unauthorized", "permission denied", "invalid_argument"])
    if quota_error:
        st.session_state.pop("google_ai_key", None)
        return "Gemini API quota ပြည့်သွားပါပြီ။ API Key အသစ်ထည့်ပါ သို့မဟုတ် Quota ပြန်ရသည်အထိ စောင့်ပါ။"
    if key_error:
        st.session_state.pop("google_ai_key", None)
        return "Gemini API Key မမှန်ပါ သို့မဟုတ် Permission မရှိပါ။ API Key အသစ်ပြန်ထည့်ပါ။"
    return f"Gemini API Error: {message}"


def save_upload(uploaded_file) -> Path:
    suffix = Path(uploaded_file.name).suffix or ".mp4"
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(uploaded_file.getbuffer())
    handle.close()
    return Path(handle.name)


def get_video_duration(video_path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return max(1, round(float(result.stdout.strip())))
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return None


def parse_duration_input(value: str) -> int:
    cleaned = value.strip().lower().replace(".", ":")
    if ":" in cleaned:
        parts = cleaned.split(":")
        if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
            raise ValueError("အချိန်ကို 1:18 သို့မဟုတ် 0:45 ပုံစံနဲ့ ထည့်ပါ။")
        minutes, seconds = (int(part) for part in parts)
        if seconds >= 60:
            raise ValueError("စက္ကန့်ကို 00 မှ 59 အတွင်း ထည့်ပါ။")
        total = minutes * 60 + seconds
    elif cleaned.isdigit():
        total = int(cleaned)
    else:
        raise ValueError("အချိန်ကို 1:18 သို့မဟုတ် 1.18 ပုံစံနဲ့ ထည့်ပါ။")
    if total < 5:
        raise ValueError("Recap အရှည် အနည်းဆုံး 5 seconds ဖြစ်ရပါမယ်။")
    return total


def format_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def generate_recap_script(video_path: Path, language: str, duration_seconds: int, tone: str, mode: str) -> str:
    client = get_client()
    uploaded = client.files.upload(file=str(video_path))
    if mode == "Faithful full translation":
        prompt = f"""
Watch the uploaded video and translate ALL spoken dialogue and narration into {language}.
This is a faithful translation mode: do not summarize, shorten, skip, reorder, or invent anything.
Preserve every meaningful sentence and event in the original order. Translate naturally and clearly for a native {language} speaker.
Keep speaker changes and paragraph breaks when they are apparent. Do not add commentary, headings, timestamps, subtitles, or explanations.
If a word is unclear, mark it as [မရှင်းလင်း] rather than inventing content.
Return only the complete natural translation.
"""
    else:
        prompt = f"""
You are a professional movie recap editor. Watch the uploaded video and write a concise original narration in {language}.
Target length: approximately {duration_seconds} seconds. Tone: {tone}.

Important originality and safety rules:
- Do not copy dialogue, subtitles, or any source narration word-for-word.
- Do not quote long passages.
- Paraphrase the events in your own words and focus on commentary, sequence, cause-and-effect, and character decisions.
- Do not invent scenes that are not visible or inferable from the video.
- Return only the narration script, without headings, markdown, timestamps, or subtitles.
"""
    interaction = client.interactions.create(
        model=st.session_state.get("gemini_text_model", "gemini-3.7-flash"),
        input=[
            {"type": "text", "text": prompt},
            {"type": "video", "uri": uploaded.uri, "mime_type": uploaded.mime_type},
        ],
        generation_config={"temperature": 0.65, "thinking_level": "low"},
    )
    text = getattr(interaction, "output_text", None)
    if not text:
        raise RuntimeError("Gemini က Script မပြန်ပေးပါ။")
    return text.strip()


def generate_voiceover(text: str, voice: str, style: str) -> bytes:
    client = get_client()
    prompt = f"Read this narration in a {style} style. Speak clearly and naturally, with short pauses at punctuation.\n\n{text}"
    interaction = client.interactions.create(
        model=st.session_state.get("gemini_tts_model", "gemini-3.1-flash-tts-preview"),
        input=prompt,
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": voice}]},
    )
    audio = getattr(interaction, "output_audio", None)
    encoded = getattr(audio, "data", None) if audio else None
    if not encoded:
        raise RuntimeError("Gemini က Audio မပြန်ပေးပါ။")
    return base64.b64decode(encoded)


def pcm_to_wav(pcm: bytes) -> bytes:
    output = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    output.close()
    with wave.open(output.name, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(pcm)
    data = Path(output.name).read_bytes()
    Path(output.name).unlink(missing_ok=True)
    return data


def extract_preview_frame(video_path: Path) -> Image.Image:
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", "0", "-i", str(video_path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        capture_output=True,
        timeout=60,
        check=True,
    )
    return Image.open(BytesIO(result.stdout)).convert("RGB")


def get_video_dimensions(video_path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(video_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    width, height = result.stdout.strip().split("x")
    return int(width), int(height)


def apply_region_blur(video_path: Path, box: tuple[int, int, int, int]) -> Path:
    x, y, width, height = box
    output_path = Path(tempfile.mktemp(suffix="-blurred.mp4"))
    width = max(2, width - (width % 2))
    height = max(2, height - (height % 2))
    filter_graph = f"[0:v]split=2[base][blur];[blur]crop={width}:{height}:{x}:{y},boxblur=18:2[blurred];[base][blurred]overlay={x}:{y}[v]"
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "copy", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-1200:])
    return output_path


def merge_audio_video(video_path: Path, audio_bytes: bytes) -> bytes:
    audio_path = Path(tempfile.mktemp(suffix=".wav"))
    output_path = Path(tempfile.mktemp(suffix=".mp4"))
    audio_path.write_bytes(pcm_to_wav(audio_bytes))
    video_duration = get_video_duration(video_path)
    if not video_duration:
        raise RuntimeError("Original video duration ကို မဖတ်နိုင်ပါ။")
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
        "-af", "apad", "-t", str(video_duration), str(output_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(result.stderr[-1000:])
        return output_path.read_bytes()
    finally:
        audio_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def apply_cinematic_theme():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
        :root { --coral:#ff4f67; --violet:#8c6cff; --ink:#f7f7fb; --muted:#a9adbd; --panel:rgba(20,22,34,.78); }
        .stApp { background: radial-gradient(circle at 5% 0%, rgba(140,108,255,.22), transparent 30%), radial-gradient(circle at 95% 10%, rgba(255,79,103,.14), transparent 25%), #080910; color:var(--ink); font-family:'DM Sans',sans-serif; }
        .stApp::before { content:''; position:fixed; inset:0; pointer-events:none; opacity:.12; background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px); background-size:48px 48px; mask-image:linear-gradient(to bottom,black,transparent 78%); }
        h1,h2,h3 { font-family:'Space Grotesk',sans-serif !important; letter-spacing:-.04em; }
        h1 { font-size:clamp(2.2rem,6vw,4.8rem) !important; background:linear-gradient(100deg,#fff 20%,#ff9a9f 58%,#9d8dff 90%); -webkit-background-clip:text; color:transparent; margin-bottom:.2rem !important; }
        h2 { color:#fff !important; }
        [data-testid='stHeader'] { background:rgba(8,9,16,.72); }
        [data-testid='stSidebar'] { background:linear-gradient(180deg,rgba(20,22,35,.96),rgba(11,12,20,.98)); border-right:1px solid rgba(255,255,255,.09); }
        [data-testid='stSidebar'] h2 { font-size:1.3rem !important; }
        [data-testid='stExpander'] { background:linear-gradient(145deg,rgba(41,37,65,.72),rgba(19,21,32,.72)); border:1px solid rgba(255,255,255,.12); border-radius:20px; box-shadow:0 20px 60px rgba(0,0,0,.22); }
        [data-testid='stFileUploader'] { background:linear-gradient(145deg,rgba(42,37,63,.6),rgba(19,21,31,.72)); border:1px dashed rgba(255,111,126,.55); border-radius:20px; padding:10px; box-shadow:0 12px 40px rgba(0,0,0,.22); }
        [data-testid='stFileUploader'] section { background:transparent; border:0; }
        [data-testid='stFileUploaderDropzone'] { background:rgba(255,255,255,.025); border-radius:14px; }
        .stButton > button { width:100%; border:1px solid rgba(255,255,255,.14); border-radius:12px; padding:.72rem 1rem; color:#fff; background:linear-gradient(135deg,rgba(255,79,103,.95),rgba(132,76,255,.92)); box-shadow:0 10px 28px rgba(255,79,103,.18); font-weight:700; transition:transform .18s ease, box-shadow .18s ease; }
        .stButton > button:hover { transform:translateY(-2px); box-shadow:0 14px 34px rgba(255,79,103,.3); border-color:rgba(255,255,255,.35); }
        .stButton > button:active { transform:scale(.98); }
        .stDownloadButton > button { width:100%; border-radius:12px; color:#ffdce0; background:rgba(255,79,103,.12); border:1px solid rgba(255,79,103,.38); }
        .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb='select'] > div, .stNumberInput input { color:#fff !important; background:rgba(8,9,16,.72) !important; border:1px solid rgba(255,255,255,.13) !important; border-radius:11px !important; }
        .stTextArea textarea:focus, .stTextInput input:focus { border-color:var(--coral) !important; box-shadow:0 0 0 1px var(--coral) !important; }
        [data-testid='stAlert'] { border-radius:14px; border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.06); }
        [data-testid='stMetric'] { background:rgba(255,255,255,.055); border:1px solid rgba(255,255,255,.09); border-radius:15px; padding:12px; }
        .recap-hero { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:26px 28px; margin:8px 0 22px; border:1px solid rgba(255,255,255,.11); border-radius:24px; background:linear-gradient(120deg,rgba(49,37,83,.84),rgba(28,23,42,.64) 52%,rgba(75,27,41,.42)); box-shadow:0 24px 70px rgba(0,0,0,.28); position:relative; overflow:hidden; }
        .recap-hero::after { content:'✦  REC  /  01'; position:absolute; right:24px; bottom:14px; color:rgba(255,255,255,.25); letter-spacing:.18em; font-size:.7rem; }
        .hero-kicker { color:#ff8b9b; text-transform:uppercase; letter-spacing:.2em; font-size:.7rem; font-weight:700; margin-bottom:8px; }
        .hero-copy { color:#b9b9ca; margin:0; max-width:580px; }
        .hero-orb { width:74px; height:74px; flex:none; display:grid; place-items:center; border-radius:23px; color:#fff; font-size:2rem; background:linear-gradient(145deg,var(--coral),var(--violet)); box-shadow:0 0 45px rgba(255,79,103,.36); transform:rotate(-8deg); }
        .section-label { color:#ff8b9b; font-weight:700; letter-spacing:.14em; font-size:.72rem; text-transform:uppercase; margin:18px 0 8px; }
        @media (max-width:700px) { .recap-hero { padding:20px; } .hero-orb { width:54px; height:54px; border-radius:17px; font-size:1.4rem; } .recap-hero::after { display:none; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    apply_cinematic_theme()
    st.markdown(
        """
        <div class='recap-hero'>
          <div><div class='hero-kicker'>AI POST-PRODUCTION / GEMINI WORKSPACE</div><h1>RecapLab</h1><p class='hero-copy'>Turn a movie into a sharper story with natural translation, cinematic narration, and a clean final cut.</p></div>
          <div class='hero-orb'>🎬</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Gemini-powered movie recap studio · subtitles are intentionally disabled in this version")

    with st.expander("🔐 Gemini API Settings — API Key ကို ဒီမှာထည့်ပါ", expanded=not bool(get_api_key())):
        st.markdown("Google AI Studio မှာ Key ယူရန် [ဒီနေရာကိုဖွင့်ပါ](https://aistudio.google.com/app/apikey)")
        st.caption("Key ကို ဒီ Session အတွင်းပဲ အသုံးပြုမယ်။ Code၊ GitHub၊ URL သို့မဟုတ် Browser localStorage ထဲ မသိမ်းပါ။")
        key = st.text_input("Google AI Studio API Key", type="password", value=st.session_state.get("google_ai_key", ""), placeholder="AIza...", help="Google AI Studio API key ကို ဒီမှာ paste လုပ်ပါ")
        if key.strip():
            st.session_state.google_ai_key = key.strip()
            st.session_state.api_key_status = "saved_for_session"
        if get_api_key():
            st.success("API Key ကို ဒီ Session အတွင်း မှတ်ထားပြီး Quota ပြည့်မှသာ ပြန်ထည့်ရန် တောင်းပါမယ်။")
        settings_left, settings_right = st.columns([1, 1])
        with settings_left:
            st.session_state.gemini_text_model = st.text_input("Gemini text model", value=st.session_state.get("gemini_text_model", "gemini-3.7-flash"))
            st.session_state.gemini_tts_model = st.text_input("Gemini TTS model", value=st.session_state.get("gemini_tts_model", "gemini-3.1-flash-tts-preview"))
        with settings_right:
            if st.button("Test Gemini API", use_container_width=True):
                try:
                    client = get_client()
                    result = client.interactions.create(model=st.session_state.gemini_text_model, input="Reply with the single word: READY")
                    st.success(f"Connected: {getattr(result, 'output_text', 'READY')}")
                except Exception as exc:
                    st.error(api_error_message(exc))
            if st.button("Clear session key", use_container_width=True):
                st.session_state.pop("google_ai_key", None)
                st.rerun()
        st.info("Streamlit Cloud သုံးရင် Settings → Secrets ထဲမှာ GOOGLE_AI_API_KEY ထည့်နိုင်ပါတယ်။ App ထဲမှာထည့်တဲ့ Key က ယာယီ Session Key ဖြစ်ပါတယ်။")

    upload = st.file_uploader("Video ထည့်ပါ", type=["mp4", "mov", "mkv", "avi", "webm"])
    if not upload:
        st.warning("Video ဖိုင်တစ်ခု ထည့်ပါ။")
        st.stop()

    if "video_path" not in st.session_state or st.session_state.get("video_name") != upload.name:
        st.session_state.video_path = save_upload(upload)
        st.session_state.video_name = upload.name
        st.session_state.script = ""
        st.session_state.audio = None
        st.session_state.blurred_video_path = None
        st.session_state.output_video = None

    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Original video")
        st.video(upload)
        st.caption(f"{upload.name} · {upload.size / 1024 / 1024:.1f} MB")

    with right:
        st.subheader("1 · ပြန်ရေးမည့် ဘာသာစကား")
        language = st.selectbox("Language", LANGUAGES, label_visibility="collapsed")
        mode = st.selectbox("လုပ်ဆောင်မည့်ပုံစံ", ["Faithful full translation", "Original recap"], help="Faithful mode က အကြောင်းအရာအားလုံးကို မကျန်အောင် သဘာဝကျကျ ဘာသာပြန်ပေးမယ်။ Original recap က အကျဉ်းချုပ် Script အသစ်ရေးပေးမယ်။")
        video_duration = get_video_duration(st.session_state.video_path)
        duration_valid = True
        if video_duration:
            st.caption(f"Video အရှည်: {format_duration(video_duration)}")
            if mode == "Faithful full translation":
                duration_seconds = video_duration
                st.info("Faithful Translation Mode: Video ထဲက အကြောင်းအရာအားလုံးကို မကျန်အောင် ပြန်ပေးမယ်။")
            else:
                default_duration = min(60, video_duration)
                duration_text = st.text_input("Recap အရှည် (mm:ss)", value=format_duration(default_duration), help="ဥပမာ 1:18 သို့မဟုတ် 1.18 ထည့်ပါ။ Video အရှည်ထက် မကျော်ရပါ။")
                try:
                    duration_seconds = parse_duration_input(duration_text)
                    if duration_seconds > video_duration:
                        duration_valid = False
                        st.error(f"Recap အရှည်က Video အရှည် {format_duration(video_duration)} ထက် မကျော်ရပါ။")
                except ValueError as exc:
                    duration_valid = False
                    duration_seconds = 0
                    st.error(str(exc))
        else:
            st.warning("Video အရှည်ကို မဖတ်နိုင်ပါ။ FFmpeg/FFprobe ကို စစ်ပါ။")
            duration_text = st.text_input("Recap အရှည် (mm:ss)", value="1:00", help="ဥပမာ 1:18 သို့မဟုတ် 1.18 ထည့်ပါ။")
            try:
                duration_seconds = parse_duration_input(duration_text)
            except ValueError as exc:
                duration_valid = False
                duration_seconds = 0
                st.error(str(exc))
        tone = st.selectbox("Script style", ["Cinematic and concise", "Fast TikTok style", "Calm documentary", "Dramatic storyteller"])
        if st.button("Gemini နဲ့ Script ပြန်ရေးမယ်", type="primary", use_container_width=True):
            if not duration_valid:
                st.warning("အရင်ဆုံး Recap အရှည်ကို မှန်ကန်အောင် ထည့်ပါ။")
                st.stop()
            progress_message = "Video ကို Gemini က သုံးသပ်ပြီး အကြောင်းအရာအားလုံးကို သဘာဝကျကျ ဘာသာပြန်နေပါတယ်..." if mode == "Faithful full translation" else "Video ကို Gemini က သုံးသပ်ပြီး Copy မဖြစ်အောင် Script ပြန်ရေးနေပါတယ်..."
            with st.spinner(progress_message):
                try:
                    st.session_state.script = generate_recap_script(st.session_state.video_path, language, int(duration_seconds), tone, mode)
                    st.session_state.audio = None
                    st.success("အကြောင်းအရာအပြည့်အစုံ သဘာဝကျကျ ဘာသာပြန်ပြီးပါပြီ။" if mode == "Faithful full translation" else "Original recap script ရပါပြီ။")
                except Exception as exc:
                    st.error(api_error_message(exc))

    if st.session_state.get("script"):
        st.divider()
        st.subheader("2 · Script ကို စစ်ပြီး ပြင်ပါ")
        st.caption("ဒီ Version မှာ Subtitle မထည့်သေးပါ။ Script ကို ကိုယ်တိုင်ပြင်ပြီး အသံထုတ်နိုင်ပါတယ်။")
        st.session_state.script = st.text_area("Editable narration", st.session_state.script, height=230)
        st.download_button("Script ဒေါင်းရန်", st.session_state.script, file_name="recap-script.txt", mime="text/plain")

        st.divider()
        st.subheader("3 · Video ထဲက စာတန်းကို လက်နဲ့ရွေးပြီး Blur လုပ်ပါ")
        st.caption("Video ပုံပေါ်မှာ ဖျောက်ချင်တဲ့စာတန်းနေရာကို လက်နဲ့ rectangle ဆွဲပါ။")
        try:
            preview_frame = extract_preview_frame(st.session_state.video_path)
            preview_width = min(720, preview_frame.width)
            preview_height = max(240, round(preview_frame.height * preview_width / preview_frame.width))
            canvas_result = st_canvas(
                fill_color="rgba(255, 65, 95, 0.28)",
                stroke_width=3,
                stroke_color="#ff4f67",
                background_image=preview_frame.resize((preview_width, preview_height)),
                update_streamlit=True,
                height=preview_height,
                width=preview_width,
                drawing_mode="rect",
                key=f"blur-canvas-{st.session_state.video_name}",
            )
            if st.button("ရွေးထားတဲ့နေရာကို Blur လုပ်ပြီး ဆက်မယ်", type="primary", use_container_width=True):
                objects = (canvas_result.json_data or {}).get("objects", [])
                rectangles = [item for item in objects if item.get("type") == "rect"]
                if not rectangles:
                    st.warning("ဖျောက်ချင်တဲ့နေရာကို အရင် rectangle ဆွဲပါ။")
                else:
                    selected = rectangles[-1]
                    original_width, original_height = get_video_dimensions(st.session_state.video_path)
                    scale_x = original_width / preview_width
                    scale_y = original_height / preview_height
                    x = max(0, round(float(selected.get("left", 0)) * scale_x))
                    y = max(0, round(float(selected.get("top", 0)) * scale_y))
                    width = round(float(selected.get("width", 0)) * scale_x)
                    height = round(float(selected.get("height", 0)) * scale_y)
                    width = min(width, original_width - x)
                    height = min(height, original_height - y)
                    if width < 4 or height < 4:
                        st.warning("Blur နေရာက အရမ်းသေးနေပါတယ်။ ပိုကြီးတဲ့ rectangle ဆွဲပါ။")
                    else:
                        with st.spinner("ရွေးထားတဲ့စာတန်းနေရာကို Blur လုပ်နေပါတယ်..."):
                            try:
                                st.session_state.blurred_video_path = str(apply_region_blur(st.session_state.video_path, (x, y, width, height)))
                                st.session_state.audio = None
                                st.success("Video စာတန်း Blur ပြီးပါပြီ။ အခု အသံရွေးနိုင်ပါပြီ။")
                            except Exception as exc:
                                st.error(f"Blur မအောင်မြင်ပါ: {exc}")
        except Exception as exc:
            st.error(f"Video frame/canvas မဖွင့်နိုင်ပါ: {exc}")

        if st.session_state.get("blurred_video_path"):
            st.video(st.session_state.blurred_video_path)
            st.caption("Blur ပြီးသား Video Preview")
            st.divider()
            st.subheader("4 · Gemini အသံရွေးပြီး အသံသွင်းပါ")
            voice = st.selectbox("Gemini voice", list(VOICE_OPTIONS.keys()), format_func=lambda item: f"{item} · {VOICE_OPTIONS[item]}")
            style = st.selectbox("Voice style", ["cinematic narrator", "warm narrator", "energetic creator", "serious documentary"])
            if st.button("Voiceover ထုတ်မယ်", type="primary", use_container_width=True):
                with st.spinner(f"Gemini {voice} အသံနဲ့ Voiceover ပြုလုပ်နေပါတယ်..."):
                    try:
                        st.session_state.audio = generate_voiceover(st.session_state.script, voice, style)
                        st.success("Voiceover ရပါပြီ။")
                    except Exception as exc:
                        st.error(api_error_message(exc))
        else:
            st.info("Blur လုပ်ပြီးမှ Gemini အသံရွေးတဲ့အဆင့် ပေါ်လာပါမယ်။")

    if st.session_state.get("audio"):
        st.audio(pcm_to_wav(st.session_state.audio), format="audio/wav")
        st.download_button("Voiceover ဒေါင်းရန်", pcm_to_wav(st.session_state.audio), file_name="recap-voiceover.wav", mime="audio/wav")
        if st.button("Video + Voiceover ဖိုင် ထုတ်မယ်", use_container_width=True):
            with st.spinner("Video နဲ့ Voiceover ကို ပေါင်းနေပါတယ်..."):
                try:
                    st.session_state.output_video = merge_audio_video(Path(st.session_state.blurred_video_path), st.session_state.audio)
                    st.success("Video အပြည့်အစုံနဲ့ Voiceover ကို အရှည်ကိုက်အောင် ပေါင်းပြီးပါပြီ။")
                except Exception as exc:
                    st.error(f"FFmpeg မအောင်မြင်ပါ: {exc}")

    if st.session_state.get("output_video"):
        st.video(st.session_state.output_video)
        st.download_button("Final Video ဒေါင်းရန်", st.session_state.output_video, file_name="movie-recap.mp4", mime="video/mp4")


if __name__ == "__main__":
    main()
