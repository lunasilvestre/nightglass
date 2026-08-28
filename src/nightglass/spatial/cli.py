"""`nightglass-spatial` — the M3 command surface.

One entry point rather than six scripts, for the same reason `nightglass-corpus`
is one: these are views of a single pipeline and the interesting thing is the
seam between them. Four subcommands touch the network, and they run in a
different image on a different network from every other one. Running any of them
inside the enclave fails at DNS resolution, which is correct.

    nightglass-spatial fetch-granules       ONLINE, provisioning only
    nightglass-spatial fetch-ais            ONLINE, provisioning only
    nightglass-spatial fetch-coastline      ONLINE, provisioning only
    nightglass-spatial gfw-reference        ONLINE, provisioning only
    nightglass-spatial gfw-compare          our detector vs the published layer
    nightglass-spatial migrate              create/refresh the PostGIS schema
    nightglass-spatial scenes               catalogue granules as STAC items
    nightglass-spatial detect               run the detector, load detections
    nightglass-spatial load-ais             load the acquisition-window AIS
    nightglass-spatial dark                 §M3's query: detections with no AIS
    nightglass-spatial validate-shift       measure the azimuth-displacement sign
    nightglass-spatial render               chips, overview, map, plots
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from nightglass.config import BBox, settings


def _rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n{'-' * max(len(title), 44)}")


def _aoi_bbox(args: argparse.Namespace) -> tuple[str, BBox]:
    if getattr(args, "bbox", None):
        return "custom", BBox.parse(args.bbox, origin="--bbox")
    aoi = settings.aoi if not getattr(args, "aoi", None) else _named_aoi(args.aoi)
    return aoi.name, aoi.bbox


def _named_aoi(name: str):
    from nightglass.config import AOI

    return AOI.from_env(name)


def _coastline(args: argparse.Namespace, aoi_name: str):
    """The AOI's shoreline, or None with a visible warning.

    Deliberately a warning rather than a hard failure. The detector runs without
    it and says so in the run record — but every detection over an archipelago
    then has to be treated as unverified, so silence would be the wrong default.
    """
    if getattr(args, "no_coastline", False):
        return None
    from nightglass.spatial.coastline import Coastline, CoastlineError, clipped_path

    path = Path(args.coastline) if getattr(args, "coastline", None) else clipped_path(
        settings.coastline_dir, aoi_name
    )
    try:
        return Coastline.load(path, buffer_m=args.coast_buffer_m)
    except CoastlineError as exc:
        print(f"\033[33mWARNING: {exc}\033[0m", file=sys.stderr)
        print(
            "\033[33mProceeding with the data-derived land mask alone. It cannot "
            "separate a skerry from a hull, so coastal detections are unverified."
            "\033[0m",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------


def cmd_fetch_coastline(args: argparse.Namespace) -> int:
    """ONLINE. Provisioning only."""
    from nightglass.config import AOI, ConfigError
    from nightglass.spatial.coastline import fetch_and_clip

    names = args.aois or _configured_aoi_names()
    aois = {}
    for name in names:
        try:
            aois[name] = AOI.from_env(name).bbox
        except ConfigError as exc:
            print(f"skipping {name}: {exc}", file=sys.stderr)
    if not aois:
        print("no AOIs configured", file=sys.stderr)
        return 1
    _rule(f"GSHHG {args.resolution}/L1 -> {len(aois)} AOI(s)")
    fetch_and_clip(aois, args.out, resolution=args.resolution, force=args.force,
                   keep_archive=args.keep_archive)
    return 0


def _configured_aoi_names() -> list[str]:
    import os

    return sorted(
        k[len("AOI_") : -len("_BBOX")].lower()
        for k, v in os.environ.items()
        if k.startswith("AOI_") and k.endswith("_BBOX") and v.strip()
    )


def _roles(args: argparse.Namespace) -> tuple[str, ...]:
    from nightglass.spatial.archive import ROLES

    if args.all:
        return ROLES
    return tuple(args.role) if args.role else ("required",)


def _fetch_section(args: argparse.Namespace, section_key: str, out: str) -> list:
    """The half of `fetch-granules` and `fetch-ais` that is the same in both.

    Downloads what the requested roles select, prints what it skipped and why,
    and returns the paths. Nothing is silently omitted: a manifest entry either
    appears in the fetch list or in the skip list.
    """
    from nightglass.spatial.archive import (
        ArchiveError,
        download,
        earthdata_credentials,
        load_manifest,
    )

    section = load_manifest(args.manifest, section_key)
    wanted, skipped = section.select(_roles(args))
    if args.name:
        wanted = tuple(i for i in wanted if args.name in i.name)
    if not wanted:
        raise SystemExit(f"nothing selected. roles={_roles(args)} name={args.name!r}")

    total = sum(i.bytes for i in wanted)
    _rule(f"{section_key.upper()} — {len(wanted)} file(s), {total / 1e9:.2f} GB")
    print(f"  {section.licence}")
    if skipped:
        print(f"\n  not fetched ({len(skipped)}, {sum(i.bytes for i in skipped) / 1e9:.2f} GB):")
        for item in skipped:
            print(f"    {item.name}")
            print(f"      {item.role}: {item.note}")
        print("  add --all for every entry, or --role <role> to pick.")
    print()

    auth = None
    if section.credentials == "earthdata" and any(
        args.force or not (Path(out) / i.name).exists() for i in wanted
    ):
        auth = earthdata_credentials()
        print(f"  Earthdata as {auth[0]} — sent to {'urs.earthdata.nasa.gov'} and nowhere else\n")

    paths = []
    for item in wanted:
        try:
            path, outcome = download(
                item, out, auth=auth, force=args.force, verify_cached=args.verify
            )
        except ArchiveError as exc:
            print(f"\n\033[31m{exc}\033[0m", file=sys.stderr)
            raise SystemExit(1) from exc
        if outcome in ("cached", "verified"):
            note = "size matches the manifest" if outcome == "cached" else "sha256 checked"
            print(f"   {item.name[:46]:46s} {item.mb:8.1f} MB  {outcome} ({note})")
        paths.append(path)
    return paths


def cmd_fetch_granules(args: argparse.Namespace) -> int:
    """ONLINE. Provisioning only — the input that used to be staged by hand.

    Weights, documents, the shoreline and the GFW layer all had a fetch target
    from the milestone that introduced them. The granules did not, which made
    §M6's "clone, `make up`, reproduce" false everywhere but the machine this
    was built on. This is that step.
    """
    paths = _fetch_section(args, "sar", args.out)
    print(f"\n  {len(paths)} granule(s) in {args.out}")
    print("  next: make scenes   — catalogue them as STAC items")
    return 0


def cmd_fetch_ais(args: argparse.Namespace) -> int:
    """ONLINE. Provisioning only — the Danish day the matcher is validated against.

    Denmark is the validation AOI because it is the only place with free
    point-level *historical* AIS (§3.1), and a clone with no AIS can run the
    detector but cannot check it against anything. Downloads the daily file and
    cuts the acquisition window out of it, because the matcher reads the day
    twice and each pass is a full scan of 5.45 GB of CSV.
    """
    from nightglass.spatial.ais import acquisition_window, slice_day, slice_path
    from nightglass.spatial.safe import SafeProduct

    paths = _fetch_section(args, "ais", args.out)
    if args.no_slice:
        print(f"\n  {len(paths)} daily file(s) in {args.out}; --no-slice, nothing cut")
        return 0

    aoi_name, bbox = _aoi_bbox(args)
    granule = Path(args.granule)
    if not granule.exists():
        print(
            f"\n\033[33mNo granule at {granule} — cannot derive the acquisition window,\n"
            f"so nothing was sliced. Run `make fetch-granules` first, or pass\n"
            f"--granule/--no-slice.\033[0m",
            file=sys.stderr,
        )
        return 1
    centre = SafeProduct(granule).acquisition.acquisition_time
    start, end = acquisition_window(centre, args.window_min)

    for day in paths:
        # `aisdk-YYYY-MM-DD.zip`. Slicing a different day against this window
        # would write an empty CSV that looks like a successful provisioning
        # step, so it is refused by name rather than discovered by row count.
        if day.stem.removeprefix("aisdk-") != f"{start:%Y-%m-%d}":
            print(f"\n  {day.name}: not the acquisition day ({start:%Y-%m-%d}) — not sliced")
            continue
        out = slice_path(args.slice_out, aoi_name, start, end)
        _rule(f"slice {day.name} -> {out.name}")
        print(f"  acquisition  {centre:%Y-%m-%d %H:%M:%S} UTC  ({granule.name[:32]}…)")
        print(f"  window       ±{args.window_min:g} min  ->  {start:%H:%M:%S} – {end:%H:%M:%S}")
        print(f"  bbox         {aoi_name} {bbox}")
        if out.exists() and not args.force:
            print(f"  cached       {out} ({out.stat().st_size / 1e6:.1f} MB)")
            continue
        kept, scanned = slice_day(day, out, bbox, start, end)
        pct = 100.0 * kept / scanned if scanned else 0.0
        print(f"  {kept:,} rows kept of {scanned:,} ({pct:.2f}%)  ->  {out}")
        print("  duplicates are kept verbatim; DMAFileSource deduplicates on read.")
    return 0


def cmd_gfw_reference(args: argparse.Namespace) -> int:
    """ONLINE. Provisioning only — the fifth thing this system fetches."""
    import os

    from nightglass.spatial.gfw import fetch_reference, reference_path, write_reference

    aoi_name, bbox = _aoi_bbox(args)
    start, end = _parse_day(args.start), _parse_day(args.end)
    _rule(f"GFW published detections -> {aoi_name}  [{bbox}]")
    print(f"  window     {start:%Y-%m-%d} .. {end:%Y-%m-%d}")
    detections = fetch_reference(
        bbox,
        start,
        end,
        token=os.environ.get("GFW_TOKEN", ""),
        zoom=args.zoom,
        progress=True,
    )
    path = write_reference(
        detections,
        reference_path(args.out, aoi_name),
        aoi=aoi_name,
        bbox=bbox,
        start=start,
        end=end,
        zoom=args.zoom,
    )
    matched = sum(1 for d in detections if d.matched)
    print(f"\n  {len(detections)} detections  ({matched} matched, {len(detections) - matched} not)")
    for granule in sorted({d.granule_id for d in detections}):
        n = sum(1 for d in detections if d.granule_id == granule)
        print(f"    {granule}  {n}")
    print(f"\n  {path}")
    return 0


def cmd_gfw_compare(args: argparse.Namespace) -> int:
    """OFFLINE. Our detections against the published layer, over one granule."""
    from nightglass.spatial.gfw import compare, load_reference, reference_path
    from nightglass.tools import detect_vessels

    aoi_name, _ = _aoi_bbox(args)
    theirs, doc = load_reference(args.reference or reference_path(args.gfw_dir, aoi_name))

    granule = args.scene_id or _pick_granule(doc, theirs)
    ours = detect_vessels(granule, args.min_length_m)
    report = compare(ours, theirs, granule_id=granule, radius_m=args.radius_m)

    _rule("two independent detectors, one granule")
    print(report.render())
    print(f"\n  reference retrieved {doc['retrieved_at']}\n  {doc['licence']}")
    return 0


def _pick_granule(doc: dict, theirs: list) -> str:
    """The granule the reference has most to say about, named out loud.

    An AOI usually clips several granules from one pass, so a reference over a
    bbox covers more than one — over Lisbon, 66 detections on the granule that
    crosses the AOI and 3 on the neighbour that clips its corner. Comparing
    against the corner would report a detector that misses almost everything.
    Choosing silently would hide that; the alternatives are printed.
    """
    counts: dict[str, int] = {}
    for d in theirs:
        counts[d.granule_id] = counts.get(d.granule_id, 0) + 1
    if not counts:
        raise SystemExit("the reference is empty — re-run `make fetch-gfw`")
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1:
        others = ", ".join(f"{g} ({n})" for g, n in ranked[1:])
        print(f"  comparing over {ranked[0][0]} ({ranked[0][1]} GFW detections)")
        print(f"  also in this reference, not compared: {others}")
        print("  select another with --scene-id\n")
    return ranked[0][0]


def _parse_day(raw: str):
    from datetime import UTC, datetime

    stamp = datetime.fromisoformat(raw)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def cmd_migrate(args: argparse.Namespace) -> int:
    from nightglass.spatial.db import connect, migrate

    with connect() as conn:
        applied = migrate(conn, drop=args.drop)
    _rule("schema")
    for name in applied:
        print(f"  applied  {name}")
    return 0


def cmd_scenes(args: argparse.Namespace) -> int:
    from nightglass.spatial.db import connect, upsert_scene
    from nightglass.spatial.safe import SafeProduct, find_granules

    paths = [Path(p) for p in args.granule] if args.granule else find_granules(args.scene_dir)
    if not paths:
        print(f"no granules under {args.scene_dir}", file=sys.stderr)
        return 1
    _rule(f"cataloguing {len(paths)} granule(s)")
    with connect() as conn:
        for path in paths:
            product = SafeProduct(path)
            item = product.stac_item()
            upsert_scene(conn, item, str(path))
            a = product.acquisition
            print(
                f"  {a.scene_id}\n"
                f"      {a.acquisition_time:%Y-%m-%d %H:%M:%S} UTC  {a.pass_direction:<10} "
                f"rel.orbit {a.relative_orbit:<4} {'+'.join(a.polarizations)}  "
                f"{a.width}×{a.height}"
            )
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    from nightglass.spatial.db import connect, insert_detections, upsert_scene
    from nightglass.spatial.detect import DetectorConfig, detect_scene
    from nightglass.spatial.safe import SafeProduct

    aoi_name, bbox = _aoi_bbox(args)
    coastline = _coastline(args, aoi_name)
    cfg = DetectorConfig(
        polarization=args.pol,
        k=args.k,
        min_length_m=args.min_length_m if args.min_length_m is not None else settings.min_length_m,
    )
    product = SafeProduct(args.granule)
    _rule(f"detecting over {aoi_name}  [{bbox}]")
    detections, measurements, run = detect_scene(
        product, config=cfg, bbox=bbox, coastline=coastline, progress=True
    )
    print(run.render())
    if not args.dry_run:
        with connect() as conn:
            upsert_scene(conn, product.stac_item(), str(args.granule))
            insert_detections(conn, run, detections, measurements)
        print(f"\nloaded {len(detections)} detections into detect.detections")
    return 0


def cmd_load_ais(args: argparse.Namespace) -> int:
    from nightglass.spatial.ais import DMAFileSource, acquisition_window
    from nightglass.spatial.db import connect, insert_positions, scene_row
    from nightglass.spatial.safe import SafeProduct

    _, bbox = _aoi_bbox(args)
    if args.granule:
        product = SafeProduct(args.granule)
        centre = product.acquisition.acquisition_time
    else:
        with connect() as conn:
            row = scene_row(conn, args.scene_id)
        if row is None:
            print(f"unknown scene {args.scene_id}", file=sys.stderr)
            return 1
        centre = row["acquisition_time"]
    start, end = acquisition_window(centre, args.window_min)

    source = DMAFileSource(args.file)
    _rule(f"AIS from {Path(args.file).name}")
    print(f"  acquisition  {centre:%Y-%m-%d %H:%M:%S} UTC")
    print(f"  window       ±{args.window_min:g} min  ->  {start:%H:%M:%S} – {end:%H:%M:%S}")
    print(f"  bbox         {bbox}")
    print(f"  ground truth {source.is_ground_truth}")
    with connect() as conn:
        n, vessels = insert_positions(conn, source, bbox, start, end)
    print(f"\n  {n:,} deduplicated positions, {vessels:,} distinct MMSI")
    print(f"  attribution: {source.attribution}")
    return 0


def cmd_dark(args: argparse.Namespace) -> int:
    from nightglass.spatial.db import connect, dark_query

    with connect() as conn:
        rows, summary = dark_query(
            conn,
            scene_id=args.scene_id,
            radius_m=args.radius_m,
            window_min=args.window_min,
            correct_azimuth=not args.no_azimuth_correction,
        )
    _rule(f"§M3 — detections with no AIS correspondence  [{args.scene_id}]")
    for k, v in summary.items():
        print(f"  {k:<28} {v}")
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    dark = [r for r in rows if r["status"] == "dark"]
    print(f"\n  {'detection':<22} {'lat':>9} {'lon':>9} {'len m':>7} {'conf':>5}  nearest AIS")
    for r in (dark if args.dark_only else rows)[: args.limit]:
        near = (
            f"{r['mmsi']}  {r['distance_m']:.0f} m  Δt {r['time_delta_s']:+.0f} s"
            if r["mmsi"]
            else "— none in window"
        )
        print(
            f"  {r['detection_id'].split(':')[-1]:<22} {r['lat']:9.5f} {r['lon']:9.5f} "
            f"{r['length_m'] or 0:7.0f} {r['confidence'] or 0:5.2f}  {near}"
        )
    return 0


def cmd_validate_shift(args: argparse.Namespace) -> int:
    from nightglass.spatial.validate import validate_azimuth_shift

    aoi_name, bbox = _aoi_bbox(args)
    report = validate_azimuth_shift(
        granule=args.granule,
        ais_file=args.file,
        bbox=bbox,
        window_min=args.window_min,
        coastline=_coastline(args, aoi_name),
        out_dir=Path(args.out) if args.out else None,
        map_path=Path(args.map) if args.map else None,
    )
    _rule("azimuth displacement — measured, not argued")
    print(report.render())
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from nightglass.spatial import render as R
    from nightglass.spatial.detect import DetectorConfig, detect_scene
    from nightglass.spatial.safe import SafeProduct

    aoi_name, bbox = _aoi_bbox(args)
    product = SafeProduct(args.granule)
    cfg = DetectorConfig(polarization=args.pol, k=args.k)
    detections, measurements, run = detect_scene(
        product, config=cfg, bbox=bbox, coastline=_coastline(args, aoi_name), progress=True
    )
    print(run.render())
    out = Path(args.out)
    _rule("rendering")
    for path in (
        R.contact_sheet(
            product, detections, measurements, out / "chips_top.png",
            title=f"{aoi_name} — highest-confidence detections (VH sigma0, dB)",
        ),
        R.contact_sheet(
            product, detections, measurements, out / "chips_spread.png", order="spread",
            title=f"{aoi_name} — detections sampled across the confidence range",
        ),
        R.scene_overview(product, bbox, detections, measurements, out / "overview.png", config=cfg),
    ):
        print(f"  {path}")
    return 0


# ---------------------------------------------------------------------------


def _add_aoi_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--aoi", help="named AOI (default: NIGHTGLASS_AOI)")
    p.add_argument("--bbox", help="override: min_lon,min_lat,max_lon,max_lat")


def _add_coastline_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--coastline", help="clipped coastline GeoJSON")
    p.add_argument(
        "--no-coastline",
        action="store_true",
        help="data-derived land mask only — coastal detections will be unverified",
    )
    p.add_argument(
        "--coast-buffer-m",
        type=float,
        default=1000.0,
        help="seaward buffer on the shoreline. Measured trade-off over the "
             "Kattegat: 300 m -> 35%% dark, 1 km -> 25%%, 3 km -> 14%%, and going "
             "from 300 m to 3 km drops 21 detections of which 18 were dark and "
             "only 3 matched. 1 km is where that stops being nearly free.",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nightglass-spatial", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch-coastline", help="ONLINE. Download GSHHG and clip it per AOI.")
    f.add_argument("--out", default="/app/data/coastline")
    f.add_argument("--resolution", default="f", choices=list("clihf"))
    f.add_argument("--aois", nargs="*", help="default: every AOI_*_BBOX in the environment")
    f.add_argument("--force", action="store_true")
    f.add_argument("--keep-archive", action="store_true",
                   help="keep the 149 MB download for a re-clip; off by default so the "
                        "enclave mount holds only the clipped AOIs")
    f.set_defaults(func=cmd_fetch_coastline)

    def _add_manifest_args(p: argparse.ArgumentParser) -> None:
        from nightglass.spatial.archive import ROLES

        p.add_argument("--manifest", default="/app/data/sources.yaml")
        p.add_argument(
            "--role", action="append", choices=list(ROLES), default=None,
            help="repeatable. default: required — everything a proof or the demo needs",
        )
        p.add_argument("--all", action="store_true", help="every entry, superseded included")
        p.add_argument("--name", help="only entries whose filename contains this")
        p.add_argument("--force", action="store_true", help="re-download even if present")
        p.add_argument(
            "--verify", action="store_true",
            help="sha256 what is already on disk. Without it a cached file is checked "
                 "by size only, because hashing 2.8 GB on every run is a cost with no "
                 "reader.",
        )

    fg = sub.add_parser("fetch-granules", help="ONLINE. Download the Sentinel-1 granules.")
    fg.add_argument("--out", default="/app/data/raw/sar")
    _add_manifest_args(fg)
    fg.set_defaults(func=cmd_fetch_granules)

    fa = sub.add_parser("fetch-ais", help="ONLINE. Download the DMA day and cut the window.")
    fa.add_argument("--out", default="/app/data/raw/ais")
    fa.add_argument("--slice-out", default="/app/data/interim")
    fa.add_argument(
        "--granule",
        default="/app/data/raw/sar/"
        "S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13.zip",
        help="the acquisition whose window is cut out of the day",
    )
    fa.add_argument("--window-min", type=float, default=30.0,
                    help="±minutes around the acquisition. Wider than the matcher's ±11 "
                         "on purpose: the slice is a cache of the day, and the matcher's "
                         "own window is what decides a match.")
    fa.add_argument("--no-slice", action="store_true", help="download only")
    _add_aoi_args(fa)
    _add_manifest_args(fa)
    fa.set_defaults(func=cmd_fetch_ais)

    g = sub.add_parser(
        "gfw-reference",
        help="ONLINE. Fetch GFW's published detections for an AOI as a reference layer.",
    )
    _add_aoi_args(g)
    g.add_argument("--start", required=True, help="YYYY-MM-DD")
    g.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive upper bound)")
    g.add_argument("--out", default="/app/data/gfw")
    g.add_argument("--zoom", type=int, default=9)
    g.set_defaults(func=cmd_gfw_reference)

    gc = sub.add_parser(
        "gfw-compare",
        help="Our detections vs GFW's over the identical granule. Offline.",
    )
    _add_aoi_args(gc)
    gc.add_argument("--scene-id", help="default: the reference's only granule")
    gc.add_argument("--reference", help="path to the fetched reference JSON")
    gc.add_argument("--gfw-dir", default="/app/data/gfw")
    gc.add_argument("--radius-m", type=float, default=500.0)
    gc.add_argument("--min-length-m", type=float, default=15.0)
    gc.set_defaults(func=cmd_gfw_compare)

    m = sub.add_parser("migrate", help="Create or refresh the PostGIS schema.")
    m.add_argument("--drop", action="store_true", help="DROP the M3 schemas first")
    m.set_defaults(func=cmd_migrate)

    s = sub.add_parser("scenes", help="Catalogue granules as STAC items.")
    s.add_argument("granule", nargs="*")
    s.add_argument("--scene-dir", default="/app/data/raw/sar")
    s.set_defaults(func=cmd_scenes)

    d = sub.add_parser("detect", help="Run the detector over one granule.")
    d.add_argument("granule")
    _add_aoi_args(d)
    _add_coastline_args(d)
    d.add_argument("--pol", default="VH")
    d.add_argument("--k", type=float, default=8.0)
    d.add_argument("--min-length-m", type=float, default=None)
    d.add_argument("--dry-run", action="store_true", help="do not write to PostGIS")
    d.set_defaults(func=cmd_detect)

    a = sub.add_parser("load-ais", help="Load acquisition-window AIS into PostGIS.")
    a.add_argument("file", help="DMA daily zip or a pre-sliced CSV")
    a.add_argument("--granule", help="take the acquisition time from this granule")
    a.add_argument("--scene-id", help="...or from this catalogued scene")
    _add_aoi_args(a)
    a.add_argument("--window-min", type=float, default=11.0)
    a.set_defaults(func=cmd_load_ais)

    k = sub.add_parser("dark", help="§M3: detections with no AIS correspondence.")
    k.add_argument("scene_id")
    k.add_argument("--radius-m", type=float, default=500.0)
    k.add_argument("--window-min", type=float, default=11.0)
    k.add_argument("--no-azimuth-correction", action="store_true")
    k.add_argument("--dark-only", action="store_true")
    k.add_argument("--limit", type=int, default=40)
    k.add_argument("--json", action="store_true")
    k.set_defaults(func=cmd_dark)

    v = sub.add_parser("validate-shift", help="Measure the azimuth-displacement correction.")
    v.add_argument("granule")
    v.add_argument("file", help="DMA daily zip or pre-sliced CSV")
    _add_aoi_args(v)
    _add_coastline_args(v)
    v.add_argument("--window-min", type=float, default=11.0)
    v.add_argument("--out", default="/app/data/out")
    v.add_argument(
        "--map",
        help="also render detections and AIS over the SAR backdrop to this path. "
             "This is the only command holding pixels, detections and AIS at once.",
    )
    v.set_defaults(func=cmd_validate_shift)

    r = sub.add_parser("render", help="Chips and scene overview — the evidence.")
    r.add_argument("granule")
    _add_aoi_args(r)
    _add_coastline_args(r)
    r.add_argument("--pol", default="VH")
    r.add_argument("--k", type=float, default=8.0)
    r.add_argument("--out", default="/app/data/out")
    r.set_defaults(func=cmd_render)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except RuntimeError as exc:
        # Every failure this package raises deliberately is a RuntimeError
        # subclass -- ArchiveError, GfwError, CoastlineError, SafeError,
        # ConfigError -- and each one's message is written for an operator: the
        # credential that is missing, the EULA that has not been accepted, the
        # fetch that has to run first. A traceback printed above that message
        # buries the only part the reader needs and makes a handled condition
        # look like a crash. `nightglass-corpus` already prints its FetchError
        # clean; this is the same contract, applied at the boundary rather than
        # in ten command functions -- this CLI imports its domain modules lazily
        # (rasterio, geopandas, pyproj are not cheap) and so cannot name the
        # exception types up here without paying for them on every invocation.
        # NIGHTGLASS_TRACEBACK=1 puts the stack back when debugging.
        if os.environ.get("NIGHTGLASS_TRACEBACK"):
            raise
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
