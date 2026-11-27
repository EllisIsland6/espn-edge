"""ops/deploy_spa.py -- the page is deployed without breaking pages already open (WU-12).

On 2026-11-20 a deploy removed the previous build's files as it uploaded the new ones, and
uploaded the shell with no `Cache-Control`. Two and a half hours later the operator's browser,
still running the previous shell, asked for the Analytics screen by its old name and was
answered with CloudFront's 403; the page showed the router's "Unexpected Application Error!".

These tests hold the properties that make that impossible again -- nothing a loaded page may
ask for is removed, the shell is revalidated on every use, nothing changes unless the build
succeeded, the shell goes up last -- against a stand-in for `git`, `npm` and the AWS CLI that
records every command and keeps a bucket in memory. The page's own half (reload once, then
say so) is pinned at the end and exercised in the browser suite.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "infra/terraform/README.md"
APP_TSX = ROOT / "web/src/App.tsx"
CHUNK_TS = ROOT / "web/src/lib/chunk.ts"
ROUTE_ERROR_TSX = ROOT / "web/src/components/RouteError.tsx"
COPY_TS = ROOT / "web/src/copy.ts"
E2E = ROOT / "web/e2e/smoke.spec.ts"


def _load():
    spec = importlib.util.spec_from_file_location("deploy_spa", ROOT / "ops" / "deploy_spa.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ds = _load()

NOW = datetime(2026, 11, 27, 18, 0, 0, tzinfo=UTC)
IMMUTABLE = "public, max-age=31536000, immutable"


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


def make_dist(root: Path, *, tag: str = "new1", extra: bool = False) -> Path:
    """A whole page as `vite build` leaves it: a shell naming a script and a stylesheet,
    and one on-demand screen the shell does not name."""
    dist = root / "web" / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head>"
        f'<script type="module" crossorigin src="/assets/index-{tag}.js"></script>'
        '<link rel="stylesheet" crossorigin href="/assets/index-css1.css">'
        '</head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (dist / "assets" / f"index-{tag}.js").write_text("// shell script\n", encoding="utf-8")
    (dist / "assets" / f"Analytics-{tag}.js").write_text("// on-demand screen\n", encoding="utf-8")
    (dist / "assets" / "index-css1.css").write_text("/* styles */\n", encoding="utf-8")
    if extra:
        (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


class Site:
    """Stands in for git, npm and the AWS CLI. Remembers every command and what the bucket
    holds: key -> {"cache": the Cache-Control it was stored with, "modified": when}."""

    def __init__(
        self,
        root: Path,
        *,
        stored: dict | None = None,
        dirty: str = "",
        account: str = "123456789012",
        distribution: str = "E2ABCDEFGHIJKL",
        build=None,
        fail: tuple[str, ...] = (),
    ) -> None:
        self.root = root
        self.calls: list[list[str]] = []
        self.bucket: dict[str, dict] = dict(stored or {})
        self.dirty = dirty
        self.account = account
        self.distribution = distribution
        self.build = build
        self.fail = set(fail)
        self.waited = False
        self.fetched: list[tuple[str, str]] = []
        self.slept: list[float] = []

    def fetch(self, url: str, method: str = "GET"):
        """The edge: what a visitor is given. It serves the bucket -- unless the test says it
        is still holding the copy it had before the deploy."""
        assert url.startswith("https://eaedgefinder.com/"), url
        assert self.waited, "the live site is read only after the invalidation has completed"
        self.fetched.append((method, url))
        key = url.removeprefix("https://eaedgefinder.com/")
        if "edge-stale" in self.fail and key == "index.html":
            return 200, {"last-modified": "old"}, '<script src="/assets/index-prev.js"></script>'
        if "edge-down" in self.fail:
            return 0, {}, ""
        held = self.bucket.get(key)
        if held is None:
            return 403, {}, ""
        headers = {"cache-control": held["cache"]} if held["cache"] else {}
        return 200, headers, held.get("body", "") if method == "GET" else ""

    def __call__(self, cmd, cwd=None, capture=True):
        cmd = [str(part) for part in cmd]
        self.calls.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            return ds.Completed(0, "abc1234\n")
        if cmd[:2] == ["git", "status"]:
            return ds.Completed(0, self.dirty)
        if cmd[:3] == ["npm", "run", "build"]:
            assert cwd == self.root / "web"
            return ds.Completed(self.build() if self.build else 0)
        if cmd[:3] == ["aws", "sts", "get-caller-identity"]:
            return ds.Completed(0, self.account + "\n")
        if cmd[:3] == ["aws", "cloudfront", "list-distributions"]:
            assert "eaedgefinder.com" in cmd[cmd.index("--query") + 1]
            return ds.Completed(0, self.distribution + "\n")
        if cmd[:3] == ["aws", "s3", "cp"]:
            key = cmd[4].split("/", 3)[3]
            if f"cp:{key}" in self.fail:
                return ds.Completed(1)
            assert Path(cmd[3]).is_file(), cmd[3]
            assert cmd[4] == f"s3://espn-edge-spa-{self.account}/{key}"
            self.bucket[key] = {
                "cache": cmd[cmd.index("--cache-control") + 1],
                "modified": NOW,
                "body": Path(cmd[3]).read_text(encoding="utf-8"),
            }
            return ds.Completed(0)
        if cmd[:3] == ["aws", "s3api", "head-object"]:
            key = cmd[cmd.index("--key") + 1]
            if "head" in self.fail:
                return ds.Completed(0, "None\n")
            return ds.Completed(0, f"{self.bucket[key]['cache']}\n")
        if cmd[:3] == ["aws", "cloudfront", "create-invalidation"]:
            if "invalidate" in self.fail:
                return ds.Completed(1)
            assert cmd[cmd.index("--distribution-id") + 1] == self.distribution
            return ds.Completed(0, "I2TESTINVALIDATION\n")
        if cmd[:4] == ["aws", "cloudfront", "wait", "invalidation-completed"]:
            assert cmd[cmd.index("--id") + 1] == "I2TESTINVALIDATION"
            self.waited = True
            return ds.Completed(1 if "wait" in self.fail else 0)
        if cmd[:3] == ["aws", "s3api", "list-objects-v2"]:
            if "list" in self.fail:
                return ds.Completed(1)
            prefix = cmd[cmd.index("--prefix") + 1]
            contents = [
                {"Key": key, "LastModified": held["modified"].isoformat()}
                for key, held in sorted(self.bucket.items())
                if key.startswith(prefix)
            ]
            return ds.Completed(0, json.dumps({"Contents": contents}))
        if cmd[:3] == ["aws", "s3", "rm"]:
            key = cmd[3].split("/", 3)[3]
            if "rm" in self.fail:
                return ds.Completed(1)
            del self.bucket[key]
            return ds.Completed(0)
        raise AssertionError(f"a command the deploy has no business running: {cmd}")

    # -- what the tests ask of the record
    def uploaded(self) -> list[str]:
        return [c[4].split("/", 3)[3] for c in self.calls if c[:3] == ["aws", "s3", "cp"]]

    def removed(self) -> list[str]:
        return [c[3].split("/", 3)[3] for c in self.calls if c[:3] == ["aws", "s3", "rm"]]

    def changes(self) -> list[list[str]]:
        """Every command that alters the site: an upload, a removal, an invalidation."""
        return [
            c
            for c in self.calls
            if c[:3] in (["aws", "s3", "cp"], ["aws", "s3", "rm"])
            or c[:3] == ["aws", "cloudfront", "create-invalidation"]
        ]


def previous_deploy() -> dict:
    """The bucket as the 2026-11-20 deploy would have left it had it kept what it replaced:
    the build before last (40 days), the previous build (3 days), a stylesheet whose name
    the new build reuses, the shell, and one file outside `assets/`."""
    return {
        "index.html": {"cache": None, "modified": days_ago(3)},
        "assets/index-old1.js": {"cache": None, "modified": days_ago(40)},
        "assets/Analytics-old1.js": {"cache": None, "modified": days_ago(40)},
        "assets/index-prev.js": {"cache": None, "modified": days_ago(3)},
        "assets/Analytics-prev.js": {"cache": None, "modified": days_ago(3)},
        "assets/index-css1.css": {"cache": None, "modified": days_ago(60)},
        "favicon.svg": {"cache": None, "modified": days_ago(400)},
    }


def deploy(site: Site, *argv: str) -> list[str]:
    said: list[str] = []
    ds.deploy(
        list(argv),
        run=site,
        fetch=site.fetch,
        now=lambda: NOW,
        sleep=site.slept.append,
        say=said.append,
        root=site.root,
    )
    return said


# ---------------------------------------------------------------- the defect of 2026-11-20


def test_the_previous_builds_screen_is_still_there_after_a_deploy(tmp_path):
    """The whole of the incident in one assertion: a tab running the previous shell asks
    for `Analytics-prev.js`, and after the deploy that file must still be in the bucket."""
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy())
    deploy(site)
    assert "assets/Analytics-prev.js" in site.bucket
    assert "assets/index-prev.js" in site.bucket
    # ...and the new build is there beside it, with the shell pointing at the new script.
    assert "assets/Analytics-new1.js" in site.bucket
    assert site.bucket["index.html"]["modified"] == NOW


def test_a_superseded_file_is_pruned_by_age_and_only_by_age(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy())
    said = deploy(site)
    # Forty days: past the thirty-day window, and not in this build.
    assert sorted(site.removed()) == ["assets/Analytics-old1.js", "assets/index-old1.js"]
    # Three days: superseded, and kept. Sixty days but in this build: kept (and re-sent).
    assert "assets/index-css1.css" in site.bucket
    # Outside assets/: never the prune's to judge, however old.
    assert "favicon.svg" in site.bucket
    assert any(line.startswith("kept    assets/Analytics-prev.js") for line in said)
    assert any(line == "prune   assets/index-old1.js" for line in said)


@pytest.mark.parametrize(
    ("key", "age_days", "in_build", "expected"),
    [
        ("assets/old.js", 31, False, True),
        ("assets/old.js", 30, False, False),  # the window is not yet behind it
        ("assets/old.js", 29, False, False),
        ("assets/current.js", 400, True, False),  # in the build just uploaded
        ("index.html", 400, False, False),  # not under assets/
        ("other/old.js", 400, False, False),
        ("assetsX/old.js", 400, False, False),  # a prefix, not a look-alike
    ],
)
def test_pruning_needs_all_three_conditions(key, age_days, in_build, expected):
    stored = [ds.Stored(key, days_ago(age_days))]
    keep = [key] if in_build else ["assets/something-else.js"]
    assert (ds.select_prunable(stored, keep, NOW, 30) == (key,)) is expected


def test_a_retention_of_less_than_a_day_is_refused(tmp_path):
    with pytest.raises(ds.Refused):
        ds.select_prunable([], [], NOW, 0)
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy())
    with pytest.raises(ds.Refused):
        deploy(site, "--retain-days", "0")
    assert site.changes() == []


# ---------------------------------------------------------------------------- the headers


def test_the_shell_is_asked_about_every_time_and_a_hashed_file_never(tmp_path):
    make_dist(tmp_path, extra=True)
    site = Site(tmp_path)
    said = deploy(site)
    assert site.bucket["index.html"]["cache"] == "no-cache"
    for key in ("assets/index-new1.js", "assets/Analytics-new1.js", "assets/index-css1.css"):
        assert site.bucket[key]["cache"] == IMMUTABLE, key
    # A file that is neither the shell nor hashed: cached, but not for a year.
    assert site.bucket["favicon.svg"]["cache"] == "public, max-age=3600"
    # The headers are read back from the bucket before the deploy calls itself done.
    read_back = [c[c.index("--key") + 1] for c in site.calls if c[:3] == ["aws", "s3api", "head-object"]]
    assert read_back == ["index.html", "assets/index-new1.js", "assets/index-css1.css"]
    assert any("Cache-Control: no-cache" in line for line in said)
    assert said[-1] == "done    the site serves abc1234"


def test_headers_that_do_not_read_back_stop_the_deploy_before_it_removes_anything(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy(), fail=("head",))
    with pytest.raises(ds.Incomplete, match="does not read back Cache-Control: no-cache"):
        deploy(site)
    assert site.removed() == []


# ------------------------------------------------------------------- "done" is a reading


def test_done_means_the_live_site_was_read_and_serves_this_build(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy())
    said = deploy(site)
    # The shell, as a visitor gets it; then each file the shell names.
    assert site.fetched == [
        ("GET", "https://eaedgefinder.com/index.html"),
        ("HEAD", "https://eaedgefinder.com/assets/index-new1.js"),
        ("HEAD", "https://eaedgefinder.com/assets/index-css1.css"),
    ]
    assert any(line.startswith("edge    https://eaedgefinder.com/ serves this build") for line in said)
    assert said[-1] == "done    the site serves abc1234"
    assert site.slept == [], "it was right the first time; nothing to wait for"


def test_an_edge_still_serving_the_old_shell_is_not_done_and_nothing_is_pruned(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy(), fail=("edge-stale",))
    with pytest.raises(ds.Incomplete, match="the edge still serves another build's shell"):
        deploy(site)
    assert site.removed() == []
    # It looked again before giving up, pausing between looks.
    assert len(site.slept) == ds.EDGE_ATTEMPTS - 1
    assert sum(1 for method, _ in site.fetched if method == "GET") == ds.EDGE_ATTEMPTS


def test_a_site_that_does_not_answer_is_not_done(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy(), fail=("edge-down",))
    with pytest.raises(ds.Incomplete, match="index.html answered nothing at the edge"):
        deploy(site)
    assert site.removed() == []


def test_an_invalidation_that_does_not_complete_is_not_done(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy(), fail=("wait",))
    with pytest.raises(ds.Incomplete, match="did not complete in time"):
        deploy(site)
    assert site.fetched == [] and site.removed() == []


@pytest.mark.parametrize(
    ("shell_cache", "file_cache", "words"),
    [
        (None, IMMUTABLE, "index.html arrives with Cache-Control: None"),
        ("no-cache", None, "assets/index-new1.js arrives with Cache-Control: None"),
        ("no-cache", "no-cache", "assets/index-new1.js arrives with Cache-Control: no-cache"),
    ],
)
def test_the_edge_read_names_the_header_that_is_wrong(tmp_path, shell_cache, file_cache, words):
    """The 2026-11-20 site, read this way: a shell with no Cache-Control at all."""
    build = ds.read_build(make_dist(tmp_path))

    def edge(url: str, method: str = "GET"):
        if url.endswith("/index.html"):
            body = (tmp_path / "web/dist/index.html").read_text(encoding="utf-8")
            return 200, ({"cache-control": shell_cache} if shell_cache else {}), body
        return 200, ({"cache-control": file_cache} if file_cache else {}), ""

    assert ds.read_edge(edge, "eaedgefinder.com", build) == words


def test_without_the_edge_read_done_says_so_in_words(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy())
    said = deploy(site, "--no-edge-read")
    assert site.fetched == []
    assert "edge    not read (--no-edge-read)" in said
    assert said[-1] == "done    abc1234 is uploaded and invalidated; the live site was not read"


def test_the_live_site_is_read_with_curl_over_https_and_its_answer_parsed():
    asked: list[list[str]] = []
    answers = {
        "GET": "HTTP/2 200 \r\ncontent-type: text/html\r\nCache-Control: no-cache\r\n"
        "etag: \"abc\"\r\n\r\n<!doctype html>\n\n<p>two blank lines in a body survive</p>",
        "HEAD": "HTTP/2 403 \r\ncontent-type: application/xml\r\nx-cache: Error from cloudfront\r\n\r\n",
    }

    def run(cmd, cwd=None, capture=True):
        asked.append(list(cmd))
        return ds.Completed(0, answers["HEAD" if "-I" in cmd else "GET"])

    fetch = ds.edge_reader(run)
    status, headers, body = fetch("https://eaedgefinder.com/index.html", "GET")
    assert (status, headers["cache-control"], headers["etag"]) == (200, "no-cache", '"abc"')
    assert body == "<!doctype html>\n\n<p>two blank lines in a body survive</p>"
    assert fetch("https://eaedgefinder.com/assets/gone.js", "HEAD")[0] == 403
    assert asked[0] == ["curl", "-sS", "--max-time", "20", "-D", "-", "https://eaedgefinder.com/index.html"]
    assert asked[1][:5] == ["curl", "-sS", "--max-time", "20", "-I"]
    with pytest.raises(ValueError, match="https"):
        fetch("http://eaedgefinder.com/index.html", "GET")
    # A curl that could not reach the site is "no answer", not an exception.
    assert ds.edge_reader(lambda cmd, cwd=None, capture=True: ds.Completed(6))(
        "https://eaedgefinder.com/index.html", "GET"
    ) == (0, {}, "")


# ------------------------------------------------------------------- the order, the gates


def test_the_shell_goes_up_last_after_every_file_it_names(tmp_path):
    make_dist(tmp_path, extra=True)
    site = Site(tmp_path)
    deploy(site)
    order = site.uploaded()
    assert order[-1] == "index.html"
    assert order.count("index.html") == 1
    assert set(order[:-1]) == {
        "assets/index-new1.js",
        "assets/Analytics-new1.js",
        "assets/index-css1.css",
        "favicon.svg",
    }
    # Then, and only then: the invalidation, the wait for it, and after that the prune's listing.
    kinds = [" ".join(c[1:3]) for c in site.calls if c[0] == "aws"]
    assert kinds.index("cloudfront create-invalidation") > max(
        i for i, kind in enumerate(kinds) if kind == "s3 cp"
    )
    assert kinds.index("cloudfront wait") > kinds.index("cloudfront create-invalidation")
    assert kinds.index("s3api list-objects-v2") > kinds.index("cloudfront wait")


def test_nothing_on_the_site_changes_when_the_build_fails(tmp_path):
    """`vite build` empties its folder before it writes. A build that dies after that
    leaves an empty folder -- the input that would once have emptied the bucket."""
    dist = make_dist(tmp_path, tag="prev")

    def a_build_that_empties_its_folder_and_dies() -> int:
        for path in sorted(dist.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        return 1

    before = previous_deploy()
    site = Site(tmp_path, stored=before, build=a_build_that_empties_its_folder_and_dies)
    with pytest.raises(ds.Refused, match="the build failed"):
        deploy(site)
    assert site.changes() == []
    assert site.bucket == before


def test_a_build_that_reports_success_but_left_no_page_is_refused(tmp_path):
    dist = make_dist(tmp_path)
    (dist / "index.html").unlink()
    site = Site(tmp_path, stored=previous_deploy())
    with pytest.raises(ds.Refused, match="has no index.html"):
        deploy(site)
    assert site.changes() == []


def test_a_shell_naming_a_file_the_build_lacks_is_refused(tmp_path):
    dist = make_dist(tmp_path)
    (dist / "assets" / "index-new1.js").unlink()
    site = Site(tmp_path, stored=previous_deploy())
    with pytest.raises(ds.Refused, match="names files the build does not contain"):
        deploy(site)
    assert site.changes() == []


def test_a_shell_naming_no_script_is_refused(tmp_path):
    dist = make_dist(tmp_path)
    (dist / "index.html").write_text("<!doctype html><html><body>nothing</body></html>", encoding="utf-8")
    site = Site(tmp_path)
    with pytest.raises(ds.Refused, match="names no script"):
        deploy(site)
    assert site.changes() == []


def test_an_upload_that_fails_leaves_the_previous_shell_serving(tmp_path):
    make_dist(tmp_path)
    before = previous_deploy()
    site = Site(tmp_path, stored=before, fail=("cp:assets/index-new1.js",))
    with pytest.raises(ds.Incomplete, match="index.html was not replaced"):
        deploy(site)
    # The shell is the previous one, untouched; nothing was invalidated; nothing removed.
    assert site.bucket["index.html"] == before["index.html"]
    assert "index.html" not in site.uploaded()
    assert not any(c[:3] == ["aws", "cloudfront", "create-invalidation"] for c in site.calls)
    assert site.removed() == []


def test_nothing_is_removed_unless_everything_before_it_succeeded(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy(), fail=("invalidate",))
    with pytest.raises(ds.Incomplete, match="invalidation failed"):
        deploy(site)
    assert site.removed() == []
    assert site.fetched == [] and not site.waited
    assert not any(c[:3] == ["aws", "s3api", "list-objects-v2"] for c in site.calls)


def test_a_listing_that_fails_prunes_nothing_and_says_the_site_is_deployed(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy(), fail=("list",))
    with pytest.raises(ds.Incomplete, match="the site is deployed; nothing was pruned"):
        deploy(site)
    assert site.removed() == []
    assert site.bucket["index.html"]["cache"] == "no-cache"


def test_no_command_makes_the_bucket_match_the_folder(tmp_path):
    """The old block's verb was `sync --delete`: make there look like here. No command
    this script runs has that meaning, in any of its modes."""
    for argv in ((), ("--no-prune",), ("--dry-run",), ("--no-build",)):
        root = tmp_path / ("run" + "".join(argv).replace("-", "_"))
        make_dist(root)
        site = Site(root, stored=previous_deploy())
        deploy(site, *argv)
        for call in site.calls:
            assert "sync" not in call and "--delete" not in call and "--recursive" not in call, call
        assert all(key.startswith("assets/") for key in site.removed())


def test_no_prune_removes_nothing(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, stored=previous_deploy())
    said = deploy(site, "--no-prune")
    assert site.removed() == []
    assert "pruned  nothing (--no-prune)" in said


def test_a_dry_run_builds_reads_and_changes_nothing(tmp_path):
    make_dist(tmp_path)
    before = previous_deploy()
    site = Site(tmp_path, stored=before)
    said = deploy(site, "--dry-run")
    assert site.changes() == []
    assert site.bucket == before
    assert any(c[:3] == ["npm", "run", "build"] for c in site.calls)
    assert "would upload  index.html  [no-cache]" in said
    assert "would prune   assets/index-old1.js" in said
    assert said[-1] == "dry run -- nothing on the site changed"


def test_uncommitted_changes_are_refused_before_anything_is_built(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path, dirty=" M web/src/App.tsx\n")
    with pytest.raises(ds.Refused, match="not clean") as refusal:
        deploy(site)
    assert "M web/src/App.tsx" in str(refusal.value), "the refusal names what is in the way"
    assert not any(c[0] in ("npm", "aws") for c in site.calls)
    # Said out loud, it is allowed: the flag is the operator's sentence, not a default.
    allowed = Site(tmp_path, dirty=" M web/src/App.tsx\n")
    deploy(allowed, "--allow-dirty")
    assert allowed.bucket["index.html"]["cache"] == "no-cache"


def test_an_untracked_file_refuses_too_and_is_named(tmp_path):
    """2026-11-27: a file the desktop app had saved into the repository folder made the
    tree "not clean". Untracked counts -- but the refusal has to say which file, and a long
    list is cut short."""
    make_dist(tmp_path)
    site = Site(tmp_path, dirty='?? "Claude outputs/"\n')
    with pytest.raises(ds.Refused, match='not clean .*: \\?\\? "Claude outputs/"$'):
        deploy(site)
    assert site.changes() == []
    many = Site(tmp_path, dirty="".join(f"?? stray-{n}.txt\n" for n in range(8)))
    with pytest.raises(ds.Refused, match="stray-4.txt; and 3 more$") as refusal:
        deploy(many)
    assert "stray-5.txt" not in str(refusal.value)


def test_the_folder_the_desktop_app_delivers_into_is_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "Claude outputs/" in ignore


@pytest.mark.parametrize(
    ("account", "distribution", "words"),
    [
        ("None", "E2ABCDEFGHIJKL", "twelve digits"),
        ("", "E2ABCDEFGHIJKL", "twelve digits"),
        ("123456789012", "None", "no CloudFront distribution carries"),
        ("123456789012", "", "no CloudFront distribution carries"),
    ],
)
def test_an_unreadable_account_or_distribution_stops_before_the_build(
    tmp_path, account, distribution, words
):
    make_dist(tmp_path)
    site = Site(tmp_path, account=account, distribution=distribution)
    with pytest.raises(ds.Refused, match=words):
        deploy(site)
    assert not any(c[:3] == ["npm", "run", "build"] for c in site.calls)
    assert site.changes() == []


def test_a_domain_that_is_not_a_host_name_never_reaches_a_query(tmp_path):
    make_dist(tmp_path)
    site = Site(tmp_path)
    with pytest.raises(ds.Refused, match="plain host name"):
        deploy(site, "--domain", "x') || contains('")
    assert site.calls == []


def test_the_exit_status_says_whether_the_site_changed(monkeypatch, capsys):
    def refuses(_argv):
        raise ds.Refused("nothing changed")

    def half_done(_argv):
        raise ds.Incomplete("something changed")

    monkeypatch.setattr(ds, "deploy", refuses)
    assert ds.main([]) == 2
    monkeypatch.setattr(ds, "deploy", half_done)
    assert ds.main([]) == 1
    monkeypatch.setattr(ds, "deploy", lambda _argv: None)
    assert ds.main([]) == 0
    err = capsys.readouterr().err
    assert "refused: nothing changed" in err and "incomplete: something changed" in err


def test_a_file_whose_date_cannot_be_read_is_never_pruned(tmp_path):
    def run(cmd, cwd=None, capture=True):
        listing = {
            "Contents": [
                {"Key": "assets/undated.js", "LastModified": "not a date"},
                {"Key": "assets/no-date-at-all.js"},
                {"Key": "assets/old.js", "LastModified": days_ago(90).isoformat()},
                {"Key": "assets/naive.js", "LastModified": "2026-01-01T00:00:00"},
            ]
        }
        return ds.Completed(0, json.dumps(listing))

    stored = ds.list_stored(run, "espn-edge-spa-123456789012")
    assert [s.key for s in stored] == ["assets/old.js", "assets/naive.js"]
    assert all(s.last_modified.tzinfo is not None for s in stored)
    assert ds.select_prunable(stored, [], NOW, 30) == ("assets/naive.js", "assets/old.js")


# -------------------------------------------------------------- as a program, not a function

_STUB_AWS = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$1 $2" in
  "sts get-caller-identity") echo 123456789012 ;;
  "cloudfront list-distributions") echo E2ABCDEFGHIJKL ;;
  "s3 cp") echo "upload: $3 to $4" ;;
  "s3api head-object")
    key=""; prev=""
    for arg in "$@"; do [ "$prev" = "--key" ] && key="$arg"; prev="$arg"; done
    case "$key" in index.html) echo "no-cache" ;; *) echo "public, max-age=31536000, immutable" ;; esac ;;
  "cloudfront create-invalidation") echo I2STUBINVALIDATION ;;
  "cloudfront wait") : ;;
  "s3api list-objects-v2") echo '{"Contents": []}' ;;
  *) echo "unexpected: $*" >&2; exit 64 ;;
esac
"""
_STUB_GIT = """#!/bin/sh
case "$1" in rev-parse) echo abc1234 ;; status) : ;; *) exit 64 ;; esac
"""
# The edge, serving what was just uploaded: the shell from the build folder, with the headers
# the script stored.
_STUB_CURL = """#!/bin/sh
printf 'curl %s\\n' "$*" >> "$STUB_LOG"
for last in "$@"; do :; done
case "$last" in
  */index.html) printf 'HTTP/2 200 \\r\\ncache-control: no-cache\\r\\n\\r\\n'; cat "$STUB_SHELL" ;;
  */assets/*) printf 'HTTP/2 200 \\r\\ncache-control: public, max-age=31536000, immutable\\r\\n\\r\\n' ;;
  *) exit 6 ;;
esac
"""


