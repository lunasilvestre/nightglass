"""The spatial layer (§M3).

Reads Sentinel-1 GRD granules, runs our own vessel detector over them, loads
AIS, and answers the one question §M3 exists to answer: which detections have no
AIS correspondence inside a space-time window.

The detector is ours, not a published layer. That distinction is load-bearing —
§3.1 is explicit that GFW's SAR detections are a *reference* layer to cross-check
against, and claiming someone else's detections as your computation is the kind
of thing that unravels in a technical round.
"""

from __future__ import annotations

__all__ = ["detect", "geodesy", "safe"]
