"""Load every config file. All weights, thresholds, anchors, peer lists,
tickers and DCF assumptions live in config/*.yaml — never in code."""
from pathlib import Path
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"


def load_all():
    cfg = {}
    for name in ("scoring", "tickers", "peers", "dcf", "sources"):
        with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as fh:
            cfg[name] = yaml.safe_load(fh)
    return cfg
