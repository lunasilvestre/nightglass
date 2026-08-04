"""Document RAG.

Ingest corpus -> chunk -> embed with bge-m3 -> Qdrant, then retrieval that
returns chunks carrying their document ID, title and classification marking, and
generation that is grounded strictly in what was retrieved.

The package is split along the one boundary this project cares about:

    fetch.py     ONLINE.  Provisioning. Runs on the provision network, once.
    everything   OFFLINE. Operation.    Runs inside the enclave, forever.

`fetch` is the only module here that opens a socket to the outside world, and it
is never imported by anything the enclave runs. That is the same split as
`make pull-models` versus `make up`, applied to documents instead of weights.
"""

from nightglass.rag.documents import Document, load_corpus
from nightglass.rag.index import DocumentIndex

__all__ = ["Document", "DocumentIndex", "load_corpus"]
