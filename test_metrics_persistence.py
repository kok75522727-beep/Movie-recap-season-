from pathlib import Path
import tempfile

import app


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app.METRICS_PATH = Path(directory) / 'generation_metrics.json'
        assert app.load_generation_log() == []
        app.register_generation()
        first = app.load_generation_log()
        assert len(first) == 1
        app.register_generation()
        second = app.load_generation_log()
        assert len(second) == 2
        assert app.METRICS_PATH.exists()
    print('metrics persistence checks OK')


if __name__ == '__main__':
    main()
