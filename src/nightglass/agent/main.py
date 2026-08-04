"""`nightglass-agent` — §M5's graph from a terminal.

    nightglass-agent ask "Were there any …?"            run to the gate, and stop
    nightglass-agent pending                            what is halted, awaiting review
    nightglass-agent show <thread>                      the draft, as the reviewer sees it
    nightglass-agent approve <thread>                   resume and release
    nightglass-agent reject  <thread> --note "…"        resume and withhold

The split into separate commands is the milestone, not a convenience. `ask`
exits at the human gate and the process ends; `approve` is a *different*
invocation that picks the run up from Postgres and finishes it. If the gate were
a bare `input()` those would have to be one command, one process, one thread —
and "the graph halts with inspectable persisted state" would be a description of
a blocked read rather than of a checkpoint.

`pending` and `show` exist because a gate nobody can inspect is a rubber stamp.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from nightglass.agent.graph import GATE, build, new_thread_id
from nightglass.config import settings
from nightglass.display import call, wrap

BOLD = "\033[1m"
DIM = "\033[2m"
YELL = "\033[33m"
GREEN = "\033[32m"
RESET = "\033[0m"


def _rule(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}\n{'-' * max(len(title), 44)}")


def _saver():
    """A Postgres checkpointer, set up on first use.

    `setup()` creates the checkpoint tables and is idempotent, so it runs every
    time rather than being a migration somebody has to remember. It writes into
    the same database the scenes and detections live in — the halted graph is a
    row beside the evidence it was reasoning about.
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    return PostgresSaver.from_conn_string(settings.postgres_dsn)


def cmd_ask(args: argparse.Namespace) -> int:
    question = " ".join(args.question).strip()
    if not question:
        print("nothing to ask", file=sys.stderr)
        return 2

    thread = args.thread or new_thread_id()
    config = {"configurable": {"thread_id": thread}}

    with _saver() as saver:
        saver.setup()
        graph = build(saver)
        _rule(f"NIGHTGLASS agent — {settings.aoi.name}")
        print(f"{DIM}thread {thread}{RESET}")
        print(f"question  {question}\n")

        result = graph.invoke({"question": question, "errors": []}, config=config)
        state = graph.get_state(config)

    _print_progress(state.values)

    if not state.next:
        # The graph ran to completion without stopping. That should not happen:
        # every path goes through the gate, so reaching END in one invocation
        # would mean the interrupt did not fire.
        print(f"{YELL}The graph completed without halting — the gate did not fire.{RESET}")
        print(result.get("answer", ""))
        return 1

    _print_gate(result, thread)
    return 0


def _print_progress(values: dict[str, Any]) -> None:
    if values.get("plan"):
        _rule("plan")
        for line in values["plan"]:
            print(f"  {line}")
    steps = values.get("steps") or []
    if steps:
        _rule("tools the model chose")
        for i, s in enumerate(steps, 1):
            note = (
                "  ERROR" if s.get("error") else ("  REPEAT — told to advance" if s.get("repeated") else "")
            )
            print(call(i, s["tool"], json.dumps(s["arguments"], ensure_ascii=False), note=note))
    c = values.get("correlation")
    if c:
        dark = [m for m in c["matches"] if m["status"] == "dark"]
        _rule("correlation — deterministic, not the model's arithmetic")
        print(
            f"  {len(c['scenes'])} scene(s), {len(c['detections'])} detections, "
            f"{len(c['matches']) - len(dark)} matched, {len(dark)} unmatched"
        )


def _print_gate(result: dict[str, Any], thread: str) -> None:
    payload = _interrupt_payload(result)
    _rule(f"⏸  {GATE} — the graph has stopped")
    if payload:
        print(f"  marking             {payload.get('marking')}")
        print(f"  claims              {payload.get('claims')}")
        print(f"  unsupported claims  {payload.get('unsupported_claims')}")
        # Wrapped, not sliced. A caveat is the part of a report that exists to
        # be read in full; cropping one for width is cropping the argument.
        for c in payload.get("caveats", []):
            print(wrap(c, first="    - ", cont="      "))
        for e in payload.get("errors", []):
            print(f"{YELL}{wrap(e, first='  ! ', cont='    ')}{RESET}")
    print(
        f"\n{DIM}State is persisted in Postgres. This process can exit; the run cannot\n"
        f"advance without a human. Inspect it with:{RESET}\n"
        f"    nightglass-agent show {thread}\n"
        f"    nightglass-agent approve {thread}\n"
        f"    nightglass-agent reject {thread} --note \"why\""
    )


def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """The value the gate handed out, across langgraph's return shapes."""
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else None


