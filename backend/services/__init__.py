# Install the persistent Pocket Option transport patch before service modules are used.
from backend.services import pocket_persistent_patch as _pocket_persistent_patch  # noqa: F401

# Keep AUTO scan status/journal live and make the 92% payout gate explicit in UI text.
from backend.services import auto_session_live_patch as _auto_session_live_patch  # noqa: F401
