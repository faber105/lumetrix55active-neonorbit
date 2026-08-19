from __future__ import annotations
import json, os
from pathlib import Path

secrets = Path(__file__).resolve().parents[1] / 'runtime_secrets.json'
if secrets.exists():
    for k, v in json.loads(secrets.read_text()).items():
        os.environ.setdefault(str(k), str(v))

# Use the minimal read-only Socket.IO transport for Pocket market data. It sends
# the captured browser auth frame unchanged and never exposes trading methods to
# AlphaPulse's market-data service.
from backend.services import pocketoption_otc as _po_service
from backend.services.pocket_direct import DirectPocketOptionClient


def _make_direct_market_client(self):
    return DirectPocketOptionClient(self.ssid, is_demo=self.demo)


_po_service.PocketOptionOTCService._make_client = _make_direct_market_client

from backend.main import app
