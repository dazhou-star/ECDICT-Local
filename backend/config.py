from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(os.environ.get("ECDICT_REPO", ROOT)).resolve()
DATABASE_DIR = Path(os.environ.get("ECDICT_DB_DIR", ROOT / "database")).resolve()
DATABASE_PATH = Path(os.environ.get("ECDICT_DB", DATABASE_DIR / "dictionary.db")).resolve()
FRONTEND_DIR = ROOT / "frontend"

HOST = os.environ.get("ECDICT_HOST", "0.0.0.0")
PORT = int(os.environ.get("ECDICT_PORT", "8000"))

DATABASE_DIR.mkdir(parents=True, exist_ok=True)
