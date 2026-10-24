"""Phone access to a running Reezyn: a local page, paired per device.

The page is bound to loopback and published with ``tailscale serve``, so the
only way in is the tailnet. Sessions are bearer cookies issued by pairing, and
only their hashes touch disk.
"""

from .server import RemoteServer

__all__ = ["RemoteServer"]
