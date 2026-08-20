import base64
import hmac
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
import subprocess
import tempfile
import wave
from pathlib import Path

import streamlit as st
from google import genai
from PIL import Image, ImageDraw

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
PLATFORM_OPTIONS = {
    "YouTube": {"ratio": "16:9", "width": 1920, "height": 1080},
    "TikTok": {"ratio": "9:16", "width": 1080, "height": 1920},
    "Facebook": {"ratio": "1:1", "width": 1080, "height": 1080},
}


def get_api_key() -> str:
    try:
        secret_key = st.secrets.get("GOOGLE_AI_API_KEY", "")
    except Exception:
        secret_key = ""
    return st.session_state.get("google_ai_key") or secret_key or os.getenv("GOOGLE_AI_API_KEY", "")


def get_admin_password() -> str:
    try:
        return str(st.secrets.get("ADMIN_PASSWORD", "")).strip()
    except Exception:
        return os.getenv("ADMIN_PASSWORD", "").strip()


def register_generation() -> None:
    log = st.session_state.setdefault("generation_log", [])
    log.append(datetime.now(timezone.utc).isoformat())


def render_menu() -> None:
    with st.popover("⋮", use_container_width=True):
        st.markdown("### API Settings · Admin")
        st.markdown("Google AI Studio Key ယူရန် [ဒီနေရာကိုဖွင့်ပါ](https://aistudio.google.com/app/apikey)")
        st.caption("Google AI Studio ရဲ့ AQ... Authentication Key နဲ့ AIza... legacy key နှစ်မျိုးလုံး ထည့်နိုင်ပါတယ်။ Key ကို Session အတွင်းသာ အသုံးပြုပြီး GitHub/URL ထဲ မသိမ်းပါ။")
        key = st.text_input("Google AI Studio API Key", type="password", value=st.session_state.get("google_ai_key", ""), placeholder="AQ... or AIza...", key="menu_google_ai_key")
        if key.strip():
            st.session_state.google_ai_key = key.strip()
        if get_api_key():
            st.success("API Key အသင့်ဖြစ်ပါပြီ။")
        settings_left, settings_right = st.columns(2)
        with settings_left:
            st.session_state.gemini_text_model = st.text_input("Gemini text model", value=st.session_state.get("gemini_text_model", "gemini-3.7-flash"), key="menu_text_model")
            st.session_state.gemini_tts_model = st.text_input("Gemini TTS model", value=st.session_state.get("gemini_tts_model", "gemini-3.1-flash-tts-preview"), key="menu_tts_model")
        with settings_right:
            if st.button("Test Gemini API", use_container_width=True, key="menu_test_api"):
                try:
                    result = call_gemini(lambda client: client.interactions.create(model=st.session_state.gemini_text_model, input="Reply with the single word: READY"))
                    st.success(f"Connected: {getattr(result, 'output_text', 'READY')}")
                except Exception as exc:
                    st.error(api_error_message(exc))
            if st.button("Clear session key", use_container_width=True, key="menu_clear_key"):
                st.session_state.pop("google_ai_key", None)
                st.rerun()

        st.divider()
        st.markdown("### Admin")
        admin_password = st.text_input("Admin password", type="password", key="menu_admin_password")
        expected_password = get_admin_password()
        if st.button("Open Admin Dashboard", use_container_width=True, key="menu_admin_open"):
            st.session_state.admin_unlocked = bool(expected_password) and hmac.compare_digest(admin_password, expected_password)
            if not st.session_state.admin_unlocked:
                st.error("Admin password မမှန်ပါ သို့မဟုတ် ADMIN_PASSWORD Secret မထည့်ရသေးပါ။")
        if st.session_state.get("admin_unlocked"):
            now = datetime.now(timezone.utc)
            log = st.session_state.get("generation_log", [])
            recent = [stamp for stamp in log if now - datetime.fromisoformat(stamp) <= timedelta(hours=24)]
            metric_left, metric_mid, metric_right = st.columns(3)
            metric_left.metric("ပြီးခဲ့တဲ့ 24 နာရီ", len(recent))
            metric_mid.metric("စုစုပေါင်း", len(log))
            metric_right.metric("Session", "Active")
            st.caption("မှတ်ချက်: Streamlit Cloud မှာ Database မချိတ်ထားသေးတဲ့ Version ဖြစ်လို့ ဒီစာရင်းက လက်ရှိ Session အတွင်းပဲ မှတ်ထားပါတယ်။ အမြဲတမ်း စုစုပေါင်းလိုရင် Database လိုပါမယ်။")


def get_client() -> genai.Client:
    api_key = get_api_key().strip()
    if not api_key:
        raise ValueError("Google AI Studio API key မထည့်ရသေးပါ။ API Settings ထဲမှာ ထည့်ပါ။")
    return genai.Client(api_key=api_key)


def call_gemini(operation):
    last_error = None
    for attempt in range(2):
        try:
            return operation(get_client())
        except Exception as exc:
            last_error = exc
            if "closed" not in str(exc).lower() or attempt == 1:
                raise
    raise last_error


