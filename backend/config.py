"""
ARC global configuration.

Every tunable number that a judge might ask about ("why 0.85?") lives here,
so there is exactly one place to look and one place to change it.
"""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
DB_PATH = Path(os.environ.get("ARC_DB_PATH", DATA_DIR / "arc.db"))

# Load .env if present (GEMINI_API_KEY etc.) without making it mandatory.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass

# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
SEED = 42
TICK_SECONDS = 30            # one telemetry row every 30 simulated seconds
SHIFT_HOURS = 8
N_SHIFTS = 12                # historical shifts generated
LIVE_SHIFT_ID = "S13"        # the "today" shift used by the live demo

# Label horizon for the risk-trajectory classifier:
# "will an unsafe event happen within the next 15 minutes?"
LABEL_MIN_AHEAD_S = 0          # label = unsafe event within the next 15 min;
                               # alerts typically fire 5-15 min ahead (see train.py lead-time report)
LABEL_MAX_AHEAD_S = 15 * 60

# ---------------------------------------------------------------------------
# Arbitration Engine thresholds (see ARCHITECTURE.md)
# ---------------------------------------------------------------------------
# Tier 5 hard floor. NEVER calibrated per operator. If the model says the
# probability of an unsafe event in the next 15 min is >= this, we intervene.
HARD_SAFETY_THRESHOLD = 0.85

# Tier 4 default soft threshold. This one IS calibrated per operator by the
# feedback loop, but always clamped inside [SOFT_MIN, SOFT_MAX] and always
# strictly below the hard floor.
DEFAULT_SOFT_THRESHOLD = 0.50
SOFT_MIN = 0.35
SOFT_MAX = 0.65

# Tier 4 also fires if the task-time model says the current task is very
# likely to overrun (the message is "foreman notified — keep your pace",
# never "go faster").
TASK_DELAY_THRESHOLD = 0.70
TASK_DELAY_CAB_COOLDOWN_S = 3 * 3600   # operator hears "foreman knows" at most once per 3 h

# Tier 3 ambient icon: personal-baseline deviation z-score.
DEFAULT_AMBIENT_Z = 2.0

# "At most one thing at a time": minimum seconds between two surfaced
# tier-4 cards, and per-message cooldown so the same nudge doesn't nag.
TIER4_MIN_GAP_S = 5 * 60
MESSAGE_COOLDOWN_S = 15 * 60

# How long after a tier-4/5 event we watch telemetry to score Loop 1
# ("did it work?").
FEEDBACK_WINDOW_S = 5 * 60

# ---------------------------------------------------------------------------
# Google Gemini (Phase 6). Agents fall back to deterministic, data-grounded
# templates when no key is configured so the demo never breaks.
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GEMINI_MODEL = os.environ.get("ARC_GEMINI_MODEL", "gemini-2.5-flash")
