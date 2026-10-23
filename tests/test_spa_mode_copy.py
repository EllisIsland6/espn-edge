"""The page tells the truth about its mode (ADR-012, WU-4).

Three modes, decided in `api/config.py`; the SPA names the same three and says
something different in each: the local copy, the synthetic notice, or the
consent the invited person reads before handing over an ESPN session. The
consent's sentences are pinned by literal because they carry a decision the
ADR records as the person's -- a reworded sentence is a different decision.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "api/config.py"
API_TS = ROOT / "web/src/api.ts"
COPY_TS = ROOT / "web/src/copy.ts"
MANAGE_TSX = ROOT / "web/src/pages/Manage.tsx"
LAYOUT_TSX = ROOT / "web/src/components/Layout.tsx"
E2E = ROOT / "web/e2e/smoke.spec.ts"

CONSENT = {
    "storage": (
        "Your ESPN session (SWID and espn_s2) will be stored encrypted on this server "
        "and used only to read your leagues."
    ),
    "erase": "Deleting the account here erases it.",
    "terms": "ESPN's terms do not permit this kind of access; you are choosing to do it.",
}


def _backend_modes() -> list[str]:
    source = CONFIG.read_text(encoding="utf-8")
    match = re.search(r"app_mode: Literal\[([^\]]+)\]", source)
    assert match, "api/config.py no longer declares app_mode as a Literal"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def test_the_spa_names_the_backends_three_modes_and_no_others():
    modes = _backend_modes()
    assert len(modes) == 3
    api_ts = API_TS.read_text(encoding="utf-8")
    union = re.search(r"mode: ([^;]+);", api_ts).group(1)
    assert sorted(re.findall(r'"([a-z_]+)"', union)) == sorted(modes)
    copy = COPY_TS.read_text(encoding="utf-8")
    declared = re.search(r"export const MODES: readonly Mode\[\] = \[([^\]]+)\];", copy).group(1)
    assert re.findall(r'"([a-z_]+)"', declared) == modes, "same three, same order"
    footer = copy.split("export const FOOTER_LABEL")[1].split("};")[0]
    assert all(f"{m}:" in footer for m in modes), "every mode has a footer label"


def test_the_consent_copy_is_pinned_by_literal():
    copy = COPY_TS.read_text(encoding="utf-8")
    for key, sentence in CONSENT.items():
        assert f"{key}:" in copy and sentence in copy, f"the {key} sentence changed"
    manage = MANAGE_TSX.read_text(encoding="utf-8")
    invited = manage.split('mode === "private_invited" && (')[1].split(")}")[0]
    assert "INVITED_CONSENT.storage" in invited
    assert "INVITED_CONSENT.erase" in invited
    assert "INVITED_CONSENT.terms" in invited
    # The invited branch is the ONLY one that renders the consent.
    assert manage.count("INVITED_CONSENT.") == 3


def test_the_three_branches_say_three_different_things():
    manage = MANAGE_TSX.read_text(encoding="utf-8")
    assert 'mode === "public_synthetic" && (' in manage and "SYNTHETIC_NOTICE" in manage
    assert 'mode === "private_operator" && (' in manage and "LOCAL_NOTICE" in manage
    assert 'mode === "private_invited" && (' in manage
    copy = COPY_TS.read_text(encoding="utf-8")
    notices = re.findall(r'export const (\w+_NOTICE) =\s*"([^"]+)"', copy)
    assert {name for name, _ in notices} == {"SYNTHETIC_NOTICE", "LOCAL_NOTICE"}
    texts = [text for _, text in notices] + [CONSENT["storage"]]
    assert len(set(texts)) == 3
    # The accounts empty state is mode-aware too.
    assert 'mode === "public_synthetic" ? (' in manage
    assert "No accounts on a synthetic instance" in manage


def test_the_footer_names_the_deployment_from_the_backends_word():
    layout = LAYOUT_TSX.read_text(encoding="utf-8")
    assert "getHealth()" in layout and "FOOTER_LABEL" in layout
    assert "{footerLabel(mode)}" in layout
    assert 'return (mode && FOOTER_LABEL[mode]) || "ESPN Edge";' in layout, (
        "an unknown mode claims nothing beyond the name"
    )
    assert "local portfolio analytics</span>" not in layout, "the label is no longer a literal"


def test_the_browser_suite_exercises_every_mode():
    spec = E2E.read_text(encoding="utf-8")
    for mode in _backend_modes():
        assert f'mode: "{mode}"' in spec, f"no Playwright test mocks {mode}"
    assert CONSENT["terms"] in spec, "the consent is asserted in the browser, not only pinned here"


# ------------------------------------------------------- the AI key (G7)

AI_KEY_CONSENT = {
    "storage": (
        "Your Anthropic API key will be stored encrypted on this server and used only "
        "for the reports you ask for."
    ),
    "billing": "Anthropic bills you directly for those calls.",
    "erase": "Deleting the key here erases it.",
}


def test_the_ai_key_copy_is_pinned_and_the_panel_is_the_invited_sites_alone():
    """ADR-012 G7, the operator's decision of 2026-10-23: each person pays for
    their own AI and the site keeps their key the way it keeps their ESPN
    session. The three sentences are the decision; the panel renders in the
    invited branch and nowhere else; the field is write-only."""
    copy = COPY_TS.read_text(encoding="utf-8")
    for key, sentence in AI_KEY_CONSENT.items():
        assert f"{key}:" in copy and sentence in copy, f"the AI key {key} sentence changed"
    manage = MANAGE_TSX.read_text(encoding="utf-8")
    assert 'mode === "private_invited" && <AiKeyPanel' in manage, (
        "the panel is not gated on the mode"
    )
    assert manage.count("<AiKeyPanel") == 1
    panel = manage.split("function AiKeyPanel(")[1].split("\nfunction ")[0]
    for key in AI_KEY_CONSENT:
        assert f"AI_KEY_CONSENT.{key}" in panel
    assert manage.count("AI_KEY_CONSENT.") == 3, "the consent is rendered somewhere else too"
    assert 'type="password"' in panel, "the key field must be write-only"
    assert "key.hint" in panel and "api_key" not in panel.split("putAiKey(")[0], (
        "the panel shows the hint and never the key"
    )
    api = API_TS.read_text(encoding="utf-8")
    key_out = api.split("export interface AiKeyOut {")[1].split("}")[0]
    assert "hint: string" in key_out and "api_key" not in key_out, (
        "the client type must have no field for the key"
    )
    assert 'send<AiKeyOut>("PUT", "/api/ai/key", { api_key })' in api
    assert 'send<void>("DELETE", "/api/ai/key")' in api
    e2e = E2E.read_text(encoding="utf-8")
    assert AI_KEY_CONSENT["billing"] in e2e, "the browser suite does not read the billing sentence"
    assert 'getByTestId("ai-key-panel")).toHaveCount(0)' in e2e, (
        "the browser suite must assert the panel's absence off the invited site"
    )