def api_error_message(error: Exception) -> str:
    message = str(error)
    lowered = message.lower()
    quota_error = any(token in lowered for token in ["429", "quota", "resource_exhausted", "rate limit", "too many requests"])
    key_error = any(token in lowered for token in ["401", "403", "api key", "unauthorized", "permission denied"])
    model_error = any(token in lowered for token in ["invalid_argument", "model not found", "unsupported model", "unknown model", "invalid model"])
    if quota_error:
        st.session_state.pop("google_ai_key", None)
        return "Gemini API quota ပြည့်သွားပါပြီ။ API Key အသစ်ထည့်ပါ သို့မဟုတ် Quota ပြန်ရသည်အထိ စောင့်ပါ။"
    if key_error:
        st.session_state.pop("google_ai_key", None)
        return "Gemini API Key Permission မရှိပါ။ AQ key ဖြစ်ရင် Native Gemini API/Google AI Studio အတွက် ထုတ်ထားတာ သေချာစစ်ပြီး API Key အသစ်ပြန်ထည့်ပါ။"
    if model_error:
        return f"Gemini Model/Endpoint မကိုက်ပါ။ Menu ထဲက Gemini model name ကို စစ်ပါ။ အသေးစိတ်: {message}"
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


def seconds_to_srt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def script_to_srt(script: str, duration_seconds: int, lines_per_caption: int = 2) -> str:
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    if not lines:
        return ""
    chunks = [lines[index:index + lines_per_caption] for index in range(0, len(lines), lines_per_caption)]
    slot = duration_seconds / len(chunks)
    entries = []
    for index, chunk in enumerate(chunks):
        start = index * slot
        end = duration_seconds if index == len(chunks) - 1 else (index + 1) * slot
        entries.append(f"{index + 1}\\n{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\\n{'\\n'.join(chunk)}\\n")
    return "\\n".join(entries)


def extract_translation_audio(video_path: Path) -> Path:
    """Create a small speech-focused file so faithful translation uploads quickly."""
    audio_path = Path(tempfile.mktemp(suffix="-translation.mp3"))
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "48k", str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        audio_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-800:] or "Video ထဲက အသံကို မထုတ်နိုင်ပါ။")
    return audio_path


def generate_recap_script(video_path: Path, language: str, duration_seconds: int, tone: str, mode: str) -> str:
    media_path = video_path
    media_type = "video"
    media_mime = "video/mp4"
    temporary_audio = None
    if mode == "Faithful full translation":
        try:
            temporary_audio = extract_translation_audio(video_path)
            media_path = temporary_audio
            media_type = "audio"
            media_mime = "audio/mpeg"
        except Exception:
            # Keep the full-video fallback for files with no readable audio stream.
            temporary_audio = None

    try:
        uploaded = call_gemini(lambda client: client.files.upload(file=str(media_path)))
    finally:
        if temporary_audio:
            temporary_audio.unlink(missing_ok=True)

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
    interaction = call_gemini(lambda client: client.interactions.create(
        model=st.session_state.get("gemini_text_model", "gemini-3.7-flash"),
        input=[
            {"type": "text", "text": prompt},
            {"type": media_type, "uri": uploaded.uri, "mime_type": getattr(uploaded, "mime_type", media_mime) or media_mime},
        ],
        generation_config={"temperature": 0.65, "thinking_level": "low"},
    ))
    text = getattr(interaction, "output_text", None)
    if not text:
        raise RuntimeError("Gemini က Script မပြန်ပေးပါ။")
    return text.strip()


def generate_voiceover(text: str, voice: str, style: str) -> bytes:
    prompt = f"Read this narration in a {style} style. Speak clearly and naturally, with short pauses at punctuation.\n\n{text}"
    interaction = call_gemini(lambda client: client.interactions.create(
        model=st.session_state.get("gemini_tts_model", "gemini-3.1-flash-tts-preview"),
        input=prompt,
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": voice}]},
    ))
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


