"""Configuration — the only place an AOI is allowed to be named.

EXECUTION_SPEC §3.1 makes this a hard design requirement rather than a nicety:
two AOIs, one codebase, swap by config. Portugal is the demo AOI, Denmark the
validation AOI, and the difference between them is data availability, not code.
That is also what "deploy into customer environments" means in practice.

So an AOI is defined entirely by environment variables following a naming
convention -- `AOI_<NAME>_*` -- and adding a third one is two lines in `.env`
with no code change at all. Nothing downstream may hardcode a bbox.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AisSourceName = Literal["dma", "aisstream", "gfw"]


class ConfigError(RuntimeError):
    """Raised at start-up, not halfway through a spatial join."""


@dataclass(frozen=True)
class BBox:
    """A geographic box in the order everything except aisstream uses.

    Stored as min_lon, min_lat, max_lon, max_lat -- GIS, STAC and ASF order.
    aisstream.io is the exception and takes ``[[lat, lon], [lat, lon]]``; that
    conversion lives in :meth:`as_aisstream` precisely so nobody has to
    remember it. Getting it backwards yields a stream that is silently empty
    rather than an error, which cost real time during pre-dev.
    """

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @classmethod
    def parse(cls, raw: str, *, origin: str) -> BBox:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ConfigError(
                f"{origin}: expected 4 comma-separated numbers "
                f"(min_lon,min_lat,max_lon,max_lat), got {len(parts)}: {raw!r}"
            )
        try:
            min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
        except ValueError as exc:
            raise ConfigError(f"{origin}: non-numeric value in {raw!r}") from exc

        # Catch a transposed bbox here rather than as an empty result set later.
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ConfigError(
                f"{origin}: degenerate or transposed bbox {raw!r}. "
                "Order is min_lon,min_lat,max_lon,max_lat."
            )
        if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
            raise ConfigError(f"{origin}: longitude out of range in {raw!r}")
        # Catches only swaps that push a latitude past ±90. It will NOT catch a
        # transposed AOI in general: Lisbon's box swapped to "38.0,-10.5,39.5,-8.5"
        # is a perfectly valid box off Somalia, and Kattegat's lands in the
        # Arabian Sea. Both parse clean and then quietly return nothing.
        #
        # That is precisely why axis order is converted in exactly one place
        # (:meth:`as_aisstream`) rather than at each call site — validation
        # cannot be the safety net here, so the conversion must not be repeated.
        if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            raise ConfigError(
                f"{origin}: latitude out of range in {raw!r}. "
                "A latitude above 90 means lat/lon are swapped."
            )
        return cls(min_lon, min_lat, max_lon, max_lat)

    def as_list(self) -> list[float]:
        """STAC / ASF / `stac_search` order."""
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    def as_aisstream(self) -> list[list[float]]:
        """aisstream.io's ``[[lat, lon], [lat, lon]]`` -- the inverted one."""
        return [[self.min_lat, self.min_lon], [self.max_lat, self.max_lon]]

    def as_wkt(self) -> str:
        return (
            f"POLYGON(({self.min_lon} {self.min_lat}, {self.max_lon} {self.min_lat}, "
            f"{self.max_lon} {self.max_lat}, {self.min_lon} {self.max_lat}, "
            f"{self.min_lon} {self.min_lat}))"
        )

    def __str__(self) -> str:
        return f"{self.min_lon},{self.min_lat},{self.max_lon},{self.max_lat}"


@dataclass(frozen=True)
class AOI:
    """One area of interest, resolved from `AOI_<NAME>_*` environment keys."""

    name: str
    bbox: BBox
    ais_source: AisSourceName
    # Sentinel-1 overpass windows, UTC "HH:MM-HH:MM". Measured from the
    # catalogue during pre-dev, not taken from a revisit table -- see NOTES.md.
    # Optional: an AOI is usable without them, they just make AIS slicing exact.
    pass_descending: str | None = None
    pass_ascending: str | None = None

    @classmethod
    def from_env(cls, name: str, env: dict[str, str] | None = None) -> AOI:
        env = os.environ if env is None else env  # type: ignore[assignment]
        key = name.strip().upper()

        raw_bbox = env.get(f"AOI_{key}_BBOX", "").strip()
        if not raw_bbox:
            known = sorted(
                k[len("AOI_") : -len("_BBOX")].lower()
                for k in env
                if k.startswith("AOI_") and k.endswith("_BBOX") and env[k].strip()
            )
            raise ConfigError(
                f"NIGHTGLASS_AOI={name!r} but AOI_{key}_BBOX is unset or empty. "
                f"Configured AOIs: {', '.join(known) or '(none)'}"
            )

        source = env.get(f"AOI_{key}_AIS_SOURCE", "").strip().lower()
        if source not in ("dma", "aisstream", "gfw"):
            raise ConfigError(
                f"AOI_{key}_AIS_SOURCE must be one of dma|aisstream|gfw, got {source!r}"
            )

        return cls(
            name=name.strip().lower(),
            bbox=BBox.parse(raw_bbox, origin=f"AOI_{key}_BBOX"),
            ais_source=source,  # type: ignore[arg-type]
            pass_descending=env.get(f"AOI_{key}_PASS_DESCENDING", "").strip() or None,
            pass_ascending=env.get(f"AOI_{key}_PASS_ASCENDING", "").strip() or None,
        )

    @property
    def is_ground_truth(self) -> bool:
        """Whether this AOI's AIS may be quoted as a dark-vessel *rate*.

        Only the DMA feed may. aisstream was measured at ~17% of vessels
        against DMA over an identical bbox and clock window, so treating it as
        ground truth would mark ~83% of detections dark -- worse than the 40%
        failure mode §3.2 warns about. GFW detections are already AIS-matched
        upstream, so they are a reference layer, not an independent correlation.

        Consumers should gate rate language on this. See §7 and NOTES.md.
        """
        return self.ais_source == "dma"


