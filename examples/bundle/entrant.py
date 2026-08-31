"""A minimal entrant: question bundle in, response bundle out. Standard library only.

  python examples/bundle/entrant.py --bundle examples/bundle/sandbox-batch.json \
      --entrant demo_bundle_entrant > response.json

**This is not a forecaster.** It answers every question with a deliberately wide
prior centred on whatever anchor it is given, and the numbers it produces carry
no information about the world. It exists to settle the format questions -- how
the three answer shapes sit in one payload, where the entrant id goes, what a
distribution has to contain -- so that the only thing left for a real entrant to
write is `answer_for`. Replace that one function and the rest of this file is
already a working submission client.

**Nothing here is invented silently.** A question this file cannot honestly
answer raises instead of filing something plausible-looking. A free-choice
ranking round, for instance, asks for ten article titles out of all of
Wikipedia; a default answer to that would be ten titles nobody chose, scored as
though somebody had. `--anchor` is how you supply what the model actually
believes; without it, only questions with a self-evident structure (a fixed
ranking basket, a numeric round with a stated centre) can be answered at all.

**Both accepted distribution formats round-trip.** `--quantiles` emits the
quantile form instead of mean/sd for scalar and profile answers; the arena
scores the two with the same CRPS, so the choice is about which one you can
state honestly, not about which one scores better. Use quantiles when your
belief is skewed or fat-tailed and a normal would misstate it.
"""
import argparse
import json
import sys

# The quantile levels this example emits. Any levels are accepted as long as
# 0.5 is among them and the values do not decrease; these five are just a
# readable spread.
LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)

# z-scores for the levels above, so the quantile form and the mean/sd form
# describe the same normal. Writing one belief two ways and having them
# disagree is a bug an entrant would ship without noticing.
Z = {0.05: -1.6449, 0.25: -0.6745, 0.5: 0.0, 0.75: 0.6745, 0.95: 1.6449}

# The width of the default prior, in the question's own unit. Wide on purpose:
# an example that shipped a confident default would score badly and teach the
# wrong lesson about what an sd is for.
DEFAULT_SD = 8.0


class Unanswerable(RuntimeError):
    """This question needs an anchor and none was supplied."""


def distribution(centre, sd, as_quantiles):
    if not as_quantiles:
        return {"mean": round(centre, 4), "sd": round(sd, 4)}
    return {"quantiles": {f"{level}": round(centre + Z[level] * sd, 4)
                          for level in LEVELS}}


def answer_for(question, anchor, as_quantiles):
    """The one function a real entrant replaces.

    `anchor` is whatever `--anchor` supplied for this round_id, or None. What it
    means depends on the question's shape, which is the point of the shape being
    declared in the bundle rather than guessed from the answer.
    """
    target = question["target_type"]
    rid = question["round_id"]

    if target == "continuous_normal":
        if anchor is None:
            raise Unanswerable(
                f"{rid}: a scalar round needs a centre. Put "
                f'{{"{rid}": <number>}} in your --anchor file, in the '
                f"round's unit ({question['unit']}).")
        return {"topline": distribution(float(anchor), DEFAULT_SD, as_quantiles)}

    if target == "profile_energy":
        cells = question["cells"]
        if not isinstance(anchor, dict) or set(anchor) != set(cells):
            raise Unanswerable(
                f"{rid}: a profile round needs a centre for every one of its "
                f"{len(cells)} cells, and only those cells. Put "
                f'{{"{rid}": {{"<cell>": <number>, ...}}}} in your --anchor '
                f"file. Cells: {', '.join(cells)}.")
        return {"profile": {cell: distribution(float(anchor[cell]), DEFAULT_SD,
                                               as_quantiles)
                            for cell in cells}}

    if target == "ranking_list":
        if anchor is not None:
            order = list(anchor)
        elif question.get("items"):
            # A fixed basket is a permutation problem, so the basket as
            # published is a real, statable answer: "no reordering". A
            # free-choice round has no equivalent, which is why it falls through.
            order = list(question["items"])
        else:
            raise Unanswerable(
                f"{rid}: this ranking round is a free choice of "
                f"{question['ranking_length']} items, not a permutation of a "
                f'published basket. Put {{"{rid}": ["item", ...]}} in your '
                "--anchor file; there is no defensible default.")
        if len(order) != question["ranking_length"]:
            raise Unanswerable(
                f"{rid}: asks for exactly {question['ranking_length']} items in "
                f"order, the anchor has {len(order)}.")
        return {"ranking": order}

    raise Unanswerable(
        f"{rid}: target_type '{target}' is not one this example knows how to "
        "answer. Do not guess: a wrong-shaped answer is an answer to a "
        "different question.")


def build_response(bundle, entrant_id, anchors, as_quantiles, skip_unanswerable):
    answers = []
    skipped = []
    for question in bundle["questions"]:
        try:
            answer = answer_for(question, anchors.get(question["round_id"]),
                                as_quantiles)
        except Unanswerable as err:
            if not skip_unanswerable:
                raise
            skipped.append(str(err))
            continue
        answer["round_id"] = question["round_id"]
        answers.append(answer)
    if not answers:
        raise Unanswerable(
            "nothing in this bundle could be answered; a response with no "
            "answers is not a submission")
    for line in skipped:
        print("skipped:", line, file=sys.stderr)
    return {
        "schema_version": bundle["schema_version"],
        "batch_id": bundle["batch_id"],
        "entrant_id": entrant_id,
        "answers": answers,
        "notes": ("examples/bundle/entrant.py, wide default prior; not a "
                  "forecast. Replace answer_for() with your model."),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", required=True, help="the question bundle")
    ap.add_argument("--entrant", required=True, help="your registered entrant id")
    ap.add_argument("--anchor", help="JSON map of round_id -> centre / cells / order")
    ap.add_argument("--quantiles", action="store_true",
                    help="emit the quantile form instead of mean+sd")
    ap.add_argument("--skip-unanswerable", action="store_true",
                    help="leave rounds you have no anchor for unanswered "
                         "(they simply score nothing) instead of failing")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args(argv)

    with open(args.bundle, encoding="utf-8") as fh:
        bundle = json.load(fh)
    anchors = {}
    if args.anchor:
        with open(args.anchor, encoding="utf-8") as fh:
            anchors = json.load(fh)

    response = build_response(bundle, args.entrant, anchors, args.quantiles,
                              args.skip_unanswerable)
    text = json.dumps(response, indent=2, sort_keys=True) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
