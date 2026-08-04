"""Terminal rendering shared by the two places that show a model at work.

Small, and here rather than in either caller, because both had the same bug and
fixing it in one would have left the other.

**Truncation that looks like completion is worse than no output.** Both the agent
gate and the tool-chaining transcript rendered a call as
``f"{tool}({json.dumps(args)[:90]})"`` — slice the arguments, then append the
closing paren. On a real call that produced

    1. correlate({"bbox": [-10.5, 38, -8.5, 39.5], … "min_length_m": 20, "star)

which is not truncated JSON, it is *plausible* JSON: balanced, syntactically
innocent, and missing the time window entirely. A reader has no way to tell it
apart from the truth, and it was on screen in the demo recording.

The caveats were worse. They are the one part of an INTREP that exists to be
read in full — §7's entire argument is that the system says what it does not
know — and a `[:150]` slice cut

    NO AIS was available for this acquisition window, so none of the 71
    detection(s) above has been assessed against AIS. They are detections, not
    dark de|tections, and nothing here says whether any vessel was transmitting.

exactly where the sentence turns. The honesty mechanism was being cropped for
width.

So: wrap, never truncate. Nothing here shortens anything; it only decides where
the line breaks.
"""

from __future__ import annotations

import shutil
import textwrap

#: Wrap at the terminal, but no wider than this. Long measured lines are hard to
#: track back to the left margin, and the recording is 100 columns.
MAX_WIDTH = 100


def width(cap: int = MAX_WIDTH) -> int:
    """Usable columns. Falls back to `cap` when there is no terminal — which is
    the normal case here, since everything runs under `docker compose exec -T`."""
    return min(shutil.get_terminal_size(fallback=(cap, 24)).columns, cap)


def wrap(text: str, *, first: str = "", cont: str | None = None, cap: int = MAX_WIDTH) -> str:
    """`text` wrapped to the terminal, with a hanging indent. Never shortened.

    `break_on_hyphens` is off and `break_long_words` is off because the things
    most likely to be long here are identifiers — `det-20260717-0523-…-0`, a
    granule name — and an id broken across a line stops being greppable, which
    is most of what an id is for. Such a token overflows the margin instead,
    which is visible and harmless.
    """
    return textwrap.fill(
        text,
        width=width(cap),
        initial_indent=first,
        subsequent_indent=cont if cont is not None else " " * len(first),
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=True,
    )


def call(index: int, tool: str, arguments: str, *, note: str = "", indent: str = "  ") -> str:
    """One numbered `tool(arguments)` line, wrapped rather than sliced."""
    first = f"{indent}{index}. "
    return wrap(f"{tool}({arguments}){note}", first=first, cont=" " * len(first))
