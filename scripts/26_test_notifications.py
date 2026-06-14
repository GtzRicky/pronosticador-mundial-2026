from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiniela.cli import app


if __name__ == "__main__":
    sys.argv.insert(1, "test-notifications")
    app()
