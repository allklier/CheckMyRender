import os
import sys
import tempfile
from pathlib import Path

# tests never write into the user's real diagnostic log (D29)
os.environ["CMR_LOG_DIR"] = tempfile.mkdtemp(prefix="cmr_test_logs_")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "media"))
