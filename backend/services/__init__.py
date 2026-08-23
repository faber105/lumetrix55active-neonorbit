# Install the persistent Pocket Option transport patch before service modules are used.
from backend.services import pocket_persistent_patch as _pocket_persistent_patch  # noqa: F401

# Keep AUTO scan status/journal live and make the 92% payout gate explicit in UI text.
from backend.services import auto_session_live_patch as _auto_session_live_patch  # noqa: F401

# Enable Smart Confluence in both AUTO modes.
from backend.services import smart_confluence_patch as _smart_confluence_patch  # noqa: F401

# Cloudflare relay lifecycle is owned explicitly by worker.main.
# Do not start background network tasks from package import side effects: many
# backend modules import backend.services, and doing so used to create a second
# outbound relay WebSocket that repeatedly replaced the real worker bridge.
