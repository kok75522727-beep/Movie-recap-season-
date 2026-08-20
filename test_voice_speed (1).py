from pathlib import Path
import subprocess
import tempfile


def atempo_filter(speed: float) -> str:
    speed = max(0.5, min(4.0, speed))
    factors = []
    while speed > 2.0:
        factors.append('atempo=2.0')
        speed /= 2.0
    while speed < 0.5:
        factors.append('atempo=0.5')
        speed /= 0.5
    factors.append(f'atempo={speed:.6f}')
    return ','.join(factors)


def main() -> None:
    raw = b'\x00\x00' * 24000 * 2  # 2 seconds, 24 kHz, mono, s16le
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'source.pcm'
        output = Path(directory) / 'fast.pcm'
        source.write_bytes(raw)
        result = subprocess.run([
            'ffmpeg', '-y', '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', str(source),
            '-af', atempo_filter(2.0), '-f', 's16le', '-ar', '24000', '-ac', '1', str(output),
        ], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        output_seconds = output.stat().st_size / (24000 * 2)
        assert 0.85 <= output_seconds <= 1.15, output_seconds
    app = Path('app.py').read_text()
    assert 'adjust_pcm_audio_speed' in app
    assert 'st.audio(pcm_to_wav(preview_audio)' in app
    assert 'atempo' in app
    print('voice speed checks OK')


if __name__ == '__main__':
    main()
