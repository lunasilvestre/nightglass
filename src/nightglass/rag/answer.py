"""Grounded generation, and the refusal path.

The argument for this whole milestone is one transcript. Asked *"what is a dark
vessel?"* with no retrieved context, qwen2.5:14b replies that it "isn't a
standard term in common usage or in specific fields" and offers readings from
literature, philosophy, art symbolism and retrocomputing. Fluent, confident, and
containing no maritime meaning at all for the central term of this project.
Nothing in the model's priors holds the operational sense, and nothing in its
tone signals the gap.

So generation here is constrained three ways, in increasing order of how much
they can be trusted:

1. **The prompt** tells the model to assert nothing absent from the context.
   Cheap, and it does most of the work most of the time. It is also a request,
   and a request can be declined.
2. **A JSON schema** forces every claim to arrive attached to a list of chunk
   IDs, so an unsourced assertion has nowhere to live in the output format.
3. **A check after the fact** verifies every cited ID against what was actually
   retrieved. Invented IDs are dropped; claims left with none are discarded;
   an empty result is a refusal whatever the model said about itself.

Only the third is a guarantee. §7's framing is that output which cannot be
traced cannot be graded and therefore cannot enter the intelligence cycle — so
the untraceable parts are removed here rather than published with a caveat.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from nightglass.rag.documents import Marking
from nightglass.rag.index import DocumentIndex
from nightglass.schemas import Chunk, Claim, GroundedAnswer

REFUSAL_TEXT = "Not supported by available sources."

_SYSTEM = """\
You are an intelligence support assistant working inside an air-gapped maritime \
analysis cell. You have no internet access and no knowledge you may rely on: the \
CONTEXT below is the only admissible source of fact.

Rules, in order of priority:

1. Assert nothing that is not present in the CONTEXT. Not background, not \
definitions you already know, not what is obviously true. If the CONTEXT does not \
say it, it does not go in the answer.
2. Every claim you make must cite the chunk ids it came from, exactly as they \
appear in the CONTEXT labels. Never invent a chunk id. Never cite a chunk id that \
is not in the CONTEXT.
3. If the CONTEXT does not contain enough to answer, set "supported" to false and \
return an empty claims list. Saying you cannot answer is a correct and valuable \
outcome; guessing is not.
4. Answer in English, even when the question or the sources are in another \
language. Translate what you cite rather than quoting it untranslated.
5. Prefer the operational or technical meaning of a term over its everyday \
meaning. Terms of art in this domain frequently do not mean what they appear to \
mean in ordinary language.
6. Write each claim as one self-contained sentence. Do not number them, and do \
not repeat the chunk id inside the claim text.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "chunk_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "chunk_ids"],
            },
        },
    },
    "required": ["supported", "claims"],
}


def build_context(chunks: list[Chunk], *, max_chars: int = 14000) -> str:
    """Label each chunk with the id the model must cite it by.

    The classification marking is shown alongside. It is not decoration: the
    model is being asked to work with material that carries caveats, and a cell
    that hides markings from its own tooling should not be surprised when the
    tooling drops them.
    """
    parts: list[str] = []
    used = 0
    for c in chunks:
        title = c.title or c.doc_id
        block = f"[{c.chunk_id}] ({c.classification}) {title}\n{c.text}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts)


def answer_question(
    question: str,
    *,
    index: DocumentIndex,
    ollama_host: str,
    chat_model: str,
    k: int = 8,
    filters: dict | None = None,
    min_score: float | None = None,
    num_ctx: int = 8192,
    timeout: float = 300.0,
) -> GroundedAnswer:
    """Retrieve, generate, then verify. Verification is the load-bearing part."""
    now = datetime.now(UTC)
    chunks = index.search(question, k=k, filters=filters, min_score=min_score)

    if not chunks:
        return GroundedAnswer(
            question=question,
            answered=False,
            refusal=REFUSAL_TEXT,
            retrieved=[],
            model=chat_model,
            generated_at=now,
        )

    context = build_context(chunks)
    payload = {
        "model": chat_model,
        "stream": False,
        "format": _SCHEMA,
        "options": {"temperature": 0, "num_ctx": num_ctx},
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"CONTEXT:\n\n{context}\n\nQUESTION: {question}"},
        ],
    }

    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{ollama_host.rstrip('/')}/api/chat", json=payload)
        r.raise_for_status()
        raw = r.json()["message"]["content"]

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # A model that cannot produce parseable output has not produced a
        # citable answer, and there is nothing here to salvage honestly.
        return GroundedAnswer(
            question=question,
            answered=False,
            refusal=REFUSAL_TEXT,
            retrieved=chunks,
            model=chat_model,
            generated_at=now,
        )

    claims, dropped = verify_claims(parsed, chunks)
    supported = bool(parsed.get("supported")) and bool(claims)

    return GroundedAnswer(
        question=question,
        answered=supported,
        claims=claims if supported else [],
        refusal=None if supported else REFUSAL_TEXT,
        retrieved=chunks,
        classification=str(combined_marking(claims if supported else [], chunks)),
        dropped_citations=dropped,
        model=chat_model,
        generated_at=now,
    )


def verify_claims(parsed: dict, chunks: list[Chunk]) -> tuple[list[Claim], list[str]]:
    """Keep only what the retrieved chunks actually support.

    The one guarantee in this module, and the reason it is a separate function:
    it is pure, so it can be tested against a fabricated model response without
    a GPU, and every interesting failure mode of a citing RAG system is a case
    in that test rather than something noticed in production.

    A citation naming a chunk that was never retrieved is not a weak citation,
    it is a fabricated one, and it is removed rather than down-weighted. A claim
    with none left is removed with it.
    """
    known = {c.chunk_id for c in chunks}
    claims: list[Claim] = []
    dropped: list[str] = []

    for item in parsed.get("claims") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        valid: list[str] = []
        for cid in item.get("chunk_ids") or []:
            (valid if str(cid) in known else dropped).append(str(cid))
        if valid:
            claims.append(Claim(text=text, chunk_ids=list(dict.fromkeys(valid))))

    return claims, list(dict.fromkeys(dropped))


def combined_marking(claims: list[Claim], chunks: list[Chunk]) -> Marking:
    """The marking the answer must carry, from the chunks it actually cited.

    Note "cited", not "retrieved": marking an answer with the caveats of
    material that was pulled back but never used would over-mark it, and
    over-marking is how markings stop being believed.
    """
    by_id = {c.chunk_id: c for c in chunks}
    return Marking.combine(
        [Marking.parse(by_id[cid].classification) for cid in _cited(claims) if cid in by_id]
    )


def answer_ungrounded(
    question: str, *, ollama_host: str, chat_model: str, timeout: float = 300.0
) -> str:
    """The same question with no retrieval at all — the 'before' half of §M2.

    Kept in the shipped code rather than in a scratch script because the
    comparison is the milestone's evidence, and evidence that cannot be re-run
    on demand degrades into a claim about a screenshot.
    """
    payload = {
        "model": chat_model,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 4096},
        "messages": [{"role": "user", "content": question}],
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{ollama_host.rstrip('/')}/api/chat", json=payload)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()


def _cited(claims: list[Claim]) -> list[str]:
    return list(dict.fromkeys(cid for c in claims for cid in c.chunk_ids))
