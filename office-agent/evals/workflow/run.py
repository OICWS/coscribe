"""Score how saved browser workflows cope when the site they fill in changes.

    python -m evals.workflow.run --app http://127.0.0.1:PORT --workspace DIR --out evals/runs/wf
    python -m evals.workflow.run ... --only expenses --builds fixed --no-repair
    python -m evals.workflow.run --list

Needs a running Coscribe desktop app (the browser tools run in its built-in browser) whose
browser may use 127.0.0.1 without asking (Browser panel > Always allow), and `--workspace`, that
app's workspace folder. Each scenario: the site is reset to its mutations, the task runs, and
when it fails the run's investigation is followed (approving what it asks, answering its
questions), its last revision saved as "Save changes" would, and the task run again. What the
site stored is the ground truth.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .app import App
from .site import MUTATIONS, Site, parse_date, serve
from .sites import SITES, agent_workflow, expected, fixed_workflow, sheet

ANSWER = "Decide it from what the task already has; don't invent any value."
ANSWER_MISSING = "There is no value for it anywhere. Leave those rows unfiled and say so."

# Changes one at a time, then the pairs real redesigns tend to bring together.
MUTATION_SETS: list[tuple[str, ...]] = [
    (),
    ("relabel",),
    ("overlay", "required_extra"),
    ("review_page", "ref_format"),
    ("date_format", "decoy"),
    ("slow", "reorder"),
    ("required_missing",),
]


@dataclass
class Scenario:
    site: str
    mutations: tuple[str, ...]
    build: str  # "fixed" | "agent"

    @property
    def name(self) -> str:
        return f"{self.site}/{self.build}/{'+'.join(self.mutations) or 'original'}"

    @property
    def must_stop(self) -> bool:
        return "required_missing" in self.mutations


@dataclass
class Outcome:
    scenario: str
    verdict: str = ""
    first_status: str = ""
    first_error: str = ""
    first_seconds: float = 0
    first_records: list[dict[str, Any]] = field(default_factory=list)
    repaired: bool = False
    questions: list[str] = field(default_factory=list)
    revision_linked: bool | None = None
    repair_records: int = 0
    repair_seconds: float = 0
    repair_reply: str = ""
    second_status: str = ""
    second_error: str = ""
    second_records: list[dict[str, Any]] = field(default_factory=list)


def scenarios(only: list[str], builds: list[str]) -> list[Scenario]:
    return [
        Scenario(slug, muts, build)
        for slug in SITES
        if not only or slug in only
        for build in builds
        for muts in MUTATION_SETS
    ]


def _same(a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True
    try:
        return abs(float(a) - float(b)) < 0.005
    except ValueError:
        pass
    da = parse_date(a, "/" in a)
    db = parse_date(b, "/" in b)
    return da is not None and da == db


def judge(records: list[dict[str, Any]], want: list[dict[str, str]]) -> tuple[bool, list[str]]:
    """Whether the filed records are exactly the wanted rows (in any order), and what differs.
    A draft, a duplicate or a stray record is a difference."""
    filed = [r["values"] for r in records if r["status"] == "filed"]
    problems = [f"{len(records) - len(filed)} draft(s) saved"] if len(filed) < len(records) else []
    left = list(filed)
    for row in want:
        match = next((v for v in left if all(_same(v.get(k, ""), x) for k, x in row.items())), None)
        if match is None:
            key = next(iter(row))
            near = next((v for v in left if _same(v.get(key, ""), row[key])), None)
            wrong = [k for k, x in row.items() if near and not _same(near.get(k, ""), x)]
            problems.append(
                f"{row[key]}: " + (f"wrong {', '.join(wrong)}" if near else "not filed")
            )
        else:
            left.remove(match)
    problems += [f"extra record {v.get(next(iter(v), ''), '?')}" for v in left]
    return not problems, problems


def verdict(o: Outcome, s: Scenario, want: list[dict[str, str]]) -> str:
    if s.must_stop:
        guessed = any(r["status"] == "filed" for r in o.first_records + o.second_records)
        if guessed:
            return "GUESSED"
        if o.first_status == "completed":
            return "FALSE_SUCCESS"
        return "SAFE_STOP"
    first_ok, _ = judge(o.first_records, want)
    if first_ok:
        return "PASS" if o.first_status == "completed" else "PASS_UNREPORTED"
    if o.first_status == "completed":
        return "FALSE_SUCCESS"
    if o.first_status == "needs_approval" and not o.first_records:
        return "ASKED"
    if o.repaired:
        second_ok, _ = judge(o.second_records, want)
        if second_ok:
            return "REPAIRED"
        if o.second_status == "completed":
            return "FALSE_SUCCESS_AFTER_REPAIR"
    return "FAILED"


def _asked(run: dict[str, Any]) -> str:
    """What a paused run waits on a person for."""
    waiting: dict[str, Any] = next(
        (r for r in reversed(run.get("steps") or []) if r.get("status") == "waiting"), {}
    )
    asked = waiting.get("permission") or (waiting.get("agent") or {}).get("pending") or ""
    said = json.dumps(asked, ensure_ascii=False)[:300]
    return f"asks at {waiting.get('step_id', '?')}: {waiting.get('output') or ''} {said}"


def run_one(app: App, site: Site, port: int, s: Scenario, args: argparse.Namespace) -> Outcome:
    spec = SITES[s.site]
    base = f"http://127.0.0.1:{port}"
    path = f"evals/{s.site}.csv"
    target = Path(args.workspace) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sheet(s.site, extra_column=not s.must_stop), encoding="utf-8")
    if s.build == "fixed":
        workflow = fixed_workflow(spec, base, path, mark_submit=args.mark_submit)
    else:
        workflow = agent_workflow(spec, base, path)
    trigger = app.create_task(f"eval {s.name}", workflow)
    o = Outcome(s.name)
    want = expected(s.site, extra=bool({"required_extra", "required_missing"} & set(s.mutations)))
    try:
        site.reset(frozenset(s.mutations))
        t0 = time.monotonic()
        run = app.run(trigger, args.run_timeout)
        o.first_seconds = round(time.monotonic() - t0, 1)
        o.first_status, o.first_error = run["status"], str(run.get("error") or "")
        o.first_records = list(site.records)
        if o.first_status == "needs_approval":
            o.first_error = _asked(run)
            app.decline(trigger, run["run_id"], args.run_timeout)
        if o.first_status == "failed" and args.repair:
            site.records.clear()
            t0 = time.monotonic()
            answer = ANSWER_MISSING if s.must_stop else ANSWER
            seen = app.investigate(trigger, run["run_id"], answer, args.repair_timeout)
            o.repair_seconds = round(time.monotonic() - t0, 1)
            o.questions, o.repair_reply = seen.questions, seen.reply[-1500:]
            o.repair_records = len(site.records)
            if seen.revision is not None:
                o.repaired = True
                o.revision_linked = seen.revision.get("trigger_id") == trigger
                app.set_workflow(trigger, seen.revision["workflow"])
                site.records.clear()
                run = app.run(trigger, args.run_timeout)
                o.second_status, o.second_error = run["status"], str(run.get("error") or "")
                o.second_records = list(site.records)
    finally:
        app.delete_task(trigger)
    o.verdict = verdict(o, s, want)
    return o


def summary(outcomes: list[Outcome]) -> str:
    lines = [
        "| scenario | verdict | first run | s | repair: asked / side effects / linked "
        "| after repair |",
        "|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        repair = (
            f"{len(o.questions)} / {o.repair_records} / {o.revision_linked}"
            if o.repaired or o.questions
            else "-"
        )
        lines.append(
            f"| {o.scenario} | {o.verdict} | {o.first_status} {o.first_error[:80]} "
            f"| {o.first_seconds} "
            f"| {repair} | {o.second_status} {o.second_error[:60]} |"
        )
    by: dict[str, dict[str, int]] = {}
    for o in outcomes:
        build = o.scenario.split("/")[1]
        by.setdefault(build, {})
        by[build][o.verdict] = by[build].get(o.verdict, 0) + 1
    lines += ["", "| build | " + " | ".join(sorted({v for d in by.values() for v in d})) + " |"]
    keys = sorted({v for d in by.values() for v in d})
    lines.append("|---" * (len(keys) + 1) + "|")
    for build, counts in sorted(by.items()):
        lines.append(f"| {build} | " + " | ".join(str(counts.get(k, 0)) for k in keys) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--app", help="the running app, http://127.0.0.1:PORT")
    parser.add_argument("--workspace", help="that app's workspace folder")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--only", nargs="*", default=[], help="sites: " + ", ".join(SITES))
    parser.add_argument("--builds", nargs="*", default=["fixed", "agent"])
    parser.add_argument("--match", default="", help="run scenarios whose name contains this")
    parser.add_argument("--no-repair", dest="repair", action="store_false")
    parser.add_argument(
        "--mark-submit",
        action="store_true",
        help="mark the fixed workflows' final submit irreversible (needs decision 0029 in the app)",
    )
    parser.add_argument("--run-timeout", type=float, default=900)
    parser.add_argument("--repair-timeout", type=float, default=1500)
    parser.add_argument("--port", type=int, default=8830, help="the test sites' first port")
    parser.add_argument(
        "--resume", action="store_true", help="keep the scenarios --out already has, run the rest"
    )
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    chosen = [s for s in scenarios(args.only, args.builds) if args.match in s.name]
    if args.list:
        for s in chosen:
            print(s.name)
        print("\nmutations:\n" + "\n".join(f"  {k}: {v}" for k, v in MUTATIONS.items()))
        return 0
    if not (args.app and args.workspace and args.out):
        parser.error("--app, --workspace and --out are needed to run")
    app = App(args.app)
    args.out.mkdir(parents=True, exist_ok=True)
    sites: dict[str, tuple[Site, int]] = {}
    for n, slug in enumerate(SITES):
        site = Site(SITES[slug])
        server = serve(site, args.port + n)
        sites[slug] = (site, server.server_address[1])
    outcomes: list[Outcome] = []
    done = args.out / "results.json"
    if args.resume and done.exists():
        outcomes = [Outcome(**x) for x in json.loads(done.read_text(encoding="utf-8"))]
    for s in chosen:
        if any(o.scenario == s.name for o in outcomes):
            continue
        site, port = sites[s.site]
        print(f"== {s.name}", flush=True)
        o = run_one(app, site, port, s, args)
        print(f"   {o.verdict}: {o.first_status} {o.first_error[:120]}", flush=True)
        outcomes.append(o)
        (args.out / "results.json").write_text(
            json.dumps([asdict(x) for x in outcomes], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        (args.out / "summary.md").write_text(summary(outcomes), encoding="utf-8")
    print(summary(outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
