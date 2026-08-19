from __future__ import annotations
import json, os
from pathlib import Path

secrets = Path(__file__).resolve().parents[1] / 'runtime_secrets.json'
if secrets.exists():
    for k, v in json.loads(secrets.read_text()).items():
        os.environ.setdefault(str(k), str(v))

# The archived pocketoptionapi-async transport can report false auth timeouts
# against the current Socket.IO binary-event flow. AlphaPulse uses its own
# read-only market transport for auth/history only; no order methods exist.
from backend.services.pocket_direct import DirectPocketOptionClient
from backend.services.pocketoption_otc import PocketOptionOTCService

def _direct_market_client(self):
    return DirectPocketOptionClient(self.ssid, self.demo)

PocketOptionOTCService._make_client = _direct_market_client

from backend.main import app
