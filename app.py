import base64
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
import subprocess
import tempfile
import wave
from pathlib import Path

import streamlit as st
from google import genai
from PIL import Image, ImageDraw, ImageFont

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


DEFAULT_ADMIN_PASSWORD = "Khant@6789"


def get_admin_password() -> str:
    try:
        configured = str(st.secrets.get("ADMIN_PASSWORD", "")).strip()
    except Exception:
        configured = ""
    return configured or os.getenv("ADMIN_PASSWORD", "").strip() or DEFAULT_ADMIN_PASSWORD


METRICS_PATH = Path(__file__).resolve().parent / "generation_metrics.json"


def load_generation_log() -> list[str]:
    try:
        if not METRICS_PATH.exists():
            return []
        data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        return [str(value) for value in data.get("generation_log", []) if isinstance(value, str)]
    except (OSError, ValueError, TypeError):
        return []


def save_generation_log(log: list[str]) -> None:
    temporary_path = METRICS_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps({"generation_log": log}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(METRICS_PATH)


def register_generation() -> None:
    log = load_generation_log()
    log.append(datetime.now(timezone.utc).isoformat())
    save_generation_log(log)


def render_menu() -> None:
    with st.popover("☰ Menu", use_container_width=True):
        st.markdown("### API Key Settings · Admin")
        st.caption("API Key နဲ့ Admin password ကို ဒီ Menu ထဲမှာပဲ ထည့်ပါ။ Main page မှာ မပြပါ။")
        st.markdown("Google AI Studio Key ယူရန် [ဒီနေရာကိုဖွင့်ပါ](https://aistudio.google.com/app/apikey)")
        st.caption("Google AI Studio ရဲ့ AQ... Authentication Key နဲ့ AIza... legacy key နှစ်မျိုးလုံး ထည့်နိုင်ပါတယ်။ Key ကို Session အတွင်းသာ အသုံးပြုပြီး GitHub/URL ထဲ မသိမ်းပါ။")
        key = st.text_input("Google AI Studio API Key ထည့်ရန်", type="password", value=st.session_state.get("google_ai_key", ""), placeholder="AQ... or AIza...", key="menu_google_ai_key")
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
        admin_password = st.text_input("Admin password (Khant@6789)", type="password", key="menu_admin_password")
        expected_password = get_admin_password()
        if st.button("Open Admin Dashboard", use_container_width=True, key="menu_admin_open"):
            st.session_state.admin_unlocked = bool(expected_password) and hmac.compare_digest(admin_password, expected_password)
            if not st.session_state.admin_unlocked:
                st.error("Admin password မမှန်ပါ သို့မဟုတ် ADMIN_PASSWORD Secret မထည့်ရသေးပါ။")
        if st.session_state.get("admin_unlocked"):
            now = datetime.now(timezone.utc)
            log = load_generation_log()
            recent = []
            for stamp in log:
                try:
                    if now - datetime.fromisoformat(stamp) <= timedelta(hours=24):
                        recent.append(stamp)
                except ValueError:
                    continue
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


def wrap_subtitle_lines(text: str, max_chars: int = 34) -> list[str]:
    """Wrap Burmese/Unicode text into readable lines without exceeding two lines per caption."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    result = []
    current = ""
    for char in cleaned:
        candidate = current + char
        if current and len(candidate) > max_chars:
            result.append(current.strip())
            current = char
        else:
            current = candidate
    if current.strip():
        result.append(current.strip())
    return result


def split_subtitle_segments(text: str, max_chars: int = 40) -> list[str]:
    """Follow the supplied voice engine: split by Burmese sentence marks, then length."""
    clean = re.sub(r"\s+", " ", str(text or "").replace("\r", "")).strip()
    if not clean:
        return []
    sentences = re.split(r"(?<=[။!?！？])\s*|\n+", clean)
    segments = []
    for sentence in sentences:
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars + 1)
            if cut < 5:
                cut = max_chars
            segments.append(sentence[:cut].strip())
            sentence = sentence[cut:].lstrip()
        if sentence:
            segments.append(sentence)
    return segments or [clean]


def script_to_srt(script: str, duration_seconds: float, lines_per_caption: int = 2) -> str:
    segments = split_subtitle_segments(script)
    if not segments:
        return ""
    captions = []
    for segment in segments:
        wrapped = wrap_subtitle_lines(segment, 34)
        for index in range(0, len(wrapped), lines_per_caption):
            captions.append("\\n".join(wrapped[index:index + lines_per_caption]))
    if not captions:
        return ""
    duration = max(0.1, float(duration_seconds))
    each = duration / len(captions)
    entries = []
    for index, caption in enumerate(captions):
        start = (index * each)
        end = duration if index == len(captions) - 1 else ((index + 1) * each)
        entries.append(f"{index + 1}\\n{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\\n{caption}\\n")
    return "\\n".join(entries)


def normalize_srt_text(srt_text: str) -> str:
    """Return strict UTF-8-friendly SRT blocks with monotonic timestamps and max two text lines."""
    cleaned = (srt_text or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    for raw_block in re.split(r"\n\s*\n", cleaned):
        lines = [line.strip() for line in raw_block.split("\n") if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
            start_match = re.match(r"(\d+):(\d{2}):(\d{2}),(\d{3})$", start_text)
            end_match = re.match(r"(\d+):(\d{2}):(\d{2}),(\d{3})$", end_text)
            if not start_match or not end_match:
                continue
            start_ms = (((int(start_match.group(1)) * 60) + int(start_match.group(2))) * 60 + int(start_match.group(3))) * 1000 + int(start_match.group(4))
            end_ms = (((int(end_match.group(1)) * 60) + int(end_match.group(2))) * 60 + int(end_match.group(3))) * 1000 + int(end_match.group(4))
            end_ms = max(start_ms + 250, end_ms)
            text = wrap_subtitle_lines(" ".join(lines[2:]), 34)
            if not text:
                continue
            blocks.append((start_ms, end_ms, "\n".join(text[:2])))
        except (TypeError, ValueError):
            continue
    normalized = []
    previous_end = 0
    for index, (start_ms, end_ms, text) in enumerate(blocks, 1):
        start_ms = max(previous_end, start_ms)
        end_ms = max(start_ms + 250, end_ms)
        normalized.append(f"{index}\n{seconds_to_srt_time(start_ms / 1000)} --> {seconds_to_srt_time(end_ms / 1000)}\n{text}\n")
        previous_end = end_ms
    return "\n".join(normalized)


def srt_to_plain_text(srt_text: str) -> str:
    """Extract caption text from a time-coded SRT for the visual preview."""
    text_lines = []
    for line in (srt_text or "").replace("\\r", "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        text_lines.append(stripped)
    return " ".join(text_lines)


def add_srt_position_tags(srt_text: str, x_percent: int, y_percent: int, width: int, height: int) -> str:
    """Add ASS position tags so FFmpeg/libass places captions at the user's XY point."""
    x = round(width * max(0, min(100, int(x_percent))) / 100)
    y = round(height * max(0, min(100, int(y_percent))) / 100)
    position_tag = f"{{\\\\an5\\\\pos({x},{y})}}"
    output = []
    for line in srt_text.splitlines():
        if " --> " in line:
            output.append(line)
        elif line.strip() and not line.strip().isdigit() and not line.startswith("{"):
            output.append(position_tag + line)
        else:
            output.append(line)
    return "\\n".join(output) + ("\\n" if srt_text.endswith("\\n") else "")


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


def estimate_script_seconds(text: str, language: str) -> float:
    cleaned = " ".join(text.split())
    if not cleaned:
        return 0.0
    if language.startswith("Burmese"):
        # Burmese narration has no whitespace between every word, so estimate by
        # Myanmar characters rather than splitting on spaces.
        units = sum(1 for char in cleaned if not char.isspace())
        return units / 7.5
    return len(cleaned.split()) / 2.35


def complete_short_recap_script(script: str, target_language: str, target_seconds: int, tone: str) -> str:
    current_seconds = estimate_script_seconds(script, target_language)
    if current_seconds >= target_seconds * 0.88:
        return script

    missing_seconds = max(1, int(round(target_seconds - current_seconds)))
    completion_prompt = f"""
You are revising a movie recap narration that is too short.
Target language: {target_language}.
Target runtime: {target_seconds} seconds. Estimated current runtime: {current_seconds:.0f} seconds.
Add approximately {missing_seconds} seconds of narration while keeping the same story and tone ({tone}).
Expand with only visible or inferable scene details: character actions, facial and emotional reactions,
movement, setting changes, cause-and-effect, important objects, tension, and brief paraphrased dialogue context.
Do not invent scenes, do not quote source dialogue, do not add headings or timestamps, and return only the complete revised narration.

CURRENT NARRATION:
{script}
"""
    try:
        interaction = call_gemini(lambda client: client.interactions.create(
            model=st.session_state.get("gemini_text_model", "gemini-3.7-flash"),
            input=completion_prompt,
            generation_config={"temperature": 0.55, "thinking_level": "low"},
        ))
        expanded = (getattr(interaction, "output_text", None) or "").strip()
        if expanded and estimate_script_seconds(expanded, target_language) > current_seconds:
            return expanded
    except Exception:
        pass
    return script


def generate_recap_script(video_path: Path, language: str, duration_seconds: int, tone: str, mode: str) -> str:
    media_path = video_path
    media_type = "video"
    media_mime = "video/mp4"
    temporary_audio = None
    # Some AQ-compatible gateways incorrectly encode request metadata as ASCII.
    # Keep the target-language instruction ASCII-safe while retaining Burmese output intent.
    target_language = "Burmese (Myanmar)" if language.startswith("Burmese") else language
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
Watch the uploaded video and translate ALL spoken dialogue and narration into {target_language}.
This is a faithful translation mode: do not summarize, shorten, skip, reorder, or invent anything.
Preserve every meaningful sentence and event in the original order. Translate naturally and clearly for a native {target_language} speaker. If the target is Burmese (Myanmar), write the result using Myanmar Unicode script.
Keep speaker changes and paragraph breaks when they are apparent. Do not add commentary, headings, timestamps, subtitles, or explanations.
If a word is unclear, mark it as [unclear] rather than inventing content.
Return only the complete natural translation.
"""
    else:
        prompt = f"""
You are a professional movie recap editor. Watch the uploaded video and write a complete original narration in {target_language}.
The user selected an exact target runtime of {duration_seconds} seconds ({duration_seconds // 60} minutes {duration_seconds % 60} seconds). The narration must be long enough to fill that full runtime at a natural Burmese narration pace; do not produce a short summary.

Build the narration scene by scene from the video. Cover the visible actions, character movements, facial or emotional reactions, changes in location, cause-and-effect, important objects, tension, and the way each scene leads to the next. Include brief paraphrased context for important dialogue and how other characters respond, but never quote source dialogue word-for-word. Use connective narration between scenes so the final script feels continuous and complete.

Important originality and safety rules:
- Do not copy dialogue, subtitles, or any source narration word-for-word.
- Do not quote long passages.
- Paraphrase the events in your own words and focus on commentary, sequence, cause-and-effect, and character decisions.
- Do not invent scenes that are not visible or inferable from the video.
- Do not end early just because the main plot is known; continue through the selected runtime with concrete scene details and reactions.
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
    result = text.strip()
    if mode != "Faithful full translation":
        result = complete_short_recap_script(result, target_language, duration_seconds, tone)
    return result


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


FONT_FILES = {
    "A ka 07 · ဧက၀၇-Bold": ("ဧက၀၇-Bold.ttf", "A ka 07"),
    "ပြည်ထောင်စု Bold": ("ပြည်ထောင်စု_Bold.ttf", "ပြည်ထောင်စု"),
    "ပြည်ထောင်စု Regular": ("ပြည်ထောင်စု_Regular.ttf", "ပြည်ထောင်စု"),
    "ပြည်ထောင်စု 2.5.3 Bold": ("ပြည်ထောင်စု-2.5.3_Bold.ttf", "ပြည်ထောင်စု"),
    "ဧက၀၁ Bold": ("ဧက၀၁-Bold.ttf", "ဧက၀၁"),
    "Noto Sans Myanmar": (None, "Noto Sans Myanmar"),
}


def resolve_myanmar_font(font_name: str | None = None) -> Path | None:
    font_dir = Path(__file__).resolve().parent / "fonts"
    if font_name in FONT_FILES:
        filename, _ = FONT_FILES[font_name]
        if filename and (font_dir / filename).exists():
            return font_dir / filename
    candidates = [
        Path("/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansMyanmar-Medium.ttf"),
        Path("/usr/share/fonts/truetype/padauk/Padauk-Regular.ttf"),
        font_dir / "ဧက၀၇-Bold.ttf",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def subtitle_font_family(font_name: str | None) -> str:
    return FONT_FILES.get(font_name, (None, font_name or "Noto Sans Myanmar"))[1]


def render_live_subtitle_preview(frame: Image.Image, text: str, font_name: str, font_size: int, text_color: str, outline_color: str, background_mode: str, background_color: str, background_opacity: int, position: str, x_percent: int = 50, y_percent: int | None = None) -> Image.Image:
    """Render a low-cost visual sample for subtitle styling before final FFmpeg export."""
    image = frame.convert("RGB").copy()
    image.thumbnail((900, 900))
    draw = ImageDraw.Draw(image, "RGBA")
    font_path = resolve_myanmar_font(font_name)
    try:
        font = ImageFont.truetype(str(font_path), max(12, int(font_size))) if font_path else ImageFont.truetype("DejaVuSans.ttf", max(12, int(font_size)))
    except OSError:
        font = ImageFont.load_default()
    clean_text = " ".join((text or "မြန်မာစာတန်းထိုး စမ်းသပ်ခြင်း").split())
    clean_text = clean_text[:180]
    max_width = max(120, image.width - 48)
    estimated_chars = max(12, min(34, int(max_width / max(10, font_size * 0.58))))
    lines = wrap_subtitle_lines(clean_text, estimated_chars)[:2]
    line_gap = max(4, int(font_size * 0.18))
    line_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2) for line in lines]
    block_height = sum(box[3] - box[1] for box in line_boxes) + line_gap * max(0, len(lines) - 1)
    if y_percent is None:
        if position == "Top":
            y_percent = 12
        elif position == "Center":
            y_percent = 50
        else:
            y_percent = 86
    start_y = max(8, min(image.height - block_height - 8, round(image.height * max(0, min(100, int(y_percent))) / 100 - block_height / 2)))
    if background_mode == "Solid background":
        pad_x, pad_y = 16, 10
        max_line_width = max((box[2] - box[0] for box in line_boxes), default=0)
        center_x = round(image.width * max(0, min(100, int(x_percent))) / 100)
        bg_box = (max(8, center_x - max_line_width // 2 - pad_x), max(4, start_y - pad_y), min(image.width - 8, center_x + max_line_width // 2 + pad_x), min(image.height - 4, start_y + block_height + pad_y))
        draw.rounded_rectangle(bg_box, radius=12, fill=background_color + f"{max(0, min(100, int(background_opacity))) * 255 // 100:02x}")
    y = start_y
    for line, box in zip(lines, line_boxes):
        width = box[2] - box[0]
        x = max(8, min(image.width - width - 8, round(image.width * max(0, min(100, int(x_percent))) / 100 - width / 2)))
        draw.text((x, y), line, font=font, fill=text_color, stroke_width=2, stroke_fill=outline_color)
        y += box[3] - box[1] + line_gap
    return image

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


def apply_region_blur(video_path: Path, boxes: list[tuple[int, int, int, int]], blur_strength: int, background_style: str, solid_box_color: str = "#16B8FF") -> Path:
    output_path = Path(tempfile.mktemp(suffix="-blurred.mp4"))
    # FFmpeg boxblur rejects chroma radii >= 15; keep the UI value safe for all pixel formats.
    safe_blur_strength = min(12, max(0, int(blur_strength)))
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
            safe_color = solid_box_color.strip().lstrip("#")[:6] or "16B8FF"
            filter_parts.append(f"color=c=0x{safe_color}@0.78:s={width}x{height}:d=1[solid{index}]")
            filter_parts.append(f"[{base}][solid{index}]overlay={x}:{y}[{output}]")
        elif background_style == "Transparent":
            filter_parts.append(f"[{region}]crop={width}:{height}:{x}:{y},boxblur={safe_blur_strength}:2[{masked}]")
            filter_parts.append(f"[{base}][{masked}]overlay={x}:{y}[{output}]")
        else:
            filter_parts.append(f"[{region}]crop={width}:{height}:{x}:{y},boxblur={safe_blur_strength}:2[{masked}]")
            filter_parts.append(f"[{base}][{masked}]overlay={x}:{y}[{output}]")
        previous = output
    filter_graph = ";".join(filter_parts)
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-threads", "2",
        "-c:a", "copy", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-1200:])
    return output_path


def apply_copyright_edit(video_path: Path, mirror: bool, auto_zoom: bool, color_filter: bool, pitch_alter: bool) -> Path:
    """Render the selected copyright-edit effects into a new preview video."""
    output_path = Path(tempfile.mktemp(suffix="-copyright-edited.mp4"))
    video_filters = []
    if mirror:
        video_filters.append("hflip")
    if auto_zoom:
        video_filters.append("scale=iw*1.08:ih*1.08,crop=iw/1.08:ih/1.08")
    if color_filter:
        video_filters.append("eq=saturation=0.82:contrast=1.04:brightness=0.02")
    video_filter = ",".join(video_filters) if video_filters else "null"
    audio_filter = "asetrate=24960,aresample=24000,atempo=0.961538" if pitch_alter else "anull"
    command = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", video_filter, "-af", audio_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-1600:])
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
    # Use libass's explicit filename form and escape filter-special characters.
    escaped_srt = str(srt_path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    escaped_fonts = str(fonts_dir.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'") if fonts_dir else ""
    fonts_option = f":fontsdir='{escaped_fonts}'" if escaped_fonts else ""
    return f"subtitles=filename='{escaped_srt}'{fonts_option}:force_style='{force_style}'"


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


def render_dimensions(platform: str, quality_mode: str) -> tuple[int, int]:
    quality_dimensions = {
        "720": {
            "YouTube": (1280, 720),
            "TikTok": (720, 1280),
            "Facebook": (720, 720),
        },
        "1280": {
            "YouTube": (1920, 1080),
            "TikTok": (1280, 1920),
            "Facebook": (1280, 1280),
        },
    }
    return quality_dimensions.get(quality_mode, quality_dimensions["720"]).get(platform, (1280, 720))


def merge_audio_video(video_path: Path, audio_bytes: bytes, platform: str, speed: float = 1.0, subtitle_srt: str = "", subtitle_font: str = "Noto Sans Myanmar", subtitle_size: int = 34, subtitle_text_color: str = "#FFFFFF", subtitle_background_mode: str = "Transparent", subtitle_background_color: str = "#000000", subtitle_background_opacity: int = 55, subtitle_position: str = "Bottom", subtitle_x: int = 50, subtitle_y: int = 86, effect_mirror: bool = False, effect_auto_zoom: bool = False, effect_color_filter: bool = False, effect_pitch_alter: bool = False, quality_mode: str = "720") -> bytes:
    audio_handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    audio_path = Path(audio_handle.name)
    audio_handle.close()
    srt_path: Path | None = None
    if subtitle_srt.strip():
        srt_handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False, suffix=".srt")
        try:
            srt_handle.write(normalize_srt_text(subtitle_srt))
            srt_handle.flush()
            os.fsync(srt_handle.fileno())
        finally:
            srt_handle.close()
        srt_path = Path(srt_handle.name).resolve()
        if not srt_path.exists() or srt_path.stat().st_size == 0:
            raise RuntimeError("Subtitle SRT temporary file ကို မဖန်တီးနိုင်ပါ။")
    output_path = Path(tempfile.mktemp(suffix=".mp4"))
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
    audio_filter = []
    if effect_pitch_alter:
        # Small pitch shift with duration compensation; avoids changing narration length.
        audio_filter.extend(["asetrate=24960", "aresample=24000", "atempo=0.961538"])
    audio_filter.extend([build_atempo_filter(audio_speed), "aresample=async=1:first_pts=0"])
    output_width, output_height = render_dimensions(platform, quality_mode)
    video_parts = [f"setpts=PTS/{auto_video_speed:.6f}"]
    if effect_mirror:
        video_parts.append("hflip")
    if effect_auto_zoom:
        video_parts.append("scale=iw*1.08:ih*1.08,crop=iw/1.08:ih/1.08")
    if effect_color_filter:
        video_parts.append("eq=contrast=1.04:brightness=0.02:saturation=1.12")
    video_parts.append(f"scale={output_width}:{output_height}:force_original_aspect_ratio=increase")
    video_parts.append(f"crop={output_width}:{output_height}")
    video_filter = ",".join(video_parts)
    base_video_filter = video_filter
    if srt_path is not None:
        provided_font_dir = Path(__file__).resolve().parent / "fonts"
        myanmar_font_path = resolve_myanmar_font(subtitle_font)
        subtitle_font_name = subtitle_font_family(subtitle_font)
        subtitle_fonts_dir = myanmar_font_path.parent if myanmar_font_path else (provided_font_dir if provided_font_dir.exists() else None)
        video_filter += "," + build_subtitle_filter(srt_path, subtitle_font_name, subtitle_size, subtitle_text_color, subtitle_background_mode, subtitle_background_color, subtitle_background_opacity, subtitle_position, subtitle_fonts_dir)
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0", "-vf", video_filter, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-af", ",".join(audio_filter),
        "-t", f"{target_duration:.3f}", "-movflags", "+faststart", str(output_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0 or not output_path.exists():
            subtitle_open_failed = srt_path is not None and "Unable to open" in (result.stderr or "") and ".srt" in (result.stderr or "")
            if subtitle_open_failed:
                # Subtitle Toggle is ON: never silently return a subtitle-free MP4.
                # The caller must see the real subtitle error and can retry after deployment refresh.
                raise RuntimeError("SRT ကို Final Video ထဲ မပေါင်းနိုင်ပါ။ SRT ဖိုင်လမ်းကြောင်း/Font ကို စစ်ပြီး ပြန်စမ်းပါ။\n" + (result.stderr or "")[-1200:])
            raise RuntimeError(result.stderr[-1400:])
        return output_path.read_bytes()
    finally:
        audio_path.unlink(missing_ok=True)
        if srt_path is not None:
            srt_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def apply_cinematic_theme():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
        :root { --mgk-gold:#f4c95d; --mgk-coral:#ff6b6b; --mgk-violet:#7c5cff; --mgk-navy:#0b1020; --mgk-ink:#f8f6ef; --mgk-muted:#aeb5c8; --mgk-panel:rgba(18,25,46,.86); --coral:var(--mgk-coral); --violet:var(--mgk-violet); --ink:var(--mgk-ink); --muted:var(--mgk-muted); --panel:var(--mgk-panel); }
        .stApp { background:radial-gradient(circle at 8% 0%,rgba(124,92,255,.24),transparent 31%),radial-gradient(circle at 92% 12%,rgba(244,201,93,.12),transparent 24%),linear-gradient(160deg,#070b16 0%,#0b1020 52%,#151026 100%); color:var(--mgk-ink); font-family:'DM Sans','Noto Sans Myanmar',sans-serif; }
        .stApp::before { content:''; position:fixed; inset:0; pointer-events:none; opacity:.12; background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px); background-size:48px 48px; mask-image:linear-gradient(to bottom,black,transparent 78%); }
        h1,h2,h3 { font-family:'Space Grotesk','Noto Sans Myanmar',sans-serif !important; letter-spacing:-.04em; }
        h1 { font-size:clamp(2.2rem,6vw,4.8rem) !important; background:linear-gradient(100deg,#fff 18%,#f4c95d 54%,#9c8bff 90%); -webkit-background-clip:text; color:transparent; margin-bottom:.2rem !important; }
        h2 { color:#fff !important; }
        [data-testid='stHeader'] { background:rgba(8,9,16,.72); }
        [data-testid='stSidebar'] { background:linear-gradient(180deg,rgba(20,22,35,.96),rgba(11,12,20,.98)); border-right:1px solid rgba(255,255,255,.09); }
        [data-testid='stSidebar'] h2 { font-size:1.3rem !important; }
        [data-testid='stExpander'] { background:linear-gradient(145deg,rgba(41,37,65,.72),rgba(19,21,32,.72)); border:1px solid rgba(255,255,255,.12); border-radius:20px; box-shadow:0 20px 60px rgba(0,0,0,.22); }
        [data-testid='stFileUploader'] { background:linear-gradient(145deg,rgba(28,39,70,.92),rgba(18,25,46,.9)); border:1px solid rgba(244,201,93,.5); border-radius:24px; min-height:190px; padding:30px 18px; box-shadow:0 18px 54px rgba(0,0,0,.3),0 0 0 1px rgba(124,92,255,.16) inset; }
        [data-testid='stFileUploader'] section { background:transparent; border:0; }
        [data-testid='stFileUploaderDropzone'] { background:linear-gradient(135deg,rgba(244,201,93,.1),rgba(124,92,255,.12)); border:1px dashed rgba(244,201,93,.45); border-radius:18px; min-height:125px; }
        .stButton > button { width:100%; border:1px solid rgba(244,201,93,.34); border-radius:12px; padding:.72rem 1rem; color:#101522 !important; background:linear-gradient(135deg,#f4c95d,#ff8b6b 52%,#7c5cff); box-shadow:0 10px 28px rgba(124,92,255,.22); font-weight:800; transition:transform .18s ease,box-shadow .18s ease; }
        [data-testid='stPopover'] > button, [data-testid='stFileUploaderDropzone'] button { color:#fff !important; background:linear-gradient(135deg,#ff4f67,#844cff) !important; border:1px solid rgba(255,255,255,.25) !important; font-weight:700 !important; }
        [data-testid='stFileUploaderDropzone'] small, [data-testid='stFileUploaderDropzone'] span, [data-testid='stFileUploaderDropzone'] p, label, .stCaption, [data-testid='stCaptionContainer'] { color:#d8d9e5 !important; }
        .stButton > button:hover { transform:translateY(-2px); box-shadow:0 14px 34px rgba(244,201,93,.28); border-color:rgba(244,201,93,.7); }
        .stButton > button:active { transform:scale(.98); }
        .stDownloadButton > button { width:100%; border-radius:12px; color:#ffdce0; background:rgba(255,79,103,.12); border:1px solid rgba(255,79,103,.38); }
        .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb='select'] > div, .stNumberInput input { color:#fff !important; background:rgba(8,9,16,.72) !important; border:1px solid rgba(255,255,255,.13) !important; border-radius:11px !important; }
        /* Dropdowns are selection-only on mobile: keep their hidden search input from summoning the keyboard. */
        .stSelectbox [data-baseweb='select'] input { pointer-events:none !important; caret-color:transparent !important; user-select:none !important; -webkit-user-select:none !important; }
        .stSelectbox [data-baseweb='select'] [role='combobox'] { cursor:pointer !important; }
        .stSelectbox [data-baseweb='select'] { touch-action:manipulation; }
        @media (max-width:700px) { .stSelectbox [data-baseweb='select'] input:focus { outline:none !important; } }
        .stRadio input, .stButton button { -webkit-tap-highlight-color:transparent; }
        .stTextArea textarea:focus, .stTextInput input:focus { border-color:var(--coral) !important; box-shadow:0 0 0 1px var(--coral) !important; }
        @media (max-width:700px) {
            [data-testid='stAppViewContainer'] .main .block-container { max-width:100% !important; padding:.45rem .55rem 1.2rem !important; }
            h1 { font-size:1.55rem !important; line-height:1.05 !important; }
            h2 { font-size:1.18rem !important; line-height:1.12 !important; }
            h3 { font-size:.98rem !important; line-height:1.15 !important; }
            p, label, [data-testid='stCaptionContainer'] { font-size:.72rem !important; line-height:1.25 !important; }
            div[data-testid='stHorizontalBlock'] { flex-direction:column !important; flex-wrap:wrap !important; gap:.35rem !important; align-items:stretch !important; }
            div[data-testid='stHorizontalBlock'] > div[data-testid='column'] { min-width:0 !important; width:100% !important; flex:1 1 100% !important; padding-left:0 !important; padding-right:0 !important; }
            div[data-testid='stHorizontalBlock']:has([data-testid='stVerticalBlockBorderWrapper']) { flex-direction:row !important; flex-wrap:nowrap !important; align-items:stretch !important; }
            div[data-testid='stHorizontalBlock']:has([data-testid='stVerticalBlockBorderWrapper']) > div[data-testid='column'] { width:0 !important; flex:1 1 0 !important; padding-left:.12rem !important; padding-right:.12rem !important; }
            div[data-testid='stHorizontalBlock'] .stButton > button { white-space:normal !important; overflow:hidden !important; text-overflow:ellipsis !important; font-size:.67rem !important; padding:.28rem .22rem !important; }
            .stSelectbox, .stTextInput, .stTextArea, .stSlider, .stColorPicker, .stAlert { margin-bottom:.22rem !important; }
            [data-testid='stWidgetLabel'] { font-size:.72rem !important; line-height:1.1 !important; margin-bottom:.12rem !important; }
            .stSelectbox div[data-baseweb='select'] > div { min-height:2rem !important; padding-top:.12rem !important; padding-bottom:.12rem !important; }
            .stButton > button, .stDownloadButton > button { min-height:1.78rem !important; padding:.24rem .38rem !important; font-size:.68rem !important; line-height:1.08 !important; border-radius:8px !important; }
            [data-testid='stVerticalBlockBorderWrapper'] { padding:.45rem !important; border-radius:10px !important; }
            [data-testid='stFileUploader'] { min-height:120px !important; padding:12px 10px !important; border-radius:14px !important; }
            [data-testid='stFileUploaderDropzone'] { min-height:80px !important; border-radius:11px !important; }

            .stTextArea textarea { min-height:7rem !important; padding:.5rem !important; }
            [data-testid='stVideo'] video { max-height:38vh !important; object-fit:contain !important; }
            [data-testid='stAudio'] audio { height:34px !important; }
            .recap-hero { padding:12px 14px !important; margin:4px 0 10px !important; border-radius:14px !important; }
            .stAlert { padding:.42rem .58rem !important; font-size:.76rem !important; }

            h1, h2, h3 { margin-top:.45rem !important; margin-bottom:.28rem !important; }
        }
        [data-testid='stAlert'] { border-radius:14px; border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.06); }
        [data-testid='stMetric'] { background:rgba(255,255,255,.055); border:1px solid rgba(255,255,255,.09); border-radius:15px; padding:12px; }
        .recap-hero { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:26px 28px; margin:8px 0 22px; border:1px solid rgba(255,255,255,.11); border-radius:24px; background:linear-gradient(120deg,rgba(49,37,83,.84),rgba(28,23,42,.64) 52%,rgba(75,27,41,.42)); box-shadow:0 24px 70px rgba(0,0,0,.28); position:relative; overflow:hidden; }
        .recap-hero::after { content:'✦  REC  /  01'; position:absolute; right:24px; bottom:14px; color:rgba(255,255,255,.25); letter-spacing:.18em; font-size:.7rem; }
        .hero-kicker { color:#ff8b9b; text-transform:uppercase; letter-spacing:.2em; font-size:.7rem; font-weight:700; margin-bottom:8px; }
        .hero-copy { color:#b9b9ca; margin:0; max-width:580px; }
        .hero-orb { width:74px; height:74px; flex:none; display:grid; place-items:center; border-radius:23px; color:#fff; font-size:2rem; background:linear-gradient(145deg,var(--coral),var(--violet)); box-shadow:0 0 45px rgba(255,79,103,.36); transform:rotate(-8deg); }
        .section-label { color:#ff8b9b; font-weight:700; letter-spacing:.14em; font-size:.72rem; text-transform:uppercase; margin:18px 0 8px; }
        .video-meta-strip { margin:2px 0 0; padding:2px 8px 0; border-top:1px solid rgba(255,255,255,.12); color:#aeb3c8; }
        .effect-wizard-card { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:10px 0 8px; padding:14px 16px; border:1px solid rgba(244,201,93,.36); border-radius:15px; background:linear-gradient(135deg,rgba(255,79,103,.16),rgba(124,92,255,.18)); }
        .effect-wizard-card span { color:#f4c95d; font-size:.7rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
        .effect-wizard-card strong { color:#fff; font-size:.95rem; }
        .video-meta-strip [data-testid='stVerticalBlock'] { gap:0 !important; }
        .video-meta-strip [data-testid='stCaptionContainer'] { margin:0 !important; padding:0 !important; font-size:.68rem !important; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        [data-testid='stRadio'] > div[role='radiogroup'] { display:flex; flex-wrap:nowrap; gap:.18rem; overflow-x:auto; padding:2px 0 5px; scrollbar-width:none; }
        [data-testid='stRadio'] > div[role='radiogroup']::-webkit-scrollbar { display:none; }
        [data-testid='stRadio'] label { min-width:max-content; padding:4px 7px !important; border-radius:999px; background:rgba(255,255,255,.055); border:1px solid rgba(255,255,255,.08); font-size:.68rem !important; }
        @media (max-width:700px) { .recap-hero { padding:20px; } .hero-orb { width:54px; height:54px; border-radius:17px; font-size:1.4rem; } .recap-hero::after { display:none; } [data-testid='stFileUploader'] { min-height:128px; padding:14px 10px; border-radius:18px; } [data-testid='stFileUploaderDropzone'] { min-height:86px; border-radius:14px; } .video-meta-strip { padding-left:2px; padding-right:2px; } }
        @media (min-width:701px) { [data-testid='stRadio'] > div[role='radiogroup'] { overflow-x:visible; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    apply_cinematic_theme()
    st.markdown("<div class='top-menu-only'>", unsafe_allow_html=True)
    render_menu()
    st.markdown("</div>", unsafe_allow_html=True)
    with st.container(border=True):
        upload = st.file_uploader("Video ထည့်ပါ", type=["mp4", "mov", "mkv", "avi", "webm"])
        if not upload:
            st.warning("Video ဖိုင်တစ်ခု ထည့်ပါ။")
            st.stop()

        if "video_path" not in st.session_state or st.session_state.get("video_name") != upload.name:
            st.session_state.video_path = save_upload(upload)
            st.session_state.video_name = upload.name
            st.session_state.script = ""
            st.session_state.audio = None
            st.session_state.copyright_video_path = None
            st.session_state.blurred_video_path = None
            st.session_state.blur_masks = None
            st.session_state.blur_enabled = False
            st.session_state.output_video = None
            st.session_state.workflow_step = 1

        video_duration = get_video_duration(st.session_state.video_path)
        persistent_preview = (st.session_state.get("output_video") or st.session_state.get("blurred_video_path") or st.session_state.get("copyright_video_path") or st.session_state.video_path)
        st.markdown("<div class='workflow-fixed-preview'>", unsafe_allow_html=True)
        st.video(persistent_preview)
        st.caption("မူရင်း Video Preview · အဆင့် ၄ ပြီးရင် Blur ပြီးသား Video ကို ဒီနေရာမှာ ဆက်ပြပါမယ်။")
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<div class='section-label'>PANEL ပြောင်းရန် · တစ်ခုချင်းစီနှိပ်ပါ</div>", unsafe_allow_html=True)
        if "workflow_step" not in st.session_state:
            st.session_state.workflow_step = 1
        workflow_steps = ["1 Script", "2 Copyright Edit", "3 Blue Mask", "4 အသံသွင်းရန်", "5 စာတန်းထိုးရန်"]
        active_step = st.session_state.get("workflow_step", 1)
        selected_step = st.radio("Workflow step", workflow_steps, index=active_step - 1, horizontal=True, label_visibility="collapsed", key="workflow_nav")
        st.session_state.workflow_step = workflow_steps.index(selected_step) + 1
        active_step = st.session_state.workflow_step
        skip_col, _ = st.columns([1, 5])
        with skip_col:
            if st.button("ကျော်မယ် →", key="workflow-skip", use_container_width=True):
                st.session_state.workflow_step = min(5, active_step + 1)
                st.rerun()
        st.markdown("<div class='video-meta-strip'>", unsafe_allow_html=True)
        meta_name, meta_duration, meta_size = st.columns([1.55, .8, .8])
        with meta_name:
            st.caption(f"{upload.name}")
        with meta_duration:
            st.caption(f"⏱ {format_duration(video_duration) if video_duration else '--:--'}")
        with meta_size:
            st.caption(f"{upload.size / 1024 / 1024:.1f} MB")
        st.markdown("</div>", unsafe_allow_html=True)
    # Keep selected output settings available to later groups after Step 1 is hidden.
    platform = st.session_state.get("target_platform", "YouTube")
    quality_mode = st.session_state.get("quality_mode", "720")
    duration_seconds = video_duration or 0
    saved_speed_label = st.session_state.get("video_speed", "1×")
    try:
        audio_speed = float(str(saved_speed_label).replace("×", ""))
    except (TypeError, ValueError):
        audio_speed = 1.0
    voiceover_duration = (len(st.session_state.audio) / (24000 * 2)) if st.session_state.get("audio") else None

    if active_step == 1:
        right = st.container()
        with right:
            st.subheader("1 · Video + ဘာသာစကား")
            setup_row_one = st.columns(3, gap="small")
            with setup_row_one[0]:
                language = "Burmese (မြန်မာ)"
            with setup_row_one[1]:
                platform = st.selectbox("ဒီ Video ကို ဘယ်မှာတင်မလဲ?", list(PLATFORM_OPTIONS.keys()), key="target_platform")
            with setup_row_one[2]:
                quality_mode = st.selectbox("Video Quality", ["720", "1280"], key="quality_mode", help="720 သို့မဟုတ် 1280 ကိုရွေးပါမယ်။")
            platform_preset = PLATFORM_OPTIONS[platform]
            st.info(f"{platform} အတွက် {platform_preset['ratio']} · {platform_preset['width']}×{platform_preset['height']}")
            setup_row_two = st.columns(3, gap="small")
            with setup_row_two[0]:
                source_kind = st.selectbox("Video အမျိုးအစား", ["Original movie", "Already-made recap"], key="source_kind", help="Original movie သို့မဟုတ် Already-made recap")
            with setup_row_two[1]:
                mode = st.selectbox("လုပ်ဆောင်မည့်ပုံစံ", ["Faithful full translation", "Original recap"], help="ဘာသာပြန် သို့မဟုတ် Recap")
            with setup_row_two[2]:
                tone = st.selectbox("Script style", ["Cinematic and concise", "Fast TikTok style", "Calm documentary", "Dramatic storyteller"])
            duration_valid = True
            is_original_movie_recap = source_kind == "Original movie" and mode == "Original recap"
            if video_duration:
                st.caption(f"Video အရှည်: {format_duration(video_duration)}")
                if is_original_movie_recap:
                    default_duration = min(60, video_duration)
                    duration_text = st.text_input("Recap အရှည် (mm:ss)", value=format_duration(default_duration), help="Original movie recap အရှည်")
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
                        st.info("Already-made recap ဖြစ်လို့ မူရင်း Video အရှည်အတိုင်း ဆက်လုပ်ပါမယ်။")
                    else:
                        st.info("Faithful Translation Mode: Video ထဲက အကြောင်းအရာအားလုံးကို မကျန်အောင် ပြန်ပေးမယ်။")
            else:
                duration_seconds = 0
                duration_valid = False
                st.warning("Video အရှည်ကို မဖတ်နိုင်ပါ။ MP4 အဖြစ် ပြောင်းပြီး ထပ်တင်ပါ။")
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
                        st.session_state.workflow_step = 2
                        st.rerun()
                    except Exception as exc:
                        st.error(api_error_message(exc))

    if active_step == 1:
        st.divider()
        st.subheader("1 · Script")
        st.caption("ဒီ Version မှာ Subtitle မထည့်သေးပါ။ Script ကို ကိုယ်တိုင်ပြင်ပြီး အသံထုတ်နိုင်ပါတယ်။")
        st.session_state.script = st.text_area("Editable narration", st.session_state.script, height=130)
        st.download_button("Script ဒေါင်းရန်", st.session_state.script, file_name="recap-script.txt", mime="text/plain")

    if active_step == 2:
        st.divider()
        st.subheader("2 · Copyright Edit")
        st.caption("🔒 Anti-Copyright System · လိုအပ်တဲ့ Effect တွေကို ရွေးပါ")
        effect_steps = [
            ("effect_mirror", "Mirror", "ဘယ်/ညာလှန်"),
            ("effect_auto_zoom", "Auto Zoom", "အလိုအလျောက် Zoom"),
            ("effect_color_filter", "Color Filter", "အရောင်ပြောင်း"),
            ("effect_pitch_alter", "Audio Pitch Alter", "အသံ Pitch ပြောင်း"),
        ]
        effect_left, effect_right = st.columns(2, gap="small")
        for position, (effect_key, effect_label, effect_hint) in enumerate(effect_steps):
            target_col = effect_left if position % 2 == 0 else effect_right
            with target_col:
                with st.container(border=True):
                    selected = st.checkbox(effect_label, value=bool(st.session_state.get(effect_key, False)), key=f"effect-card-{effect_key}", help=effect_hint)
                    st.session_state[effect_key] = selected
        with st.container(border=True):
            st.session_state.effect_freeze_bypass = st.checkbox("Freeze + Zoom Bypass (Advanced)", value=bool(st.session_state.get("effect_freeze_bypass", False)), key="effect-freeze-bypass")
        st.caption("ရွေးထားတဲ့ Effect တွေကို အခု Apply လုပ်လိုက်တာနဲ့ အပေါ်က Video Preview ထဲမှာ တစ်ခါတည်း ထည့်ပေးပါမယ်။")
        if st.button("Apply Edit →", key="apply-copyright-edit", type="primary", use_container_width=True):
            with st.spinner("Copyright Edit ကို Video ပေါ်မှာ ထည့်နေပါတယ်..."):
                try:
                    edit_source = Path(st.session_state.get("copyright_video_path") or st.session_state.video_path)
                    st.session_state.copyright_video_path = str(apply_copyright_edit(
                        edit_source,
                        bool(st.session_state.get("effect_mirror", False)),
                        bool(st.session_state.get("effect_auto_zoom", False)),
                        bool(st.session_state.get("effect_color_filter", False)),
                        bool(st.session_state.get("effect_pitch_alter", False)),
                    ))
                    st.session_state.blurred_video_path = None
                    st.session_state.output_video = None
                    st.session_state.workflow_step = 3
                    st.success("Copyright Edit ပြီးပါပြီ။ အပေါ်က Preview လည်း ပြောင်းပြီး Blue Mask အဆင့်ကို ဖွင့်ထားပါတယ်။")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Copyright Edit မအောင်မြင်ပါ: {exc}")

    if active_step == 3:
        st.divider()
        st.subheader("3 · Blue Mask")
        st.caption("Copy Edit ပြီးမှ နောက်အဆင့်မှာ Blur Mask ဆက်လုပ်နိုင်ပါတယ်။")
        st.caption("မူရင်း Video ကို အရင်ကြည့်ပြီး Blur လုပ်ချင်တဲ့ စာတန်း/နေရာကို ရွေးပါ။ Frame ပုံမထွက်ရင်လည်း အောက်က Video ကိုကြည့်ပြီး Box ကို ဆက်ချိန်နိုင်ပါတယ်။")
        st.caption("ဒီ Video ကို အပေါ်က Persistent Preview မှာပဲ ကြည့်ပြီး Blur Box ကို ချိန်ပါ။")
        try:
            blur_source = Path(st.session_state.get("copyright_video_path") or st.session_state.video_path)
            duration_for_frames = get_video_duration(blur_source)
            frame_times = list(sampled_frame_times(duration_for_frames) or [0.0])
            if not frame_times:
                frame_times = [0.0]
            frame_labels = [f"{format_duration(round(value))} မှာ Frame" for value in frame_times]
            selected_label = st.selectbox("စာတန်းအများဆုံး/အရှည်ဆုံးပေါ်တဲ့ Frame ရွေးပါ", frame_labels, key="blur_frame_choice")
            selected_frame_time = frame_times[frame_labels.index(selected_label)]
            preview_frame = None
            try:
                preview_frame = extract_preview_frame(blur_source, selected_frame_time)
            except Exception as frame_exc:
                st.warning(f"Frame ပုံကို မထုတ်နိုင်သေးပါ။ မူရင်း Video Preview ကိုကြည့်ပြီး Box ကို ဆက်ချိန်နိုင်ပါတယ်: {frame_exc}")
            dimensions = get_video_dimensions(blur_source)
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
            if not isinstance(st.session_state.get("blur_masks"), list) or not st.session_state.blur_masks:
                st.session_state.blur_masks = [{"x": original_width // 10, "y": original_height * 3 // 4, "width": original_width // 2, "height": max(10, original_height // 8)}]
            blur_enabled = st.toggle("BLUR MASK (MAX 3)", value=st.session_state.get("blur_enabled", False))
            st.session_state.blur_enabled = blur_enabled
            if blur_enabled:
                st.caption("Copy Edit မှာ ရွေးထားတဲ့ Anti-Copyright Effect တွေကို Blur Export နဲ့အတူ အသုံးချပါမယ်။")
            else:
                st.caption("Blur Mask ပိတ်ထားပါက Blur မလုပ်ဘဲ ဆက်သွားပါမယ်။")
            if not blur_enabled:
                st.session_state.blurred_video_path = None
                st.session_state.pop("subtitle_text", None)
            if blur_enabled:
                control_col, preview_col = st.columns([1, 1.35], gap="medium")
                with control_col:
                    if st.button("+ Add Blur Box", disabled=len(st.session_state.blur_masks) >= 3):
                        st.session_state.blur_masks.append({"x": original_width // 10, "y": original_height // 3, "width": original_width // 3, "height": max(10, original_height // 8)})
                        st.rerun()
                    background_style = st.selectbox("Background Style", ["None", "Transparent", "Solid Box"], help="Solid Box ကိုရွေးရင် ရွေးထားတဲ့အရောင်နဲ့ ဖုံးပေးမယ်။")
                    solid_box_color = st.color_picker("Solid Box Color", "#16B8FF", key="solid_box_color") if background_style == "Solid Box" else "#16B8FF"
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
                with preview_col:
                    if preview_frame:
                        st.image(draw_blur_selection(preview_frame.resize((preview_width, preview_height)), preview_boxes, background_style), use_container_width=True)
                    else:
                        st.info("Frame preview မရသေးပါ။ အပေါ်က မူရင်း Video ကိုကြည့်ပြီး Box နေရာကို ချိန်နိုင်ပါတယ်။ Apply လုပ်တဲ့အခါ Video တစ်ခုလုံးမှာ Blur ထည့်ပေးပါမယ်။")
                    if st.button("Apply Blur →", type="primary", use_container_width=True):
                        with st.spinner("Blur Mask ကို Video တစ်ခုလုံးပေါ်မှာ ထည့်နေပါတယ်..."):
                            st.session_state.blurred_video_path = None
                            try:
                                boxes = [(int(mask["x"]), int(mask["y"]), int(mask["width"]), int(mask["height"])) for mask in st.session_state.blur_masks]
                                st.session_state.blurred_video_path = str(apply_region_blur(blur_source, boxes, blur_strength, background_style, solid_box_color))
                                st.session_state.output_video = None
                                st.session_state.workflow_step = 4
                                st.session_state.audio = None
                                st.success("Blur Mask အောင်မြင်ပါပြီ။ အပေါ်က Preview လည်း ပြောင်းပြီး Voiceover အဆင့်ကို ဖွင့်ထားပါတယ်။")
                                st.rerun()
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

        if not st.session_state.get("blurred_video_path"):
            st.info("Blue Mask မအောင်မြင်သေးတဲ့အတွက် Blur နဲ့ မြန်မာစာတန်းထိုးအဆင့်ကို Skip လုပ်ထားပါတယ်။ Voiceover အဆင့်ကို ဆက်လုပ်နိုင်ပါတယ်။")

    if active_step == 4:
        st.divider()
        st.subheader("4 · အသံသွင်းရန်")
        voice_row = st.columns(3, gap="small")
        with voice_row[0]:
            speed_label = st.selectbox("Audio Speed", ["0.5×", "0.75×", "1×", "1.25×", "1.5×", "2×"], index=2, key="video_speed")
        with voice_row[1]:
            voice = st.selectbox("Gemini voice", list(VOICE_OPTIONS.keys()), format_func=lambda item: f"{item} · {VOICE_OPTIONS[item]}")
        with voice_row[2]:
            style = st.selectbox("Voice style", ["cinematic narrator", "warm narrator", "energetic creator", "serious documentary"])
        audio_speed = float(speed_label.replace("×", ""))
        if voiceover_duration and video_duration:
            sync_plan = calculate_sync_plan(video_duration, voiceover_duration, audio_speed)
            adjusted_audio_duration = sync_plan["adjusted_audio"]
            auto_video_speed = sync_plan["video_speed"]
            final_duration = sync_plan["target"]
            st.info(f"အသံ {format_duration(round(voiceover_duration))} → {format_duration(round(adjusted_audio_duration))} · Video Auto-fit {auto_video_speed:.2f}× · Final {format_duration(round(final_duration))}")
        else:
            st.caption("Voiceover ထွက်ပြီးနောက် Audio မူရင်းအရှည်၊ ချိန်ပြီးအရှည်နဲ့ Video Auto-fit Speed ကို ပြပါမယ်။")
        if st.button("Voiceover ထုတ်မယ်", type="primary", use_container_width=True):
            with st.spinner(f"Gemini {voice} အသံနဲ့ Voiceover ပြုလုပ်နေပါတယ်..."):
                try:
                    st.session_state.audio = generate_voiceover(st.session_state.script, voice, style)
                    raw_duration = max(0.1, len(st.session_state.audio) / (24000 * 2))
                    adjusted_duration = raw_duration / max(0.5, min(2.0, float(audio_speed)))
                    script_for_srt = str(st.session_state.get("script", "")).strip()
                    if not script_for_srt:
                        raise ValueError("Script မရှိသေးလို့ SRT မဖန်တီးနိုင်ပါ။ Script အဆင့်ကို အရင်ပြီးအောင်လုပ်ပါ။")
                    st.session_state.generated_srt = normalize_srt_text(script_to_srt(script_for_srt, adjusted_duration))
                    if not st.session_state.generated_srt.strip():
                        raise ValueError("SRT အလွတ်ဖြစ်နေပါတယ်။ Script ကို ပြန်စစ်ပြီး Voiceover ပြန်ထုတ်ပါ။")
                    st.session_state.subtitle_srt_editor = st.session_state.generated_srt
                    st.session_state.subtitle_text = srt_to_plain_text(st.session_state.generated_srt)
                    st.session_state.pop("audio_preview", None)
                    st.session_state.pop("audio_preview_token", None)
                    st.session_state.subtitle_enabled = True
                    st.session_state.workflow_step = 5
                    st.success("Voiceover နဲ့ အချိန်ပါ SRT ကို တစ်ခါတည်း ဖန်တီးပြီးပါပြီ။")
                    st.rerun()
                except Exception as exc:
                    st.error(api_error_message(exc))

    if active_step == 5 or (st.session_state.get("audio") and active_step == 4):
        raw_audio = st.session_state.get("audio") or b""
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
        if active_step == 4:
            st.caption(f"Voiceover Preview · မူရင်း {format_duration(round(voiceover_duration or 0))} → Speed {audio_speed:g}× → {format_duration(round(preview_duration))}")
            st.audio(pcm_to_wav(preview_audio), format="audio/wav")
            st.download_button("ချိန်ပြီး Voiceover ဒေါင်းရန်", pcm_to_wav(preview_audio), file_name="recap-voiceover-adjusted.wav", mime="audio/wav")

        subtitle_enabled = True if active_step == 5 else st.session_state.get("subtitle_enabled", False)
        st.session_state.subtitle_enabled = subtitle_enabled
        if subtitle_enabled and active_step == 5:
            st.divider()
            st.subheader("5 · စာတန်းထိုး (SRT)")
            st.caption("Voiceover အရှည်အတိုင်း Timing ချပြီးသား SRT ကို ဒီနေရာမှာ ပြင်နိုင်ပါတယ်။")
            generated_srt = normalize_srt_text(st.session_state.get("generated_srt", ""))
            if not generated_srt and st.session_state.get("script", "").strip():
                audio_duration = max(0.1, (len(st.session_state.get("audio") or b"") / (24000 * 2)) or float(video_duration or 60))
                generated_srt = normalize_srt_text(script_to_srt(st.session_state.script, audio_duration))
                st.session_state.generated_srt = generated_srt
            if generated_srt and not st.session_state.get("subtitle_srt_editor", "").strip():
                st.session_state["subtitle_srt_editor"] = generated_srt
            st.caption("Voiceover ပြီးတာနဲ့ Script ကနေ Timing ပါတဲ့ SRT ကို ဒီတစ်ကွက်ထဲ အလိုအလျောက်ဖြည့်ပေးထားပါတယ်။ လိုအပ်ရင် ဒီကွက်ထဲမှာ တိုက်ရိုက်ပြင်နိုင်ပါတယ်။")
            subtitle_srt = st.text_area("အချိန်ပါ SRT စာတန်းထိုး", height=180, key="subtitle_srt_editor")
            if subtitle_srt.strip():
                st.session_state.generated_srt = normalize_srt_text(subtitle_srt)
            else:
                st.warning("SRT မရှိသေးပါ။ Script နဲ့ Voiceover ကို အရင်ဖန်တီးပါ။")
            srt_download = st.session_state.get("generated_srt", "").encode("utf-8")
            st.download_button("SRT ဖိုင်ရယူရန်", srt_download, file_name="burmese-subtitles.srt", mime="text/plain", use_container_width=True, key="download_srt_immediate")
            if st.session_state.get("generated_srt", ""):
                st.success("Voiceover နဲ့ SRT ကို တစ်ခါတည်းဖန်တီးထားပြီးပါပြီ · Timing ပါပြီးသား · အများဆုံး ၂ ကြောင်း")
            subtitle_text = srt_to_plain_text(st.session_state.get("generated_srt", ""))
            st.session_state.subtitle_text = subtitle_text
            st.markdown("**Subtitle Test Side · စာတန်းထိုးပုံကို ဒီမှာ တစ်ခါတည်းကြည့်ပါ**")
            subtitle_controls, subtitle_preview = st.columns([0.9, 1.1], gap="small")
            with subtitle_controls:
                subtitle_font = st.selectbox("Font", list(FONT_FILES.keys()), key="subtitle_font")
                subtitle_size = st.slider("Font size", 16, 72, 34, key="subtitle_size")
                subtitle_text_color = st.color_picker("စာတန်းအရောင်", "#FFFFFF", key="subtitle_text_color")
                subtitle_outline_color = st.color_picker("Outline အရောင်", "#000000", key="subtitle_outline_color")
                subtitle_background_mode = st.selectbox("Background", ["Transparent", "Solid background"], key="subtitle_background_mode")
                subtitle_background_color = st.color_picker("Background အရောင်", "#000000", key="subtitle_background_color")
                subtitle_background_opacity = st.slider("Background opacity", 0, 100, 55, key="subtitle_background_opacity")
                subtitle_position = "Bottom"
                subtitle_x = st.slider("X · ဘယ် ↔ ညာ", 0, 100, int(st.session_state.get("subtitle_x", 50)), key="subtitle_x")
                subtitle_y = st.slider("Y · အပေါ် ↕ အောက်", 0, 100, int(st.session_state.get("subtitle_y", 86)), key="subtitle_y")
            with subtitle_preview:
                try:
                    subtitle_test_source = st.session_state.get("blurred_video_path") or st.session_state.get("copyright_video_path") or st.session_state.video_path
                    subtitle_test_frame = extract_preview_frame(Path(subtitle_test_source), 0)
                    subtitle_test_image = render_live_subtitle_preview(subtitle_test_frame, subtitle_text, subtitle_font, subtitle_size, subtitle_text_color, subtitle_outline_color, subtitle_background_mode, subtitle_background_color, subtitle_background_opacity, subtitle_position, subtitle_x, subtitle_y)
                    st.image(subtitle_test_image, caption="Live Subtitle Test · Video ပေါ်မှာ စာတန်းပေါ်မယ့်ပုံ", use_container_width=True)
                except Exception as preview_exc:
                    st.info(f"Subtitle Preview မရသေးပါ။ Export လုပ်တဲ့အခါ Style ထည့်ပေးပါမယ်: {preview_exc}")
            st.success(f"Subtitle style သိမ်းပြီးပါပြီ · {subtitle_font} · {subtitle_text_color} · {subtitle_background_color}")
        else:
            st.session_state.pop("subtitle_text", None)

        if active_step == 5 and st.button("စာတန်းထိုး + အသံ + Video ပေါင်းထုတ်မယ် →", use_container_width=True):
            if not st.session_state.get("audio"):
                st.warning("စာတန်းထိုး Video Export မလုပ်ခင် Voiceover ကို အရင်ထုတ်ပါ။")
                st.stop()
            with st.spinner("Audio Speed ချိန်ပြီး Video ကို အရှည်ကိုက်အောင် Auto-fit လုပ်နေပါတယ်..."):
                try:
                    source_video = Path(st.session_state.get("blurred_video_path") or st.session_state.get("copyright_video_path") or st.session_state.video_path)
                    subtitle_srt_to_burn = ""
                    if st.session_state.get("subtitle_enabled", False) and st.session_state.get("subtitle_text", "").strip():
                        source_duration = get_video_duration(source_video) or video_duration or 60
                        narration_duration = max(0.1, len(st.session_state.audio) / (24000 * 2))
                        shared_sync_plan = calculate_sync_plan(source_duration, narration_duration, audio_speed)
                        shared_final_duration = shared_sync_plan["target"]
                        subtitle_srt_to_burn = normalize_srt_text(st.session_state.get("subtitle_srt_editor", st.session_state.get("generated_srt", "")))
                        export_width, export_height = render_dimensions(platform, st.session_state.get("quality_mode", "720"))
                        subtitle_srt_to_burn = add_srt_position_tags(subtitle_srt_to_burn, st.session_state.get("subtitle_x", 50), st.session_state.get("subtitle_y", 86), export_width, export_height)
                    if not subtitle_srt_to_burn.strip():
                        st.warning("အောက်က SRT Box ထဲမှာ အချိန်ပါစာတန်းကို အရင်ဖြည့်ပါ။")
                        st.stop()
                    st.info(f"စာတန်းထိုး + Voiceover + Video ကို {format_duration(round(shared_final_duration if 'shared_final_duration' in locals() else preview_duration))} အရှည်တစ်ခုတည်းနဲ့ ပေါင်းထုတ်နေပါတယ်။")
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
                        subtitle_x=st.session_state.get("subtitle_x", 50),
                        subtitle_y=st.session_state.get("subtitle_y", 86),
                        effect_mirror=st.session_state.get("effect_mirror", False),
                        effect_auto_zoom=st.session_state.get("effect_auto_zoom", False),
                        effect_color_filter=st.session_state.get("effect_color_filter", False),
                        effect_pitch_alter=st.session_state.get("effect_pitch_alter", False),
                        quality_mode=st.session_state.get("quality_mode", "720"),
                    )
                    register_generation()
                    st.success("စာတန်းထိုး + အသံ + Video ကို တစ်ခါတည်းပေါင်းပြီး Final Video ရပါပြီ။")
                    st.rerun()
                except Exception as exc:
                    st.error(f"FFmpeg မအောင်မြင်ပါ: {exc}")

    if st.session_state.get("output_video"):
        st.download_button("Final Video ဒေါင်းရန်", st.session_state.output_video, file_name=f"movie-recap-{st.session_state.get('target_platform', 'video').lower()}.mp4", mime="video/mp4")


if __name__ == "__main__":
    main()
