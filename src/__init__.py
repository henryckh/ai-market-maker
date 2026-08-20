"""
AI Market Maker package initialization
Ensures proper imports for OpenClaw environment
"""

import os
import sys
from pathlib import Path

# Ensure src directory is in path
src_dir = Path(__file__).parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Set default environment variables for OpenClaw
os.environ.setdefault("AIMM_DESK_STRATEGY_PRESET", "default")
os.environ.setdefault("STRATEGY_INTERVAL_SEC", "180")

# Export main components for easier imports
__all__ = ["agents", "backtest", "config", "tools", "workflow", "main"]

print(f"✅ AI Market Maker initialized (Python {sys.version_info.major}.{sys.version_info.minor})")