def cmd_pending(_args: argparse.Namespace) -> int:
    """Every run halted at the gate. The queue an analyst actually works."""
    with _saver() as saver:
        saver.setup()
        graph = build(saver)
        # Listing threads is the checkpointer's job, not the graph's: the graph
        # only ever knows about the thread it was configured with. `list(None)`
        # walks every checkpoint, so collapse to the distinct threads first and
        # ask the graph for each one's current position.
        threads: list[str] = []
        for tup in saver.list(None):
            thread = tup.config["configurable"]["thread_id"]
            if thread not in threads:
                threads.append(thread)

        rows = []
        for thread in threads:
            snapshot = graph.get_state({"configurable": {"thread_id": thread}})
            if GATE not in (snapshot.next or ()):
                continue
            v = snapshot.values
            q = v.get("question", "")
            rows.append((thread, q if len(q) <= 60 else q[:59] + "…", _marking_of(v)))

    _rule(f"awaiting human review  ({len(rows)})")
    if not rows:
        print("  nothing halted")
        return 0
    for thread, question, marking in rows:
        print(f"  {thread}  {marking}\n      {question}")
    return 0


def _marking_of(values: dict[str, Any]) -> str:
    intrep = values.get("intrep")
    if not intrep:
        return "NO REPORT"
    return f"{intrep.get('classification', 'UNCLASSIFIED')} // DRAFT"


def cmd_show(args: argparse.Namespace) -> int:
    """The draft in full, so approving is a judgement rather than a reflex."""
    config = {"configurable": {"thread_id": args.thread}}
    with _saver() as saver:
        saver.setup()
        state = build(saver).get_state(config)

    if not state.values:
        print(f"no such thread: {args.thread}", file=sys.stderr)
        return 1

    intrep = state.values.get("intrep")
    _rule(f"draft — {args.thread}")
    print(f"  halted at   {', '.join(state.next) or '(not halted)'}")
    print(f"  question    {state.values.get('question', '')}")
    if not intrep:
        print(f"\n  {YELL}no report was drafted{RESET}")
        for e in state.values.get("errors", []):
            print(f"    - {e}")
        return 0

    print(f"\n  {intrep['title']}")
    print(f"  {intrep['classification']} // DRAFT — NOT RELEASABLE\n")
    for claim in intrep["claims"]:
        ids = (claim.get("scene_ids") or []) + (claim.get("chunk_ids") or [])
        n = len(claim.get("detection_ids") or [])
        tail = f"{'; '.join(i.split(':')[-1] for i in ids)}" + (f"; {n} detections" if n else "")
        print(f"  • {claim['text']}")
        print(f"      {DIM}[{tail or 'no refs'}]{RESET}")
    print("\n  caveats")
    for c in intrep["caveats"]:
        print(f"    - {c}")
    return 0


def _resume(thread: str, decision: dict[str, Any]) -> int:
    from langgraph.types import Command

    config = {"configurable": {"thread_id": thread}}
    with _saver() as saver:
        saver.setup()
        graph = build(saver)
        before = graph.get_state(config)
        if not before.values:
            print(f"no such thread: {thread}", file=sys.stderr)
            return 1
        if GATE not in (before.next or ()):
            print(
                f"thread {thread} is not waiting at the gate "
                f"(next: {', '.join(before.next) or 'nothing — already finished'})",
                file=sys.stderr,
            )
            return 1
        result = graph.invoke(Command(resume=decision), config=config)

    verdict = f"{GREEN}RELEASED{RESET}" if decision["approved"] else f"{YELL}WITHHELD{RESET}"
    _rule(f"resumed from persisted state — {verdict}")
    print(f"{DIM}A different process from the one that drafted it.{RESET}\n")
    print(result.get("answer", ""))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _resume(args.thread, {"approved": True, "note": args.note})


def cmd_reject(args: argparse.Namespace) -> int:
    return _resume(args.thread, {"approved": False, "note": args.note})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nightglass-agent", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="run to the human gate and stop")
    a.add_argument("question", nargs="+")
    a.add_argument("--thread", help="reuse a thread id instead of generating one")
    a.set_defaults(func=cmd_ask)

    p = sub.add_parser("pending", help="runs halted at the gate")
    p.set_defaults(func=cmd_pending)

    s = sub.add_parser("show", help="the draft awaiting review")
    s.add_argument("thread")
    s.set_defaults(func=cmd_show)

    ok = sub.add_parser("approve", help="resume and release")
    ok.add_argument("thread")
    ok.add_argument("--note", default=None)
    ok.set_defaults(func=cmd_approve)

    no = sub.add_parser("reject", help="resume and withhold")
    no.add_argument("thread")
    no.add_argument("--note", default=None)
    no.set_defaults(func=cmd_reject)

    args = ap.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
