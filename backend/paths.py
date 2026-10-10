"""Absolute paths so the app works regardless of the working directory (gunicorn, tests)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"
ROADS_JSON = DATA_DIR / "vizag_roads.json"
EVENTS_JSON = DATA_DIR / "special_events.json"
ACCIDENTS_CSV = DATA_DIR / "vizag_accidents.csv"