class Settings(BaseSettings):
    """Everything the services need, read from the environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    aoi_name: str = Field(default="lisbon", alias="NIGHTGLASS_AOI")

    # Detector default, §5's `min_length_m`.
    min_length_m: float = Field(default=15.0, alias="NIGHTGLASS_MIN_LENGTH_M")

    postgres_user: str = Field(default="nightglass", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="nightglass", alias="POSTGRES_DB")
    postgres_host: str = Field(default="postgis", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    qdrant_host: str = Field(default="qdrant", alias="QDRANT_HOST")
    qdrant_port: int = Field(default=6333, alias="QDRANT_PORT")
    qdrant_collection: str = Field(default="nightglass_docs", alias="QDRANT_COLLECTION")

    # M2 document corpus. Two roots, because the two halves of the corpus are
    # held differently: `corpus/synthetic` is committed (a fresh clone must be
    # able to reproduce the demo), `data/corpus/normalized` is fetched and
    # gitignored (some of it carries no reuse licence). Ingest reads both and
    # cannot tell them apart -- see corpus/README.md.
    corpus_dir: Path = Field(default=Path("/app/data/corpus"), alias="NIGHTGLASS_CORPUS_DIR")
    synthetic_dir: Path = Field(
        default=Path("/app/corpus/synthetic"), alias="NIGHTGLASS_SYNTHETIC_DIR"
    )
    rag_top_k: int = Field(default=8, alias="NIGHTGLASS_RAG_TOP_K")
    # Retrieval score floor. Left unset by default: a threshold picked before
    # measuring the corpus is a guess, and a wrong one turns answerable
    # questions into refusals, which is a worse failure than it looks because
    # it looks like the honesty feature working.
    rag_min_score: float | None = Field(default=None, alias="NIGHTGLASS_RAG_MIN_SCORE")

    ollama_host: str = Field(default="http://ollama:11434", alias="OLLAMA_HOST")
    ollama_chat_model: str = Field(
        default="qwen2.5:14b-instruct-q4_K_M", alias="OLLAMA_CHAT_MODEL"
    )
    ollama_embed_model: str = Field(default="bge-m3", alias="OLLAMA_EMBED_MODEL")

    @property
    def aoi(self) -> AOI:
        """Resolved fresh each access — cheap, and keeps tests able to monkeypatch
        the environment without fighting a cache."""
        return AOI.from_env(self.aoi_name)

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    @property
    def corpus_roots(self) -> list[Path]:
        """Where `make ingest` looks for documents, committed half first."""
        return [self.synthetic_dir, self.corpus_dir / "normalized"]

    def describe(self) -> None:
        """Print resolved config. Never prints a secret — only whether it is set.

        Called from the container entrypoint so a bad AOI fails at start-up
        with a readable message instead of surfacing later as an empty result.
        """
        aoi = self.aoi  # raises ConfigError here if misconfigured
        print("NIGHTGLASS config")
        print(f"  aoi           {aoi.name}")
        print(f"  bbox          {aoi.bbox}")
        print(f"  ais source    {aoi.ais_source}"
              f"{'' if aoi.is_ground_truth else '   (demonstration source — not ground truth)'}")
        if aoi.pass_descending or aoi.pass_ascending:
            print(f"  overpass UTC  desc {aoi.pass_descending or '—'}"
                  f"  asc {aoi.pass_ascending or '—'}")
        print(f"  min length    {self.min_length_m} m")
        print(f"  ollama        {self.ollama_host}  "
              f"[{self.ollama_chat_model}, {self.ollama_embed_model}]")
        print(f"  qdrant        {self.qdrant_url}")
        print(f"  postgis       {self.postgres_host}:{self.postgres_port}/{self.postgres_db}")


settings = Settings()