def _as_a_program(
    tmp_path: Path, *, build_exit: int
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """The script itself, run by an interpreter, with `aws`, `npm` and `git` on the PATH
    replaced by recorders -- so the process plumbing is exercised and not only the logic."""
    root = tmp_path / "repo"
    (root / "ops").mkdir(parents=True)
    shutil.copy(ROOT / "ops" / "deploy_spa.py", root / "ops" / "deploy_spa.py")
    make_dist(root)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("aws", _STUB_AWS),
        ("git", _STUB_GIT),
        ("curl", _STUB_CURL),
        ("npm", f"#!/bin/sh\necho 'stub build'\nexit {build_exit}\n"),
    ):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    log = tmp_path / "aws.log"
    log.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "STUB_LOG": str(log),
        "STUB_SHELL": str(root / "web" / "dist" / "index.html"),
    }
    done = subprocess.run(
        [sys.executable, str(root / "ops" / "deploy_spa.py")],
        env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    return done, log.read_text(encoding="utf-8").splitlines()


def test_run_as_a_program_it_uploads_the_shell_last_and_exits_zero(tmp_path):
    done, aws_calls = _as_a_program(tmp_path, build_exit=0)
    assert done.returncode == 0, done.stderr
    assert "stub build" in done.stdout, "the build's own output reaches the terminal"
    assert done.stdout.rstrip().endswith("done    the site serves abc1234")
    # The live site was read -- through the stand-in, never the network -- after the wait.
    waited = aws_calls.index("cloudfront wait invalidation-completed --distribution-id E2ABCDEFGHIJKL --id I2STUBINVALIDATION")
    reads = [i for i, line in enumerate(aws_calls) if line.startswith("curl ")]
    assert len(reads) == 3 and min(reads) > waited
    copies = [line for line in aws_calls if line.startswith("s3 cp ")]
    assert len(copies) == 4
    assert copies[-1].endswith("s3://espn-edge-spa-123456789012/index.html --cache-control no-cache")
    assert all(
        line.endswith("--cache-control public, max-age=31536000, immutable") for line in copies[:-1]
    )
    assert not any("sync" in line or "--delete" in line or line.startswith("s3 rm") for line in aws_calls)


def test_run_as_a_program_a_failed_build_exits_two_and_touches_nothing(tmp_path):
    done, aws_calls = _as_a_program(tmp_path, build_exit=1)
    assert done.returncode == 2
    assert "refused: the build failed" in done.stderr
    assert not any(line.startswith(("s3 cp", "s3 rm", "cloudfront create-invalidation")) for line in aws_calls)


# ----------------------------------------------------------------------------- the runbook


def test_the_runbook_deploys_with_the_script_and_no_longer_syncs():
    """The paste block was where two defects lived in one day (CLOSE-OUT 60 and 63). The
    runbook now calls the script, behind the same guard on the commit, and carries no
    command of its own that uploads or removes a file of the page."""
    readme = README.read_text(encoding="utf-8")
    assert ".venv/bin/python ops/deploy_spa.py" in readme
    assert "s3 sync" not in readme, "the runbook still carries a sync of the page"
    assert "--delete" not in readme
    assert "cache-control: no-cache" in readme, "the runbook reads the shell's header at the edge"


# ----------------------------------------------------------------------- the page's own half


def test_every_on_demand_screen_reloads_once_and_the_router_has_its_own_error_screen():
    app = APP_TSX.read_text(encoding="utf-8")
    lazies = re.findall(r"lazy\(\(\) => (\w+)\(", app)
    assert lazies and set(lazies) == {"loadChunk"}, "every lazy() goes through loadChunk"
    assert re.search(r"lazy\(\(\) => import\(", app) is None
    # Twice: inside the layout (a page failed, the menu stands) and on the layout itself.
    assert app.count("errorElement: <RouteError />") == 2

    chunk = CHUNK_TS.read_text(encoding="utf-8")
    assert chunk.count("window.location.reload()") == 1, "one automatic reload, in one place"
    assert "if (!reloadedJustNow() && rememberReload())" in chunk, "the loop guard"
    assert "throw new ChunkLoadError(reason)" in chunk
    assert "return true;" in chunk.split("function reloadedJustNow")[1].split("function ")[0], (
        "a page that cannot remember a reload must not reload itself"
    )

    screen = ROUTE_ERROR_TSX.read_text(encoding="utf-8")
    assert "useRouteError()" in screen and 'role="alert"' in screen
    assert "ROUTE_ERROR.chunk" in screen and "ROUTE_ERROR.other" in screen
    # The sentence is for the person; the exception is for the console.
    assert ".message" not in screen and ".stack" not in screen
    assert "console.error(" in screen

    copy = COPY_TS.read_text(encoding="utf-8")
    for sentence in (
        "This screen could not be loaded.",
        "The site may have just been updated, or the connection dropped. Reload to try again.",
        "Something went wrong on this screen.",
    ):
        assert sentence in copy and sentence not in screen, sentence

    browser = E2E.read_text(encoding="utf-8")
    assert "a screen whose file is gone reloads the page once and then shows the screen" in browser
    assert "a screen that still cannot load after the reload says so in the layout" in browser
