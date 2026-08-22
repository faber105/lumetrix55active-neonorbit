# Install the persistent Pocket Option transport patch before service modules are used.
from backend.services import pocket_persistent_patch as _pocket_persistent_patch  # noqa: F401
