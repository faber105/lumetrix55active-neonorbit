from __future__ import annotations
import json, os
from pathlib import Path
secrets=Path(__file__).resolve().parents[1]/'runtime_secrets.json'
if secrets.exists():
    for k,v in json.loads(secrets.read_text()).items(): os.environ.setdefault(str(k),str(v))
import backend.services.pocketoption_compat  # installs read-only market-data auth compatibility
from backend.main import app
