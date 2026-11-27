"""Deploy the page so that nobody's open page breaks.

    .venv/bin/python ops/deploy_spa.py             # build, upload, invalidate, prune
    .venv/bin/python ops/deploy_spa.py --dry-run   # build, then say what would be done

The page is a folder of files: a shell (`index.html`) that names its scripts, and scripts
named by a hash of their contents. Three things about deploying one were learned on the
hosted site, and this script is where they are held, instead of in a block pasted from a
runbook:

1. **Nothing is removed that a page already loaded may still ask for.** A tab opened before
   the deploy runs the old script, and the old script fetches its on-demand screens by their
   old names. `aws s3 sync --delete` removed those names the moment the new build went up; on
   2026-11-20 the operator's own browser met `Failed to fetch dynamically imported module` on
   the first such screen it opened afterwards. Superseded files are now kept for
   `--retain-days` (30) and pruned by AGE after that -- never because the new build lacks them.

2. **The shell says how long it may be reused, and the answer is: ask every time.** With no
   `Cache-Control`, a browser may reuse a response for a tenth of its age; for a shell built
   three weeks earlier that is more than two days of the old page running against the new
   site. `index.html` goes up `no-cache` (kept, but revalidated on every use -- one ETag
   comparison). The hashed files go up `immutable` for a year: a name that changes with the
   content never needs asking about.

3. **Nothing is uploaded or removed unless the build that produced it succeeded**, and the
   shell goes up last, after every file it names. `vite build` empties its folder before it
   writes; a build that fails after that must not be handed to anything that makes the
   bucket match it (CLOSE-OUT lesson 60), and a shell must never be live before its scripts.

The page's own half of this is `web/src/lib/chunk.ts`: a tab that still meets a missing file
reloads once. This script makes that the rare case instead of the usual one.

It holds no credential and prints none. It shells out to the `aws`, `npm`, `git` and `curl`
on the PATH of whoever runs it -- on the hosted site, the operator in his own terminal.

"Done" is a reading, not an inference: after the invalidation completes the script fetches
the shell from the live site and checks that it names this build's files and arrives with
the headers it was stored with. Only then does it prune (CLOSE-OUT lesson 61: the read that
proves an act belongs to the act).

Exit status: 0 the live site serves this build; 2 refused, nothing on the site changed;
1 something changed and something after it failed (the line says what).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SHELL = "index.html"
ASSET_PREFIX = "assets/"

#: Kept by the browser, but never used without asking the server first.
SHELL_CACHE = "no-cache"
#: A hashed name is its content; a year, and no revalidation even on reload.
ASSET_CACHE = "public, max-age=31536000, immutable"
#: Anything else in the build (an icon, a manifest): an hour, then ask.
OTHER_CACHE = "public, max-age=3600"

DEFAULT_DOMAIN = "eaedgefinder.com"
DEFAULT_RETAIN_DAYS = 30

#: After the invalidation completes the edge should already serve the new shell; a few
#: more looks cost half a minute and spare a false alarm.
EDGE_ATTEMPTS = 6
EDGE_PAUSE_SECONDS = 5.0

_ASSET_REF = re.compile(r"""(?:src|href)=["']/?(assets/[A-Za-z0-9._-]+)["']""")
_DOMAIN = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


class Refused(RuntimeError):
    """A reason to stop while the site is exactly as it was."""


class Incomplete(RuntimeError):
    """Something on the site changed and a later step failed."""


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str = ""


#: run(cmd, cwd=None, capture=True) -> Completed. With capture=False the command's own
#: output goes to the terminal, which is where a build's and an upload's belongs.
Runner = Callable[..., Completed]


def _run(cmd: Sequence[str], cwd: Path | None = None, capture: bool = True) -> Completed:
    # A fixed argument list, never a shell string.
    proc = subprocess.run(
        list(cmd), cwd=cwd, check=False, text=True, stdout=subprocess.PIPE if capture else None
    )
    return Completed(proc.returncode, proc.stdout or "")


#: fetch(url, method) -> (status, headers with lower-cased names, body). The live site, read
#: the way a visitor's browser reads it. Status 0 means no answer at all.
Fetcher = Callable[[str, str], tuple[int, dict[str, str], str]]


def parse_http(text: str) -> tuple[int, dict[str, str], str]:
    """`curl -D -` output: a status line, headers, a blank line, the body."""
    head, _, body = text.replace("\r\n", "\n").partition("\n\n")
    lines = head.split("\n")
    status = re.match(r"HTTP/\S+ (\d{3})", lines[0])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if colon:
            headers[name.strip().lower()] = value.strip()
    return (int(status.group(1)) if status else 0), headers, body


def edge_reader(run: Runner) -> Fetcher:
    """The live site through `curl` -- the machine's own trust store, and the same tool the
    runbook's reads use, so the script and the operator are looking through one instrument."""

    def fetch(url: str, method: str = "GET") -> tuple[int, dict[str, str], str]:
        if not url.startswith("https://"):
            raise ValueError("the live site is read over https")
        how = ["-I"] if method == "HEAD" else ["-D", "-"]
        done = run(["curl", "-sS", "--max-time", "20", *how, url])
        if done.returncode != 0:
            return 0, {}, ""
        return parse_http(done.stdout)

    return fetch


@dataclass(frozen=True)
class Build:
    dist: Path
    shell_refs: tuple[str, ...]
    assets: tuple[str, ...]
    others: tuple[str, ...]


@dataclass(frozen=True)
class Upload:
    key: str
    cache_control: str


@dataclass(frozen=True)
class Stored:
    key: str
    last_modified: datetime


def read_build(dist: Path) -> Build:
    """What the build produced, refused unless it is a whole page: a shell, at least one
    script named by it, and every file the shell names present beside it."""
    shell = dist / SHELL
    if not shell.is_file():
        raise Refused(f"the build has no {SHELL}")
    refs = tuple(dict.fromkeys(_ASSET_REF.findall(shell.read_text(encoding="utf-8"))))
    if not any(ref.endswith(".js") for ref in refs):
        raise Refused(f"{SHELL} names no script under {ASSET_PREFIX}")
    files = sorted(p.relative_to(dist).as_posix() for p in dist.rglob("*") if p.is_file())
    assets = tuple(f for f in files if f.startswith(ASSET_PREFIX))
    missing = [ref for ref in refs if ref not in assets]
    if missing:
        raise Refused(f"{SHELL} names files the build does not contain: {', '.join(missing)}")
    others = tuple(f for f in files if f != SHELL and not f.startswith(ASSET_PREFIX))
    return Build(dist, refs, assets, others)


def plan_uploads(build: Build) -> tuple[Upload, ...]:
    """Every file the shell may name, then everything else, then the shell -- last, so
    that no shell is ever live before the files it names."""
    return (
        *(Upload(key, ASSET_CACHE) for key in build.assets),
        *(Upload(key, OTHER_CACHE) for key in build.others),
        Upload(SHELL, SHELL_CACHE),
    )


def select_prunable(
    stored: Sequence[Stored], keep: Sequence[str], now: datetime, retain_days: int
) -> tuple[str, ...]:
    """Superseded files old enough that no open page can still want them.

    Three conditions, all required: under `assets/`; not a file of the build just
    uploaded; last written before the retention window. Absence from the new build is
    never, on its own, a reason -- that was the defect.
    """
    if retain_days < 1:
        raise Refused("--retain-days must be at least 1")
    cutoff = now - timedelta(days=retain_days)
    kept = set(keep)
    return tuple(
        sorted(
            s.key
            for s in stored
            if s.key.startswith(ASSET_PREFIX) and s.key not in kept and s.last_modified < cutoff
        )
    )


def read_edge(fetch: Fetcher, domain: str, build: Build) -> str | None:
    """What is wrong with the live site as a visitor would get it, or None: the shell names
    this build's files and says `no-cache`; each file it names answers and says `immutable`."""
    status, headers, body = fetch(f"https://{domain}/{SHELL}", "GET")
    if status != 200:
        return f"{SHELL} answered {status or 'nothing'} at the edge"
    if any(ref not in body for ref in build.shell_refs):
        return "the edge still serves another build's shell"
    if headers.get("cache-control") != SHELL_CACHE:
        return f"{SHELL} arrives with Cache-Control: {headers.get('cache-control')}"
    for ref in build.shell_refs:
        status, headers, _ = fetch(f"https://{domain}/{ref}", "HEAD")
        if status != 200:
            return f"{ref} answered {status or 'nothing'} at the edge"
        if headers.get("cache-control") != ASSET_CACHE:
            return f"{ref} arrives with Cache-Control: {headers.get('cache-control')}"
    return None


def _read(run: Runner, cmd: Sequence[str], what: str, cwd: Path | None = None) -> str:
    done = run(cmd, cwd=cwd)
    if done.returncode != 0:
        raise Refused(f"could not read {what}")
    return done.stdout.strip()


def list_stored(run: Runner, bucket: str) -> list[Stored]:
    """The files under `assets/` in the bucket, with when each was last written. A file
    whose date cannot be read is left out, which means it is never pruned."""
    done = run(
        [
            "aws", "s3api", "list-objects-v2",
            "--bucket", bucket, "--prefix", ASSET_PREFIX, "--output", "json",
        ]
    )
    if done.returncode != 0:
        raise Incomplete("could not list the bucket")
    try:
        contents = (json.loads(done.stdout or "{}") or {}).get("Contents") or []
    except ValueError as exc:
        raise Incomplete("the bucket listing was not JSON") from exc
    stored: list[Stored] = []
    for item in contents:
        try:
            when = datetime.fromisoformat(str(item["LastModified"]))
            key = str(item["Key"])
        except (KeyError, TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        stored.append(Stored(key, when))
    return stored


def _describe_prune(
    stored: Sequence[Stored], build: Build, now: datetime, retain_days: int
) -> tuple[tuple[str, ...], list[str]]:
    prunable = select_prunable(stored, build.assets, now, retain_days)
    lines = []
    for item in sorted(stored, key=lambda s: s.key):
        if item.key in build.assets or item.key in prunable:
            continue
        age = (now - item.last_modified).days
        lines.append(
            f"kept    {item.key}  (superseded, {age} days old; pruned after {retain_days})"
        )
    return prunable, lines


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="deploy_spa.py",
        description="Build the page and put it on the site without breaking pages already open.",
    )
    parser.add_argument(
        "--domain", default=DEFAULT_DOMAIN, help="the site's name (default: %(default)s)"
    )
    parser.add_argument(
        "--retain-days",
        type=int,
        default=DEFAULT_RETAIN_DAYS,
        help="how long a superseded file is kept before it is pruned (default: %(default)s)",
    )
    parser.add_argument("--no-build", action="store_true", help="upload web/dist as it is")
    parser.add_argument("--no-prune", action="store_true", help="remove nothing")
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would be done; change nothing"
    )
    parser.add_argument(
        "--allow-dirty", action="store_true", help="deploy with uncommitted changes"
    )
    parser.add_argument(
        "--no-edge-read",
        action="store_true",
        help="stop at the invalidation; do not read the live site (and say so)",
    )
    return parser.parse_args(list(argv))


