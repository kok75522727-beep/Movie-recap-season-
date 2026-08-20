from pathlib import Path


def main() -> None:
    source = Path('app.py').read_text(encoding='utf-8')
    assert 'st.color_picker("Solid Box Color"' in source
    assert 'solid_box_color: str = "#16B8FF"' in source
    assert 'color=c=0x{safe_color}@0.78' in source
    assert 'apply_region_blur(st.session_state.video_path, boxes, blur_strength, background_style, solid_box_color)' in source
    print('blur color checks OK')


if __name__ == '__main__':
    main()
