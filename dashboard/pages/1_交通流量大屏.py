"""Optional Streamlit multipage entry for the same dashboard view."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.app import render_dashboard


render_dashboard("交通流量大屏")