def deploy(
    argv: Sequence[str],
    *,
    run: Runner = _run,
    fetch: Fetcher | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    say: Callable[[str], None] = print,
    root: Path = ROOT,
) -> None:
    args = parse_args(argv)
    if fetch is None:
        fetch = edge_reader(run)
    if args.retain_days < 1:
        raise Refused("--retain-days must be at least 1")
    if not _DOMAIN.fullmatch(args.domain):
        raise Refused("the domain is not a plain host name")
    web = root / "web"
    dist = web / "dist"

    # What goes on the site is a commit.
    head = _read(run, ["git", "rev-parse", "--short", "HEAD"], "the commit", cwd=root)
    changes = run(["git", "status", "--porcelain"], cwd=root)
    if changes.returncode != 0:
        raise Refused("could not read the working tree's state")
    dirty = [line.strip() for line in changes.stdout.splitlines() if line.strip()]
    if dirty and not args.allow_dirty:
        # Name what is in the way: tracked edits and untracked files both count, and a
        # refusal that does not say which sends the operator looking (CLOSE-OUT lesson 52).
        more = f"; and {len(dirty) - 5} more" if len(dirty) > 5 else ""
        raise Refused(
            "the working tree is not clean -- what goes on the site is a commit: "
            f"{'; '.join(dirty[:5])}{more}"
        )

    # Where it goes: read, never assumed.
    account = _read(
        run,
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        "the AWS account",
    )
    if not re.fullmatch(r"\d{12}", account):
        raise Refused("the AWS account id did not read as twelve digits")
    bucket = f"espn-edge-spa-{account}"
    query = (
        "DistributionList.Items"
        f"[?contains(to_string(Aliases.Items), '{args.domain}')].Id | [0]"
    )
    distribution = _read(
        run,
        ["aws", "cloudfront", "list-distributions", "--query", query, "--output", "text"],
        "the CloudFront distribution",
    )
    if not re.fullmatch(r"E[A-Z0-9]{6,}", distribution):
        raise Refused(f"no CloudFront distribution carries {args.domain}")
    say(f"deploying {head} to s3://{bucket} behind {distribution}")

    if not args.no_build:
        if run(["npm", "run", "build"], cwd=web, capture=False).returncode != 0:
            raise Refused("the build failed -- nothing was uploaded and nothing was removed")
    build = read_build(dist)
    uploads = plan_uploads(build)
    say(
        f"build   {SHELL} names {', '.join(build.shell_refs)}; "
        f"{len(build.assets)} files under {ASSET_PREFIX}"
    )

    if args.dry_run:
        for item in uploads:
            say(f"would upload  {item.key}  [{item.cache_control}]")
        try:
            stored = list_stored(run, bucket)
        except Incomplete as exc:
            say(f"prune plan unknown -- {exc}")
        else:
            prunable, kept = _describe_prune(stored, build, now(), args.retain_days)
            for line in kept:
                say(line)
            for key in prunable:
                say(f"kept    {key}  (--no-prune)" if args.no_prune else f"would prune   {key}")
        say("dry run -- nothing on the site changed")
        return

    # Files first, the shell last. A failure before the shell leaves the site serving the
    # previous build, whole.
    for item in uploads:
        say(f"upload  {item.key}  [{item.cache_control}]")
        sent = run(
            [
                "aws", "s3", "cp", str(dist / item.key), f"s3://{bucket}/{item.key}",
                "--cache-control", item.cache_control,
            ],
            capture=False,
        )
        if sent.returncode != 0:
            if item.key == SHELL:
                raise Incomplete(
                    f"the {SHELL} upload failed -- the site still serves the previous build"
                )
            raise Incomplete(
                f"the upload failed at {item.key} -- {SHELL} was not replaced, "
                "so the site still serves the previous build"
            )

    # The headers are the point; read them back from where the site reads them.
    for key, expected in ((SHELL, SHELL_CACHE), *((ref, ASSET_CACHE) for ref in build.shell_refs)):
        seen = run(
            [
                "aws", "s3api", "head-object", "--bucket", bucket, "--key", key,
                "--query", "CacheControl", "--output", "text",
            ]
        )
        if seen.returncode != 0 or seen.stdout.strip() != expected:
            raise Incomplete(f"uploaded, but {key} does not read back Cache-Control: {expected}")
    say(f"stored  {SHELL} Cache-Control: {SHELL_CACHE}; its files Cache-Control: {ASSET_CACHE}")

    cleared = run(
        [
            "aws", "cloudfront", "create-invalidation", "--distribution-id", distribution,
            "--paths", "/*", "--query", "Invalidation.Id", "--output", "text",
        ]
    )
    invalidation = cleared.stdout.strip()
    if cleared.returncode != 0 or not re.fullmatch(r"[A-Z0-9]{6,}", invalidation):
        raise Incomplete(
            "uploaded, but the CloudFront invalidation failed -- run this again, "
            "or create the invalidation by hand"
        )
    say(f"invalidation  {invalidation}")

    # "Done" is read from the live site, the way a visitor gets it.
    if args.no_edge_read:
        say("edge    not read (--no-edge-read)")
    else:
        waited = run(
            [
                "aws", "cloudfront", "wait", "invalidation-completed",
                "--distribution-id", distribution, "--id", invalidation,
            ],
            capture=False,
        )
        if waited.returncode != 0:
            raise Incomplete(
                "uploaded, but the invalidation did not complete in time -- "
                "nothing was pruned; read the site by hand"
            )
        problem: str | None = "the live site was not read"
        for attempt in range(EDGE_ATTEMPTS):
            if attempt:
                sleep(EDGE_PAUSE_SECONDS)
            problem = read_edge(fetch, args.domain, build)
            if problem is None:
                break
        if problem is not None:
            raise Incomplete(f"uploaded and invalidated, but {problem} -- nothing was pruned")
        say(
            f"edge    https://{args.domain}/ serves this build; "
            f"{SHELL} no-cache, its files immutable"
        )

    # Housekeeping comes last, and only after everything above succeeded.
    if args.no_prune:
        say("pruned  nothing (--no-prune)")
    else:
        try:
            stored = list_stored(run, bucket)
        except Incomplete as exc:
            raise Incomplete(f"the site is deployed; nothing was pruned -- {exc}") from exc
        prunable, kept = _describe_prune(stored, build, now(), args.retain_days)
        for line in kept:
            say(line)
        for key in prunable:
            say(f"prune   {key}")
            if run(["aws", "s3", "rm", f"s3://{bucket}/{key}"], capture=False).returncode != 0:
                raise Incomplete(f"the site is deployed, but removing {key} failed")
        if not prunable:
            say(f"pruned  nothing -- no superseded file is older than {args.retain_days} days")
    if args.no_edge_read:
        say(f"done    {head} is uploaded and invalidated; the live site was not read")
    else:
        say(f"done    the site serves {head}")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        deploy(sys.argv[1:] if argv is None else argv)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Incomplete as exc:
        print(f"incomplete: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
