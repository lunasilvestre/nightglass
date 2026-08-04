"""`draft_intrep` (§5), and the guard that keeps it from quoting a rate.

§7's argument is that output which cannot be traced cannot be graded, so it
cannot enter the intelligence cycle however fluent it reads. This module is that
argument applied to the one number in the project that is easiest to state and
hardest to defend: the proportion of detections with no AIS correspondence.

**Two independent things have to be true before that proportion may be quoted,
and only one of them was guarded before M4.**

*Source side.* `CorrelationResult.rate_is_quotable` already checks it: a rate is
only meaningful against a feed that is complete. Over Denmark the DMA feed is
ground truth; the self-collected aisstream feed was measured at ~1.4 messages
per vessel per 4 minutes against ~40 expected, so treating it as ground truth
would mark most of a harbour dark.

*Precision side.* Nothing guarded it. A rate is a fraction, and the source side
only validates the denominator's other half — it says nothing about whether the
*numerator* is vessels. Over the Kattegat, with ground-truth AIS, 21 of 35
detections match at a median 104 m and every AIS vessel over 200 m inside the
footprint is recovered: the matcher is validated. But **40%** of detections are
unmatched against a published base rate of ~5%, and that number went *up* when
duplicate detections of the same hull were merged away — because it was the
matched detections that were duplicated, not the unmatched ones. The excess is
coastal clutter and isolated false alarms. The matcher being right does not make
the detector's precision right, and a rate computed from a numerator of unknown
composition is a number about the detector's false alarms wearing the clothes of
a number about ships.

So `DETECTOR_PRECISION_VALIDATED` is False, everywhere, today. The consequence
is deliberately absolute: this system reports *"here are N detections I matched,
with the space–time reasoning shown"*, never a dark-vessel rate. Three layers
enforce it, in increasing order of how much they can be trusted, the same
structure `rag/answer.py` uses for citations:

1. The deterministic claims below state counts and never compute a proportion.
2. The generation prompt says not to.
3. :func:`scrub_rate_claims` removes any surviving claim that states one. Only
   the third is a guarantee, and it is the reason the other two are not enough.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Any

import httpx

from nightglass.config import settings
from nightglass.schemas import INTREP, Chunk, Claim, CorrelationResult
from nightglass.tools.base import DMA_ATTRIBUTION

#: Whether this deployment's detector has a measured precision, region by
#: region, good enough to put in the denominator of a published rate. It does
#: not, and the honest place for that fact is a constant a reader can grep for
#: rather than a paragraph in a README. Flipping it to True is a claim about
#: measurement, so it should only ever change in the same commit as the
#: measurement — see NOTES.md finding 24 and the shoreline sweep in the README.
DETECTOR_PRECISION_VALIDATED = False

PRECISION_CAVEAT = (
    "The detector's precision is not validated. Over the Danish validation AOI "
    "40% of detections have no AIS correspondence against a published base rate "
    "of ~5%. The excess is coastal clutter and isolated false alarms, not dark "
    "vessels: a shoreline-buffer sweep concentrates it near shore, and after "
    "duplicate detections of the same hull are merged the unmatched fraction "
    "rises rather than falls, because it is the matched detections that were "
    "duplicated. Counts of matched pairs are defensible; a dark-vessel rate is not."
)

PRECISION_CAVEAT_PT = (
    "A precisão do detetor não está validada. Na AOI de validação dinamarquesa, "
    "40% das deteções não têm correspondência AIS contra uma taxa de base "
    "publicada de ~5%. O excesso é ruído costeiro e falsos alarmes isolados, não "
    "embarcações escuras: uma varredura de buffer de linha de costa concentra-o "
    "junto à costa, e depois de fundidas as deteções duplicadas do mesmo casco a "
    "fração sem correspondência sobe em vez de descer, porque eram as deteções "
    "correspondidas que estavam duplicadas. Contagens de pares correspondidos "
    "são defensáveis; uma taxa de embarcações escuras não é."
)

DARK_IS_A_LEAD = (
    "A detection with no AIS correspondence is a lead, not a conclusion. "
    "Revisit gaps, terrestrial coverage limits, transponder failure, class B "
    "low-power transponders and vessels not required to carry AIS all produce "
    "one. The system surfaces candidates; the analyst adjudicates."
)

DARK_IS_A_LEAD_PT = (
    "Uma deteção sem correspondência AIS é uma pista, não uma conclusão. "
    "Lacunas de revisita do satélite, limites da cobertura terrestre, avaria do "
    "transponder, transponders classe B de baixa potência e embarcações não "
    "obrigadas a transportar AIS produzem todas o mesmo resultado. O sistema "
    "apresenta candidatos; o analista adjudica."
)


@dataclass(frozen=True)
class RateVerdict:
    """Whether a proportion may be stated, and every reason it may not."""

    quotable: bool
    reasons: list[str] = field(default_factory=list)


# Each reason is a complete sentence, capitalised and terminated. They are
# joined into a caveat and also surfaced individually to the local model, and a
# fragment that reads fine in a list runs into its neighbour once joined —
# "…in this report. no detections were matched A precisão…".
_NO_MATCHES = {
    "en": "No detections were matched against AIS in this correlation.",
    "pt": "Nenhuma deteção foi correspondida com AIS nesta correlação.",
}
_NOT_GROUND_TRUTH = {
    "en": (
        "The AIS feed ({sources}) is not ground truth, so the denominator is "
        "incomplete and a proportion computed from it would report feed "
        "sparsity as vessel behaviour."
    ),
    "pt": (
        "A fonte AIS ({sources}) não é ground truth, pelo que o denominador "
        "está incompleto e uma proporção calculada a partir dele reportaria a "
        "escassez da fonte como comportamento das embarcações."
    ),
}


def rate_verdict(
    correlation: CorrelationResult, *, language: str = "en"
) -> RateVerdict:
    """Both sides of the fraction, checked independently.

    Returns `quotable=True` only if the AIS feed is ground truth *and* the
    detector's precision has been measured. Today the second is False by
    construction, so this returns False with the precision reason attached even
    over Denmark — which is the point, and it is the case worth testing: the
    Danish AIS *is* ground truth, `CorrelationResult.rate_is_quotable` *is* true,
    and the rate is still not quotable, for the other reason.
    """
    lang = "pt" if str(language).lower().startswith("pt") else "en"
    reasons: list[str] = []
    if not correlation.matches:
        reasons.append(_NO_MATCHES[lang])
    elif not correlation.rate_is_quotable:
        sources = sorted({m.ais_source or "unknown" for m in correlation.matches})
        reasons.append(_NOT_GROUND_TRUTH[lang].format(sources=", ".join(sources)))
    if not DETECTOR_PRECISION_VALIDATED:
        reasons.append(PRECISION_CAVEAT_PT if lang == "pt" else PRECISION_CAVEAT)
    return RateVerdict(quotable=not reasons, reasons=reasons)


# -- the check after the fact -------------------------------------------------

# A proportion, in either demo language. `rate`/`taxa` are included as words
# because "a dark-vessel rate of one in four" states a proportion without ever
# printing a percent sign.
_PROPORTION = re.compile(
    r"\d+(?:[.,]\d+)?\s*%"
    r"|\bper\s?cent\b|\bpercent\w*|\bpor\s?cento\b"
    r"|\brates?\b|\btaxas?\b"
    r"|\bfractions?\b|\bfra[cç][çc]?[ãa]?o\b"
    r"|\bproportions?\b|\bpropor[çc][ãa]o\b"
    r"|\bone\s+in\s+\w+\b|\bum\s+em\s+cada\b",
    re.IGNORECASE,
)

# Darkness, in either demo language — and its complement, because "75% of
# detections matched" states the same number as "25% were dark" and would
# otherwise walk straight through a guard aimed only at the word "dark".
#
# `correspondência` is matched on its own rather than only after `não`: the
# phrasing that got through a first draft of this was "25% das deteções não
# **têm** correspondência AIS", where the negation and the noun are two words
# apart. Matching the noun alone is safe because the proportion token is what
# separates a rate from a count — "15 deteções não têm correspondência AIS" has
# no proportion in it and survives.
_DARKNESS = re.compile(
    r"\bdark\b|\bescur\w*"
    r"|\bunmatched\b|\bnot\s+matched\b"
    r"|\bmatched\s+detections?\b|\bdetections?\s+(?:\w+\s+){0,2}matched\b"
    r"|\bcorrespond[êe]nci\w*|\bcorrespondid\w*"
    r"|\b(?:no|without)\s+AIS\b|\bsem\s+AIS\b",
    re.IGNORECASE,
)


def states_a_rate(text: str) -> bool:
    """True if this sentence expresses AIS correspondence as a proportion.

    Counts survive — "15 of 60 detections had no AIS correspondence" is a fact
    about this scene and is exactly what the system is allowed to say. What does
    not survive is the same fact divided by its denominator, because that reads
    as a property of the water rather than of one image and one feed.

    Honest about what it is: a backstop over the phrasings this model actually
    produces, not a proof that no rate can be expressed. Something determined
    could still write one out in words. The reason that is tolerable is that
    this is the third of three layers and the weakest guarantee of the three —
    the templated findings never compute a proportion at all, so there is no
    correct number for a generated claim to be paraphrasing.
    """
    return bool(_PROPORTION.search(text) and _DARKNESS.search(text))


def scrub_rate_claims(claims: list[Claim]) -> tuple[list[Claim], list[Claim]]:
    """Split claims into those that may be published and those that state a rate.

    Applied only to generated claims. The structural caveats are not scrubbed —
    they have to be able to use the words "dark-vessel rate" in order to say
    that this report does not quote one.
    """
    kept: list[Claim] = []
    removed: list[Claim] = []
    for claim in claims:
        (removed if states_a_rate(claim.text) else kept).append(claim)
    return kept, removed


# -- the report ---------------------------------------------------------------

_T = {
    "en": {
        "title": "INTREP — {aoi} — {date}",
        "window": (
            "The area of interest {aoi} was searched between {start} and {end} UTC; "
            "the catalogue returned {n_scenes} Sentinel-1 granule(s)."
        ),
        "scene": (
            "Scene {scene} was acquired at {acq} UTC in {mode} mode, "
            "polarisations {pols}, and is the granule this report is drawn from."
        ),
        "detections": (
            "The onboard CFAR detector found {n} candidate vessel(s) within the "
            "area of interest in that scene; the smallest measures {min_len:.0f} m."
        ),
        "matched": (
            "{n} of those detections correspond to a vessel reporting AIS, after "
            "interpolating each vessel's position onto the acquisition instant and "
            "correcting for SAR azimuth displacement; median separation {median:.0f} m."
        ),
        "dark": (
            "{n} detection(s) have no AIS correspondence in {source} within "
            "{radius:.0f} m and the search window. Each is a lead for adjudication."
        ),
        "none": "No detection in this scene was left without an AIS correspondence.",
        "lead": (
            "Detection {det} at {lat:.5f} N, {lon:.5f} E, extent {length}, has no "
            "AIS correspondence in {source} and is put forward for adjudication."
        ),
        "truncated": (
            "{shown} of {total} unmatched detections are listed individually above; "
            "the remainder are in the correlation result."
        ),
        "no_rate": "No proportion of unmatched detections is stated in this report. ",
        "weak_feed": (
            "At least one match came from a feed that is not ground truth, so "
            "absence of a match in this report is weaker evidence than a match."
        ),
        "skipped": (
            "{n} further scene(s) in the search window were not correlated: "
            "{ids}. Correlation is bounded to one scene per call; this report "
            "covers only the scene named above."
        ),
        "scrubbed": (
            "{n} generated claim(s) were removed before drafting because they "
            "stated a rate of unmatched detections."
        ),
        "no_narrative": (
            "The document assessment section was omitted: {why}. The findings "
            "above are unaffected — they are read from the database, not generated."
        ),
        "no_docs": (
            "No supporting documents were retrieved, so this report contains "
            "findings only and no doctrinal assessment."
        ),
        "no_ais": (
            "NO AIS was available for this acquisition window, so none of the "
            "{n} detection(s) above has been assessed against AIS. They are "
            "detections, not dark detections, and nothing here says whether any "
            "vessel was transmitting. This report carries no correlation finding."
        ),
        "draft": (
            "DRAFT — NOT RELEASABLE until reviewed and released at the human gate."
        ),
    },
    "pt": {
        "title": "INTREP — {aoi} — {date}",
        "window": (
            "A área de interesse {aoi} foi pesquisada entre {start} e {end} UTC; "
            "o catálogo devolveu {n_scenes} granulo(s) Sentinel-1."
        ),
        "scene": (
            "A cena {scene} foi adquirida às {acq} UTC em modo {mode}, "
            "polarizações {pols}, e é o granulo de onde este relatório é extraído."
        ),
        "detections": (
            "O detetor CFAR próprio encontrou {n} embarcação(ões) candidata(s) "
            "dentro da área de interesse nessa cena; a menor mede {min_len:.0f} m."
        ),
        "matched": (
            "{n} dessas deteções correspondem a uma embarcação que reporta AIS, após "
            "interpolar a posição de cada embarcação para o instante de aquisição e "
            "corrigir o deslocamento em azimute do SAR; separação mediana {median:.0f} m."
        ),
        "dark": (
            "{n} deteção(ões) não têm correspondência AIS em {source} dentro de "
            "{radius:.0f} m e da janela de pesquisa. Cada uma é uma pista a adjudicar."
        ),
        "none": "Nenhuma deteção nesta cena ficou sem correspondência AIS.",
        "lead": (
            "A deteção {det} em {lat:.5f} N, {lon:.5f} E, extensão {length}, não tem "
            "correspondência AIS em {source} e é apresentada para adjudicação."
        ),
        "truncated": (
            "{shown} de {total} deteções sem correspondência estão listadas "
            "individualmente acima; as restantes estão no resultado da correlação."
        ),
        "no_rate": (
            "Nenhuma proporção de deteções sem correspondência é indicada neste "
            "relatório. "
        ),
        "weak_feed": (
            "Pelo menos uma correspondência veio de uma fonte que não é ground "
            "truth, pelo que a ausência de correspondência neste relatório é "
            "evidência mais fraca do que uma correspondência."
        ),
        "skipped": (
            "Mais {n} cena(s) na janela de pesquisa não foram correlacionadas: "
            "{ids}. A correlação está limitada a uma cena por chamada; este "
            "relatório cobre apenas a cena indicada acima."
        ),
        "scrubbed": (
            "{n} afirmação(ões) geradas foram removidas antes da redação por "
            "indicarem uma taxa de deteções sem correspondência."
        ),
        "no_narrative": (
            "A secção de apreciação documental foi omitida: {why}. Os achados "
            "acima não são afetados — são lidos da base de dados, não gerados."
        ),
        "no_docs": (
            "Não foram recuperados documentos de apoio, pelo que este relatório "
            "contém apenas achados e nenhuma apreciação doutrinária."
        ),
        "no_ais": (
            "NÃO havia AIS disponível para esta janela de aquisição, pelo que "
            "nenhuma das {n} deteção(ões) acima foi avaliada contra AIS. São "
            "deteções, não deteções escuras, e nada aqui indica se alguma "
            "embarcação estava a transmitir. Este relatório não contém qualquer "
            "achado de correlação."
        ),
        "draft": (
            "RASCUNHO — NÃO DIVULGÁVEL até revisão e libertação no controlo humano."
        ),
    },
}

_MAX_LEADS = 10

_SYSTEM = """\
You are drafting the assessment section of an intelligence report (INTREP) \
inside an air-gapped maritime analysis cell. You have no internet access and no \
knowledge you may rely on: the CONTEXT below is the only admissible source.

