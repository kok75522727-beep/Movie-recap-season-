import base64
import os
import subprocess
import tempfile
import wave
from pathlib import Path

import streamlit as st
from google import genai

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


def generate_recap_script(video_path: Path, language: str, duration: str, tone: str) -> str:
    client = get_client()
    uploaded = client.files.upload(file=str(video_path))
    prompt = f"""
You are a professional movie recap editor. Watch the uploaded video and write a concise original narration in {language}.
Target length: {duration}. Tone: {tone}.

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


def merge_audio_video(video_path: Path, audio_bytes: bytes) -> bytes:
    audio_path = Path(tempfile.mktemp(suffix=".wav"))
    output_path = Path(tempfile.mktemp(suffix=".mp4"))
    audio_path.write_bytes(pcm_to_wav(audio_bytes))
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
        "-shortest", str(output_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(result.stderr[-1000:])
        return output_path.read_bytes()
    finally:
        audio_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def main():
    st.markdown("# 🎬 RecapLab")
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
        st.session_state.output_video = None

    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Original video")
        st.video(upload)
        st.caption(f"{upload.name} · {upload.size / 1024 / 1024:.1f} MB")

    with right:
        st.subheader("1 · ပြန်ရေးမည့် ဘာသာစကား")
        language = st.selectbox("Language", LANGUAGES, label_visibility="collapsed")
        duration = st.selectbox("Recap အရှည်", ["30 seconds", "60 seconds", "90 seconds", "3 minutes"], index=1)
        tone = st.selectbox("Script style", ["Cinematic and concise", "Fast TikTok style", "Calm documentary", "Dramatic storyteller"])
        if st.button("Gemini နဲ့ Script ပြန်ရေးမယ်", type="primary", use_container_width=True):
            with st.spinner("Video ကို Gemini က သုံးသပ်ပြီး Copy မဖြစ်အောင် Script ပြန်ရေးနေပါတယ်..."):
                try:
                    st.session_state.script = generate_recap_script(st.session_state.video_path, language, duration, tone)
                    st.session_state.audio = None
                    st.success("Original recap script ရပါပြီ။")
                except Exception as exc:
                    st.error(api_error_message(exc))

    if st.session_state.get("script"):
        st.divider()
        st.subheader("2 · Script ကို စစ်ပြီး ပြင်ပါ")
        st.caption("ဒီ Version မှာ Subtitle မထည့်သေးပါ။ Script ကို ကိုယ်တိုင်ပြင်ပြီး အသံထုတ်နိုင်ပါတယ်။")
        st.session_state.script = st.text_area("Editable narration", st.session_state.script, height=230)
        st.download_button("Script ဒေါင်းရန်", st.session_state.script, file_name="recap-script.txt", mime="text/plain")

        st.divider()
        st.subheader("3 · Gemini အသံရွေးပြီး အသံသွင်းပါ")
        voice = st.selectbox("Gemini voice", list(VOICE_OPTIONS.keys()), format_func=lambda item: f"{item} · {VOICE_OPTIONS[item]}")
        style = st.selectbox("Voice style", ["cinematic narrator", "warm narrator", "energetic creator", "serious documentary"])
        if st.button("Voiceover ထုတ်မယ်", type="primary", use_container_width=True):
            with st.spinner(f"Gemini {voice} အသံနဲ့ Voiceover ပြုလုပ်နေပါတယ်..."):
                try:
                    st.session_state.audio = generate_voiceover(st.session_state.script, voice, style)
                    st.success("Voiceover ရပါပြီ။")
                except Exception as exc:
                    st.error(api_error_message(exc))

    if st.session_state.get("audio"):
        st.audio(pcm_to_wav(st.session_state.audio), format="audio/wav")
        st.download_button("Voiceover ဒေါင်းရန်", pcm_to_wav(st.session_state.audio), file_name="recap-voiceover.wav", mime="audio/wav")
        if st.button("Video + Voiceover ဖိုင် ထုတ်မယ်", use_container_width=True):
            with st.spinner("Video နဲ့ Voiceover ကို ပေါင်းနေပါတယ်..."):
                try:
                    st.session_state.output_video = merge_audio_video(st.session_state.video_path, st.session_state.audio)
                    st.success("Output video ရပါပြီ။")
                except Exception as exc:
                    st.error(f"FFmpeg မအောင်မြင်ပါ: {exc}")

    if st.session_state.get("output_video"):
        st.video(st.session_state.output_video)
        st.download_button("Final Video ဒေါင်းရန်", st.session_state.output_video, file_name="movie-recap.mp4", mime="video/mp4")


if __name__ == "__main__":
    main()
