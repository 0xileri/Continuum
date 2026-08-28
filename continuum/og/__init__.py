"""The 0G integration layer — §5.2, §5.3 and §5.4.

Three capabilities, one seam. Everything in here crosses a process boundary into ``og-bridge/``
(Node), because 0G ships TypeScript and Go SDKs and this engine is Python. ``bridge.py`` owns that
boundary; ``compute.py``, ``storage.py`` and ``chain.py`` are thin, typed wrappers over it.

The whole layer is designed to **degrade visibly rather than fail closed or fake success**. Every
call returns a structured result carrying whether the 0G side actually happened and, if not, why.
A score computed while 0G was unreachable is still a valid score — it just carries
``attestation.type = "none"`` and no ``chain_ref``, and the dashboard renders it as unattested.
That is the same discipline the pre-0G phase applied to a missing Claude key: the honest failure is
a visible gap, never a plausible-looking placeholder.
"""

from continuum.og.bridge import BridgeError, BridgeUnavailable, call_bridge
from continuum.og.chain import ChainPublishResult, publish_score, registry_address
from continuum.og.compute import ComputeResult, reason_over_documents
from continuum.og.storage import StorageResult, put_feature_record, put_json

__all__ = [
    "BridgeError",
    "BridgeUnavailable",
    "call_bridge",
    "ChainPublishResult",
    "ComputeResult",
    "StorageResult",
    "publish_score",
    "put_feature_record",
    "put_json",
    "reason_over_documents",
    "registry_address",
]