Rules, in order of priority:

1. Assert nothing that is not in the CONTEXT. The FINDINGS are already stated \
elsewhere in the report — do not restate them, and do not draw numerical \
conclusions from them.
2. Every claim must cite the chunk ids it came from, exactly as they appear in \
the CONTEXT labels. Never invent a chunk id.
3. NEVER state a rate, percentage, fraction or proportion of detections that \
lack AIS. This deployment's detector precision is not validated, so such a \
number would be unsupportable. Counts are fine; proportions are not.
4. Never describe a vessel as illegal, evading, suspicious or non-compliant. A \
detection without an AIS match is a lead for an analyst, not a finding.
5. Write in the SAME LANGUAGE as the report language given below.
6. Two to four claims. Each one self-contained, no numbering, no chunk id in \
the text. Say what the CONTEXT establishes about how such findings should be \
interpreted or handled.
7. If the CONTEXT supports nothing relevant, return an empty claims list.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
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
        }
    },
    "required": ["claims"],
}


def draft_intrep(
    correlation: CorrelationResult,
    context_chunks: list[Chunk] | None = None,
    *,
    language: str = "en",
    narrative: bool = True,
    ollama_host: str | None = None,
    chat_model: str | None = None,
    timeout: float = 120.0,
) -> INTREP:
    """§5: `draft_intrep(correlation, context_chunks) -> INTREP`.

    Every claim carries the scene, detection and chunk ids that support it, so
    `INTREP.unsupported_claims` is empty by construction rather than by
    inspection. The report is always `releasable=False` — §M5's human gate is
    the only thing that may flip it, and a drafter that could mark its own work
    releasable would make the gate decorative.

    The factual claims are templated from the correlation, not generated. They
    are statements about rows in a database, and asking a language model to
    restate a count it was handed introduces a failure mode with no upside. The
    model is used only for the assessment section, over retrieved documents, and
    what it writes goes through the same citation check as §M2 and then through
    :func:`scrub_rate_claims`.
    """
    chunks = list(context_chunks or [])
    lang = "pt" if str(language).lower().startswith("pt") else "en"
    t = _T[lang]

    from nightglass.tools.base import now

    generated_at = now()
    verdict = rate_verdict(correlation, language=lang)

    claims = _factual_claims(correlation, t)
    dropped_rates: list[Claim] = []
    narrative_failed: str | None = None

    if narrative and chunks:
        try:
            generated = _assessment_claims(
                correlation,
                chunks,
                lang=lang,
                ollama_host=ollama_host or settings.ollama_host,
                chat_model=chat_model or settings.ollama_chat_model,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 — a missing narrative is not a failed report
            narrative_failed = f"{type(exc).__name__}: {exc}"
            generated = []
        kept, dropped_rates = scrub_rate_claims(generated)
        claims.extend(kept)

    from nightglass.rag.answer import combined_marking

    return INTREP(
        title=t["title"].format(
            aoi=correlation.aoi_name, date=f"{generated_at:%Y-%m-%d}"
        ),
        generated_at=generated_at,
        aoi_name=correlation.aoi_name,
        classification=str(combined_marking(claims, chunks)),
        releasable=False,
        claims=claims,
        caveats=_caveats(
            correlation,
            verdict,
            t,
            lang,
            dropped_rates=dropped_rates,
            narrative_failed=narrative_failed,
            chunks=chunks,
        ),
    )


def _factual_claims(c: CorrelationResult, t: dict[str, str]) -> list[Claim]:
    """Templated from the correlation. Every one carries the ids that support it."""
    scene_ids = [s.id for s in c.scenes]
    correlated = sorted({d.scene_id for d in c.detections}) or scene_ids[:1]
    matched = [m for m in c.matches if m.status == "matched"]
    dark = c.dark
    source = _feed_name(c)

    claims = [
        Claim(
            text=t["window"].format(
                aoi=c.aoi_name,
                start=f"{c.start:%Y-%m-%d %H:%M}",
                end=f"{c.end:%Y-%m-%d %H:%M}",
                n_scenes=len(c.scenes),
            ),
            scene_ids=scene_ids,
        )
    ]

    for scene in c.scenes:
        if scene.id in correlated:
            claims.append(
                Claim(
                    text=t["scene"].format(
                        scene=scene.id,
                        acq=f"{scene.acquisition_time:%Y-%m-%d %H:%M:%S}",
                        mode=scene.mode,
                        pols="+".join(scene.polarizations) or "—",
                    ),
                    scene_ids=[scene.id],
                )
            )

    claims.append(
        Claim(
            text=t["detections"].format(n=len(c.detections), min_len=_min_length(c)),
            scene_ids=correlated,
            detection_ids=[d.id for d in c.detections],
        )
    )

    if matched:
        distances = [m.distance_m for m in matched if m.distance_m is not None]
        claims.append(
            Claim(
                text=t["matched"].format(
                    n=len(matched), median=median(distances) if distances else 0.0
                ),
                scene_ids=correlated,
                detection_ids=[m.detection_id for m in matched],
            )
        )

    if dark:
        claims.append(
            Claim(
                text=t["dark"].format(
                    n=len(dark), source=source, radius=settings.match_radius_m
                ),
                scene_ids=correlated,
                detection_ids=[m.detection_id for m in dark],
            )
        )
        by_id = {d.id: d for d in c.detections}
        for m in dark[:_MAX_LEADS]:
            det = by_id.get(m.detection_id)
            if det is None:
                continue
            claims.append(
                Claim(
                    text=t["lead"].format(
                        det=det.id.split(":")[-1],
                        lat=det.lat,
                        lon=det.lon,
                        length=f"{det.length_m:.0f} m" if det.length_m else "not measured",
                        source=m.ais_source or source,
                    ),
                    scene_ids=[det.scene_id],
                    detection_ids=[det.id],
                )
            )
        if len(dark) > _MAX_LEADS:
            claims.append(
                Claim(
                    text=t["truncated"].format(shown=_MAX_LEADS, total=len(dark)),
                    detection_ids=[m.detection_id for m in dark],
                )
            )
    elif c.detections:
        claims.append(Claim(text=t["none"], scene_ids=correlated))

    return claims


def _min_length(c: CorrelationResult) -> float:
    lengths = [d.length_m for d in c.detections if d.length_m is not None]
    return min(lengths) if lengths else settings.min_length_m


def _feed_name(c: CorrelationResult) -> str:
    feeds = sorted({m.ais_source for m in c.matches if m.ais_source})
    return "+".join(feeds) if feeds else "the loaded AIS"


def _caveats(
    c: CorrelationResult,
    verdict: RateVerdict,
    t: dict[str, str],
    lang: str,
    *,
    dropped_rates: list[Claim],
    narrative_failed: str | None,
    chunks: list[Chunk],
) -> list[str]:
    """Built structurally, never generated, and never scrubbed.

    A caveat the drafter could forget to write is not a caveat, so these are
    assembled from the correlation's own fields — including the guard's reasons,
    which is what makes "no rate is quoted here" auditable rather than a habit.

    Not scrubbed, and they could not be: `scrub_rate_claims` removes any text
    pairing a proportion with darkness, and the second caveat below has to say
    the words "a dark-vessel rate" in order to say that this report does not
    quote one. Running the guard over its own explanation would delete it.
    """
    out = [DARK_IS_A_LEAD_PT if lang == "pt" else DARK_IS_A_LEAD]
    # First, because it subsumes everything below it: if nothing was assessed,
    # the reader needs to know that before reading a caveat about rates. An
    # empty `matches` list beside a non-empty `detections` list otherwise reads
    # as "nothing was dark", which is the opposite of what it means.
    if c.detections and not c.matches:
        out.append(t["no_ais"].format(n=len(c.detections)))
    if not verdict.quotable:
        out.append(t["no_rate"] + " ".join(verdict.reasons))
    if any("dma" in (m.ais_source or "").lower() for m in c.matches):
        # Verbatim, and in English in a Portuguese report on purpose: §8 requires
        # the DMA attribution line exactly as the authority words it, and a
        # translated licence condition is not the licence condition.
        out.append(DMA_ATTRIBUTION)
    if any(m.status == "matched" and not m.source_is_ground_truth for m in c.matches):
        out.append(t["weak_feed"])
    skipped = [s.id for s in c.scenes if s.id not in {d.scene_id for d in c.detections}]
    if skipped:
        out.append(t["skipped"].format(n=len(skipped), ids=", ".join(skipped)))
    if dropped_rates:
        out.append(t["scrubbed"].format(n=len(dropped_rates)))
    if narrative_failed:
        out.append(t["no_narrative"].format(why=narrative_failed))
    elif not chunks:
        out.append(t["no_docs"])
    out.append(t["draft"])
    return out


def _assessment_claims(
    c: CorrelationResult,
    chunks: list[Chunk],
    *,
    lang: str,
    ollama_host: str,
    chat_model: str,
    timeout: float,
) -> list[Claim]:
    """The one generated section, held to §M2's citation contract."""
    from nightglass.rag.answer import build_context, verify_claims

    findings = (
        f"{len(c.detections)} detection(s), {len(c.matches) - len(c.dark)} matched "
        f"to AIS, {len(c.dark)} with no AIS correspondence, over {c.aoi_name}."
    )
    payload = {
        "model": chat_model,
        "stream": False,
        "format": _SCHEMA,
        "options": {"temperature": 0, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"REPORT LANGUAGE: {'Portuguese' if lang == 'pt' else 'English'}\n\n"
                    f"FINDINGS (already stated in the report; context only):\n{findings}\n\n"
                    f"CONTEXT:\n\n{build_context(chunks)}\n\n"
                    "Write the assessment claims."
                ),
            },
        ],
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{ollama_host.rstrip('/')}/api/chat", json=payload)
        r.raise_for_status()
        raw = r.json()["message"]["content"]

    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return []
    claims, _dropped = verify_claims(parsed, chunks)
    return claims