def extract_preview_frame(video_path: Path, at_seconds: float = 0) -> Image.Image:
    """Decode a preview frame with a seek fallback for mobile-uploaded videos."""
    commands = [
        ["ffmpeg", "-y", "-ss", str(max(0, at_seconds)), "-i", str(video_path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        ["ffmpeg", "-y", "-i", str(video_path), "-ss", str(max(0, at_seconds)), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
    ]
    last_error = ""
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=False, timeout=90, check=True)
            if result.stdout:
                return Image.open(BytesIO(result.stdout)).convert("RGB")
            last_error = result.stderr.decode("utf-8", errors="ignore")[-500:]
        except (subprocess.SubprocessError, OSError) as exc:
            last_error = str(exc)
    raise RuntimeError(f"Video frame မရပါ။ Video format ကို စစ်ပါ။ {last_error}")


def sampled_frame_times(duration_seconds: int | None, count: int = 6) -> list[float]:
    if not duration_seconds or duration_seconds <= 1:
        return [0.0]
    usable = max(0.0, float(duration_seconds) - 0.5)
    return [round(usable * index / max(1, count - 1), 1) for index in range(count)]


def get_video_dimensions(video_path: Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(video_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        raw_dimensions = result.stdout.strip()
        if "x" not in raw_dimensions:
            return None
        width_text, height_text = raw_dimensions.split("x", 1)
        width, height = int(width_text), int(height_text)
        return (width, height) if width > 0 and height > 0 else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def draw_blur_selection(frame: Image.Image, boxes: list[tuple[int, int, int, int]], background_style: str) -> Image.Image:
    preview = frame.copy().convert("RGBA")
    draw = ImageDraw.Draw(preview, "RGBA")
    for index, (x, y, width, height) in enumerate(boxes, start=1):
        draw.rectangle((x, y, x + width, y + height), outline="#22b8ff", width=max(3, round(preview.width / 160)))
        if background_style == "Solid Box":
            draw.rectangle((x, y, x + width, y + height), fill=(20, 184, 255, 155))
        else:
            draw.rectangle((x, y, x + width, y + height), fill=(34, 184, 255, 55))
        draw.text((x + 8, y + 6), f"Blur Box {index}", fill=(255, 255, 255, 235))
    return preview.convert("RGB")


def apply_region_blur(video_path: Path, boxes: list[tuple[int, int, int, int]], blur_strength: int, background_style: str) -> Path:
    output_path = Path(tempfile.mktemp(suffix="-blurred.mp4"))
    filter_parts = []
    previous = "0:v"
    for index, (x, y, width, height) in enumerate(boxes):
        width = max(2, width - (width % 2))
        height = max(2, height - (height % 2))
        x = max(0, x)
        y = max(0, y)
        base = f"base{index}"
        region = f"region{index}"
        masked = f"masked{index}"
        output = "vout" if index == len(boxes) - 1 else f"stage{index}"
        filter_parts.append(f"[{previous}]split=2[{base}][{region}]")
        if background_style == "Solid Box":
            filter_parts.append(f"color=c=0x16b8ff@0.78:s={width}x{height}:d=1[solid{index}]")
            filter_parts.append(f"[{base}][solid{index}]overlay={x}:{y}[{output}]")
        elif background_style == "Transparent":
            filter_parts.append(f"[{region}]crop={width}:{height}:{x}:{y},boxblur={blur_strength}:2[{masked}]")
            filter_parts.append(f"[{base}][{masked}]overlay={x}:{y}[{output}]")
        else:
            filter_parts.append(f"[{region}]crop={width}:{height}:{x}:{y},boxblur={blur_strength}:2[{masked}]")
            filter_parts.append(f"[{base}][{masked}]overlay={x}:{y}[{output}]")
        previous = output
    filter_graph = ";".join(filter_parts)
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "copy", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-1200:])
    return output_path


def build_atempo_filter(speed: float) -> str:
    speed = max(0.5, min(4.0, speed))
    factors = []
    while speed > 2.0:
        factors.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        factors.append("atempo=0.5")
        speed /= 0.5
    factors.append(f"atempo={speed:.6f}")
    return ",".join(factors)


def adjust_pcm_audio_speed(audio_bytes: bytes, speed: float) -> bytes:
    """Apply the selected speed to raw 24 kHz mono PCM and return raw PCM."""
    if not audio_bytes or abs(float(speed) - 1.0) < 0.001:
        return audio_bytes
    input_path = Path(tempfile.mktemp(suffix=".pcm"))
    output_path = Path(tempfile.mktemp(suffix=".pcm"))
    input_path.write_bytes(audio_bytes)
    try:
        command = [
            "ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(input_path),
            "-af", build_atempo_filter(float(speed)), "-f", "s16le", "-ar", "24000", "-ac", "1", str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(result.stderr[-1200:])
        return output_path.read_bytes()
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def subtitle_ass_color(hex_color: str, alpha: int = 0) -> str:
    color = hex_color.strip().lstrip("#")
    if len(color) != 6:
        color = "FFFFFF"
    red, green, blue = color[0:2], color[2:4], color[4:6]
    alpha = max(0, min(255, int(alpha)))
    return f"&H{alpha:02X}{blue}{green}{red}"


def subtitle_alignment(position: str) -> int:
    return {"Bottom": 2, "Center": 5, "Top": 8}.get(position, 2)


def build_subtitle_filter(srt_path: Path, font_name: str, font_size: int, text_color: str, background_mode: str, background_color: str, background_opacity: int, position: str, fonts_dir: Path | None = None) -> str:
    back_alpha = 255 - round(max(0, min(100, int(background_opacity))) * 255 / 100)
    border_style = 3 if background_mode == "Solid background" else 1
    force_style = ",".join([
        f"FontName={font_name}", f"FontSize={max(12, min(96, int(font_size)))}",
        f"PrimaryColour={subtitle_ass_color(text_color)}", "OutlineColour=&H00000000",
        f"BackColour={subtitle_ass_color(background_color, back_alpha)}",
        f"BorderStyle={border_style}", "Outline=2", "Shadow=1",
        f"Alignment={subtitle_alignment(position)}", "MarginV=42",
    ])
    fonts_option = f":fontsdir={fonts_dir}" if fonts_dir else ""
    return f"subtitles={srt_path}{fonts_option}:force_style='{force_style}'"


def calculate_sync_plan(video_duration: float, audio_duration: float, audio_speed: float) -> dict[str, float]:
    """Use selected audio speed, then derive video speed to match the adjusted narration."""
    audio_speed = max(0.5, min(2.0, float(audio_speed)))
    adjusted_audio = max(0.1, float(audio_duration)) / audio_speed
    original_video = max(0.1, float(video_duration))
    auto_video_speed = original_video / adjusted_audio
    return {
        "audio_speed": audio_speed,
        "video_speed": auto_video_speed,
        "adjusted_audio": adjusted_audio,
        "target": adjusted_audio,
    }


def merge_audio_video(video_path: Path, audio_bytes: bytes, platform: str, speed: float = 1.0, subtitle_srt: str = "", subtitle_font: str = "Noto Sans Myanmar", subtitle_size: int = 34, subtitle_text_color: str = "#FFFFFF", subtitle_background_mode: str = "Transparent", subtitle_background_color: str = "#000000", subtitle_background_opacity: int = 55, subtitle_position: str = "Bottom") -> bytes:
    audio_path = Path(tempfile.mktemp(suffix=".wav"))
    srt_path = Path(tempfile.mktemp(suffix=".srt"))
    output_path = Path(tempfile.mktemp(suffix=".mp4"))
    if subtitle_srt.strip():
        srt_path.write_text(subtitle_srt, encoding="utf-8")
    wav_bytes = pcm_to_wav(audio_bytes)
    audio_path.write_bytes(wav_bytes)
    video_duration = get_video_duration(video_path)
    if not video_duration:
        raise RuntimeError("Original video duration ကို မဖတ်နိုင်ပါ။")
    audio_duration = max(0.1, len(audio_bytes) / (24000 * 2))
    sync_plan = calculate_sync_plan(video_duration, audio_duration, speed)
    audio_speed = sync_plan["audio_speed"]
    auto_video_speed = sync_plan["video_speed"]
    target_duration = sync_plan["target"]
    # User controls audio speed; video speed is derived automatically to match narration duration.
    audio_filter = [build_atempo_filter(audio_speed), "aresample=async=1:first_pts=0"]
    preset = PLATFORM_OPTIONS.get(platform, PLATFORM_OPTIONS["YouTube"])
    video_filter = f"setpts=PTS/{auto_video_speed:.6f},scale={preset['width']}:{preset['height']}:force_original_aspect_ratio=increase,crop={preset['width']}:{preset['height']}"
    if subtitle_srt.strip():
        provided_font_dir = Path(__file__).resolve().parent / "fonts"
        provided_font = provided_font_dir / "ဧက၀၇-Bold.ttf"
        subtitle_font_name = "A ka 07" if provided_font.exists() else subtitle_font
        video_filter += "," + build_subtitle_filter(srt_path, subtitle_font_name, subtitle_size, subtitle_text_color, subtitle_background_mode, subtitle_background_color, subtitle_background_opacity, subtitle_position, provided_font_dir if provided_font.exists() else None)
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0", "-vf", video_filter, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-af", ",".join(audio_filter),
        "-t", f"{target_duration:.3f}", "-movflags", "+faststart", str(output_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(result.stderr[-1400:])
        return output_path.read_bytes()
    finally:
        audio_path.unlink(missing_ok=True)
        srt_path.unlink(missing_ok=True)
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
        .stButton > button { width:100%; border:1px solid rgba(255,255,255,.22); border-radius:12px; padding:.72rem 1rem; color:#fff !important; background:linear-gradient(135deg,rgba(255,79,103,.95),rgba(132,76,255,.92)); box-shadow:0 10px 28px rgba(255,79,103,.18); font-weight:700; transition:transform .18s ease, box-shadow .18s ease; }
        [data-testid='stPopover'] > button, [data-testid='stFileUploaderDropzone'] button { color:#fff !important; background:linear-gradient(135deg,#ff4f67,#844cff) !important; border:1px solid rgba(255,255,255,.25) !important; font-weight:700 !important; }
        [data-testid='stFileUploaderDropzone'] small, [data-testid='stFileUploaderDropzone'] span, [data-testid='stFileUploaderDropzone'] p, label, .stCaption, [data-testid='stCaptionContainer'] { color:#d8d9e5 !important; }
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
          <div><div class='hero-kicker'>AI POST-PRODUCTION / GEMINI WORKSPACE</div><p class='hero-copy'>Turn a movie into a sharper story with natural translation, cinematic narration, and a clean final cut.</p></div>
          <div class='hero-orb'>🎬</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Gemini-powered movie recap studio · subtitles are intentionally disabled in this version")

    menu_spacer, menu_column = st.columns([5, 1], vertical_alignment="center")
    with menu_column:
        render_menu()
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
        st.session_state.blur_masks = None
        st.session_state.blur_enabled = False
        st.session_state.output_video = None

    video_duration = get_video_duration(st.session_state.video_path)
    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Original video")
        st.video(upload)
        st.caption(f"{upload.name} · {upload.size / 1024 / 1024:.1f} MB")
        if video_duration:
            duration_col, status_col = st.columns(2)
            with duration_col:
                st.metric("Video အရှည်", format_duration(video_duration))
            with status_col:
                st.metric("Duration seconds", f"{video_duration}s")
        else:
            st.error("Video အရှည်ကို မဖတ်နိုင်ပါ။")

    with right:
        st.subheader("1 · ပြန်ရေးမည့် ဘာသာစကား")
        language = st.selectbox("Language", LANGUAGES, label_visibility="collapsed")
        platform = st.selectbox("ဒီ Video ကို ဘယ်မှာတင်မလဲ?", list(PLATFORM_OPTIONS.keys()), key="target_platform")
        platform_preset = PLATFORM_OPTIONS[platform]
        st.info(f"{platform} အတွက် {platform_preset['ratio']} · {platform_preset['width']}×{platform_preset['height']}")
        source_kind = st.selectbox("Video အမျိုးအစား", ["Original movie", "Already-made recap"], key="source_kind", help="Original movie မှသာ Recap အရှည်ကို ကိုယ်တိုင်သတ်မှတ်နိုင်ပါမယ်။ Already-made recap ဆိုရင် မူရင်းအရှည်အတိုင်း ဆက်လုပ်ပါမယ်။")
        mode = st.selectbox("လုပ်ဆောင်မည့်ပုံစံ", ["Faithful full translation", "Original recap"], help="Faithful mode က အကြောင်းအရာအားလုံးကို မကျန်အောင် သဘာဝကျကျ ဘာသာပြန်ပေးမယ်။ Original recap က အကျဉ်းချုပ် Script အသစ်ရေးပေးမယ်။")
        duration_valid = True
        is_original_movie_recap = source_kind == "Original movie" and mode == "Original recap"
        if video_duration:
            st.caption(f"Video အရှည်: {format_duration(video_duration)}")
            if is_original_movie_recap:
                default_duration = min(60, video_duration)
                duration_text = st.text_input("Recap အရှည် (mm:ss)", value=format_duration(default_duration), help="Original movie ကို recap ပြန်လုပ်တဲ့အခါပဲ အရှည်သတ်မှတ်ပါ။")
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
                duration_seconds = video_duration
                if source_kind == "Already-made recap":
                    st.info("Already-made recap ဖြစ်လို့ အရှည်မသတ်မှတ်ပါ။ မူရင်း Video အရှည်အတိုင်း ဆက်လုပ်ပါမယ်။")
                else:
                    st.info("Faithful Translation Mode: Video ထဲက အကြောင်းအရာအားလုံးကို မကျန်အောင် ပြန်ပေးမယ်။")
        else:
            duration_seconds = 0
            duration_valid = False
            st.warning("Video အရှည်ကို မဖတ်နိုင်ပါ။ ဒီ Video ကို FFmpeg/FFprobe ဖတ်နိုင်တဲ့ MP4 အဖြစ် ပြောင်းပြီး ထပ်တင်ပါ။")
        tone = st.selectbox("Script style", ["Cinematic and concise", "Fast TikTok style", "Calm documentary", "Dramatic storyteller"])
        if st.button("Gemini နဲ့ Script ပြန်ရေးမယ်", type="primary", use_container_width=True):
            if not duration_valid:
                st.warning("အရင်ဆုံး Recap အရှည်ကို မှန်ကန်အောင် ထည့်ပါ။")
                st.stop()
            progress_message = "Video ကို Gemini က သုံးသပ်ပြီး အကြောင်းအရာအားလုံးကို သဘာဝကျကျ ဘာသာပြန်နေပါတယ်..." if mode == "Faithful full translation" else "Video ကို Gemini က သုံးသပ်ပြီး Copy မဖြစ်အောင် Script ပြန်ရေးနေပါတယ်..."
            with st.status(progress_message, expanded=True) as translation_status:
                try:
                    if mode == "Faithful full translation":
                        translation_status.write("Video ထဲက အသံကို အရင်ခွဲပြီး Gemini ဆီ ပို့နေပါတယ်... ဖိုင်အရွယ်အစားသေးလို့ ပိုမြန်ပါမယ်။")
                    else:
                        translation_status.write("Video အကြောင်းအရာကို Gemini က လေ့လာနေပါတယ်...")
                    st.session_state.script = generate_recap_script(st.session_state.video_path, language, int(duration_seconds), tone, mode)
                    translation_status.update(label="ဘာသာပြန်/Script ပြီးပါပြီ", state="complete", expanded=False)
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
        st.subheader("3 · Effects / Blur Mask (MAX 3)")
        st.caption("မူရင်း Video ကို အရင်ကြည့်ပြီး Blur လုပ်ချင်တဲ့ စာတန်း/နေရာကို ရွေးပါ။ Frame ပုံမထွက်ရင်လည်း အောက်က Video ကိုကြည့်ပြီး Box ကို ဆက်ချိန်နိုင်ပါတယ်။")
        st.video(st.session_state.video_path)
        st.caption("မူရင်း Video Preview · Blur ပြီးရင် အောက်မှာ Blur Video Preview ထပ်ပေါ်ပါမယ်။")
        try:
            duration_for_frames = get_video_duration(st.session_state.video_path)
            frame_times = list(sampled_frame_times(duration_for_frames) or [0.0])
            if not frame_times:
                frame_times = [0.0]
            frame_labels = [f"{format_duration(round(value))} မှာ Frame" for value in frame_times]
            selected_label = st.selectbox("စာတန်းအများဆုံး/အရှည်ဆုံးပေါ်တဲ့ Frame ရွေးပါ", frame_labels, key="blur_frame_choice")
            selected_frame_time = frame_times[frame_labels.index(selected_label)]
            preview_frame = None
            try:
                preview_frame = extract_preview_frame(st.session_state.video_path, selected_frame_time)
            except Exception as frame_exc:
                st.warning(f"Frame ပုံကို မထုတ်နိုင်သေးပါ။ မူရင်း Video Preview ကိုကြည့်ပြီး Box ကို ဆက်ချိန်နိုင်ပါတယ်: {frame_exc}")
            dimensions = get_video_dimensions(st.session_state.video_path)
            if dimensions:
                original_width, original_height = dimensions
            elif preview_frame:
                original_width, original_height = preview_frame.width, preview_frame.height
            else:
                original_width, original_height = 1280, 720
            original_width = max(2, int(original_width))
            original_height = max(2, int(original_height))
            if preview_frame:
                preview_width = min(720, preview_frame.width)
                preview_height = max(240, round(preview_frame.height * preview_width / preview_frame.width))
                scale_x = preview_width / original_width
                scale_y = preview_height / original_height
            else:
                preview_width = min(720, original_width)
                preview_height = max(240, round(original_height * preview_width / original_width))
                scale_x = preview_width / original_width
                scale_y = preview_height / original_height
            if "blur_masks" not in st.session_state:
                st.session_state.blur_masks = [{"x": original_width // 10, "y": original_height * 3 // 4, "width": original_width // 2, "height": max(10, original_height // 8)}]
            blur_enabled = st.toggle("BLUR MASK (MAX 3)", value=st.session_state.get("blur_enabled", False))
            st.session_state.blur_enabled = blur_enabled
            if not blur_enabled:
                st.session_state.blurred_video_path = None
                st.session_state.pop("subtitle_text", None)
            if blur_enabled:
                if st.button("+ Add Blur Box", disabled=len(st.session_state.blur_masks) >= 3):
                    st.session_state.blur_masks.append({"x": original_width // 10, "y": original_height // 3, "width": original_width // 3, "height": max(10, original_height // 8)})
                    st.rerun()
                background_style = st.selectbox("Background Style", ["None", "Transparent", "Solid Box"], help="Solid Box က အပြာရောင် Box အဖြစ် ဖုံးပေးမယ်။")
                blur_strength = st.slider("Blur Strength", 2, 40, 18)
                preview_boxes = []
                for index, mask in enumerate(st.session_state.blur_masks):
                    st.markdown(f"**Blur Box {index + 1}**")
                    box_left, box_right = st.columns(2)
                    with box_left:
                        mask["x"] = st.slider(f"X Position · Box {index + 1}", 0, max(0, original_width - 4), min(mask["x"], max(0, original_width - 4)), step=2, key=f"mask-x-{index}")
                        mask["y"] = st.slider(f"Y Position · Box {index + 1}", 0, max(0, original_height - 4), min(mask["y"], max(0, original_height - 4)), step=2, key=f"mask-y-{index}")
                    with box_right:
                        mask["width"] = st.slider(f"Width · Box {index + 1}", 4, max(4, original_width - mask["x"]), min(mask["width"], max(4, original_width - mask["x"])), step=2, key=f"mask-w-{index}")
                        mask["height"] = st.slider(f"Height · Box {index + 1}", 4, max(4, original_height - mask["y"]), min(mask["height"], max(4, original_height - mask["y"])), step=2, key=f"mask-h-{index}")
                    preview_boxes.append((round(mask["x"] * scale_x), round(mask["y"] * scale_y), round(mask["width"] * scale_x), round(mask["height"] * scale_y)))
                if preview_frame:
                    st.image(draw_blur_selection(preview_frame.resize((preview_width, preview_height)), preview_boxes, background_style), use_container_width=True)
                else:
                    st.info("Frame preview မရသေးပါ။ အပေါ်က မူရင်း Video ကိုကြည့်ပြီး Box နေရာကို ချိန်နိုင်ပါတယ်။ Apply လုပ်တဲ့အခါ Video တစ်ခုလုံးမှာ Blur ထည့်ပေးပါမယ်။")
                if st.button("Apply Blur Masks and Continue", type="primary", use_container_width=True):
                    with st.spinner("Blur Mask ကို Video တစ်ခုလုံးပေါ်မှာ ထည့်နေပါတယ်..."):
                        st.session_state.blurred_video_path = None
                        try:
                            boxes = [(int(mask["x"]), int(mask["y"]), int(mask["width"]), int(mask["height"])) for mask in st.session_state.blur_masks]
                            st.session_state.blurred_video_path = str(apply_region_blur(st.session_state.video_path, boxes, blur_strength, background_style))
                            st.session_state.audio = None
                            st.success("Blur Mask အောင်မြင်ပါပြီ။ အခု Blur Video ပေါ်မှာ မြန်မာစာတန်းထိုး Style ချိန်နိုင်ပါပြီ။")
                        except Exception as exc:
                            st.session_state.blurred_video_path = None
                            st.session_state.pop("subtitle_text", None)
                            st.error(f"Blur မအောင်မြင်ပါ။ Subtitle အဆင့်ကို Skip လုပ်ပြီး ဆက်သွားပါမယ်: {exc}")
            else:
                st.info("Effects ထဲက BLUR MASK ကို ဖွင့်ရင် Blue Blur Box ပေါ်လာပါမယ်။")
        except Exception as exc:
            st.session_state.blurred_video_path = None
            st.session_state.pop("subtitle_text", None)
            st.error(f"Blue Mask control မဖွင့်နိုင်ပါ။ မူရင်း Video ကို ပြန်တင်ပြီး ထပ်စမ်းပါ: {exc}")

        if st.session_state.get("blurred_video_path"):
            st.video(st.session_state.blurred_video_path)
            st.caption("Blur ပြီးသား Video Preview")
            st.divider()
            st.subheader("4 · မြန်မာစာတန်းထိုးနှင့် Style")
            st.caption("Blur အောင်မြင်ပြီးသား Video ပေါ်မှာ စာတန်းထိုး Style ချိန်ပါ။ Final MP4 ထုတ်တဲ့အခါ ဧက၀၇-Bold Font နဲ့ စာတန်းကို Video ထဲ တစ်ခါတည်း Burn-in ထည့်ပါမယ်။")
            subtitle_text = st.text_area("မြန်မာစာတန်းထိုးစာသား", value=st.session_state.get("subtitle_text", st.session_state.script), height=160, key="subtitle_text")
            subtitle_left, subtitle_right = st.columns(2)
            with subtitle_left:
                subtitle_font = st.selectbox("Font", ["Noto Sans Myanmar", "Pyidaungsu", "Myanmar Text", "Noto Sans", "Arial"], key="subtitle_font")
                subtitle_size = st.slider("Font size", 16, 72, 34, key="subtitle_size")
                subtitle_text_color = st.color_picker("စာတန်းအရောင်", "#FFFFFF", key="subtitle_text_color")
                subtitle_outline_color = st.color_picker("Outline အရောင်", "#000000", key="subtitle_outline_color")
            with subtitle_right:
                subtitle_background_mode = st.selectbox("Background", ["Transparent", "Solid background"], key="subtitle_background_mode")
                subtitle_background_color = st.color_picker("Background အရောင်", "#000000", key="subtitle_background_color")
                subtitle_background_opacity = st.slider("Background opacity", 0, 100, 55, key="subtitle_background_opacity")
                subtitle_position = st.selectbox("Position", ["Bottom", "Center", "Top"], key="subtitle_position")
            subtitle_duration = get_video_duration(Path(st.session_state.blurred_video_path)) or get_video_duration(st.session_state.video_path) or 60
            subtitle_srt = script_to_srt(subtitle_text, subtitle_duration)
            st.download_button("SRT ဒေါင်းရန်", subtitle_srt.encode("utf-8"), file_name="burmese-subtitles.srt", mime="application/x-subrip", use_container_width=True)
            st.success(f"Subtitle style သိမ်းပြီးပါပြီ · {subtitle_font} · {subtitle_text_color} · {subtitle_background_color}")

        if not st.session_state.get("blurred_video_path"):
            st.info("Blue Mask မအောင်မြင်သေးတဲ့အတွက် Blur နဲ့ မြန်မာစာတန်းထိုးအဆင့်ကို Skip လုပ်ထားပါတယ်။ Voiceover အဆင့်ကို ဆက်လုပ်နိုင်ပါတယ်။")

        st.divider()
        st.subheader("4 · Audio Speed + Gemini အသံ")
        speed_label = st.selectbox("Audio Speed", ["0.5×", "0.75×", "1×", "1.25×", "1.5×", "2×"], index=2, key="video_speed")
        audio_speed = float(speed_label.replace("×", ""))
        voiceover_duration = (len(st.session_state.audio) / (24000 * 2)) if st.session_state.get("audio") else None
        if voiceover_duration and video_duration:
            sync_plan = calculate_sync_plan(video_duration, voiceover_duration, audio_speed)
            adjusted_audio_duration = sync_plan["adjusted_audio"]
            auto_video_speed = sync_plan["video_speed"]
            final_duration = sync_plan["target"]
            st.info(f"အသံ {format_duration(round(voiceover_duration))} → {format_duration(round(adjusted_audio_duration))} · Video Auto-fit {auto_video_speed:.2f}× · Final {format_duration(round(final_duration))}")
        else:
            st.caption("Voiceover ထွက်ပြီးနောက် Audio မူရင်းအရှည်၊ ချိန်ပြီးအရှည်နဲ့ Video Auto-fit Speed ကို ပြပါမယ်။")
        st.warning("မူရင်း Video အသံကို ဖယ်ပြီး AI Voiceover ကိုပဲ ထည့်ပါမယ်။ Crop, Blur Mask, Recap Script နဲ့ AI narration တွေက transformative edit အတွက် အသုံးပြုသော်လည်း Copyright claim မဖြစ်မည်ဟု အာမခံမရပါ။ အသုံးပြုခွင့်ရှိသော footage ကိုသာ ထည့်ပါ။")
        voice = st.selectbox("Gemini voice", list(VOICE_OPTIONS.keys()), format_func=lambda item: f"{item} · {VOICE_OPTIONS[item]}")
        style = st.selectbox("Voice style", ["cinematic narrator", "warm narrator", "energetic creator", "serious documentary"])
        if st.button("Voiceover ထုတ်မယ်", type="primary", use_container_width=True):
            with st.spinner(f"Gemini {voice} အသံနဲ့ Voiceover ပြုလုပ်နေပါတယ်..."):
                try:
                    st.session_state.audio = generate_voiceover(st.session_state.script, voice, style)
                    st.session_state.pop("audio_preview", None)
                    st.session_state.pop("audio_preview_token", None)
                    st.success("Voiceover ရပါပြီ။")
                except Exception as exc:
                    st.error(api_error_message(exc))

    if st.session_state.get("audio"):
        raw_audio = st.session_state.audio
        audio_token = (len(raw_audio), raw_audio[:16], raw_audio[-16:])
        cached_token = st.session_state.get("audio_preview_token")
        if cached_token != (audio_token, audio_speed):
            try:
                st.session_state.audio_preview = adjust_pcm_audio_speed(raw_audio, audio_speed)
                st.session_state.audio_preview_token = (audio_token, audio_speed)
            except Exception as exc:
                st.session_state.audio_preview = raw_audio
                st.session_state.audio_preview_token = (audio_token, audio_speed)
                st.warning(f"Voiceover Speed Preview မပြောင်းနိုင်ပါ။ မူရင်းအသံကို ပြထားပါတယ်: {exc}")
        preview_audio = st.session_state.get("audio_preview", raw_audio)
        preview_duration = len(preview_audio) / (24000 * 2)
        st.caption(f"Voiceover Preview · မူရင်း {format_duration(round(voiceover_duration or 0))} → Speed {audio_speed:g}× → {format_duration(round(preview_duration))}")
        st.audio(pcm_to_wav(preview_audio), format="audio/wav")
        st.download_button("ချိန်ပြီး Voiceover ဒေါင်းရန်", pcm_to_wav(preview_audio), file_name="recap-voiceover-adjusted.wav", mime="audio/wav")
        if st.button("Video + Voiceover ဖိုင် ထုတ်မယ်", use_container_width=True):
            with st.spinner("Audio Speed ချိန်ပြီး Video ကို အရှည်ကိုက်အောင် Auto-fit လုပ်နေပါတယ်..."):
                try:
                    source_video = Path(st.session_state.get("blurred_video_path") or st.session_state.video_path)
                    subtitle_srt_to_burn = ""
                    if st.session_state.get("blurred_video_path") and st.session_state.get("subtitle_text", "").strip():
                        subtitle_duration = get_video_duration(source_video) or video_duration or 60
                        subtitle_srt_to_burn = script_to_srt(st.session_state.subtitle_text, subtitle_duration)
                    st.session_state.output_video = merge_audio_video(
                        source_video,
                        st.session_state.audio,
                        platform,
                        audio_speed,
                        subtitle_srt=subtitle_srt_to_burn,
                        subtitle_font=st.session_state.get("subtitle_font", "A ka 07"),
                        subtitle_size=st.session_state.get("subtitle_size", 34),
                        subtitle_text_color=st.session_state.get("subtitle_text_color", "#FFFFFF"),
                        subtitle_background_mode=st.session_state.get("subtitle_background_mode", "Transparent"),
                        subtitle_background_color=st.session_state.get("subtitle_background_color", "#000000"),
                        subtitle_background_opacity=st.session_state.get("subtitle_background_opacity", 55),
                        subtitle_position=st.session_state.get("subtitle_position", "Bottom"),
                    )
                    register_generation()
                    st.success("Audio Speed ချိန်ပြီး Video ကို အရှည်ကိုက်အောင် Auto-fit လုပ်ထားတဲ့ Final Video ရပါပြီ။")
                except Exception as exc:
                    st.error(f"FFmpeg မအောင်မြင်ပါ: {exc}")

    if st.session_state.get("output_video"):
        st.video(st.session_state.output_video)
        st.download_button("Final Video ဒေါင်းရန်", st.session_state.output_video, file_name=f"movie-recap-{st.session_state.get('target_platform', 'video').lower()}.mp4", mime="video/mp4")


if __name__ == "__main__":
    main()
