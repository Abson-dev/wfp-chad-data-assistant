"""The browser client in real Chrome, under the production Content-Security-Policy, with scripted model replies.
Skipped when Playwright or Chrome is not available."""
import json
import re
import socket
import tempfile
import threading
import time
from pathlib import Path

import app as webapp
import pytest
import uvicorn
from scripted_model import ScriptedClient
from webutil import PASSWORD, add

from wfp_agent.agent import WfpAgent

sync_api = pytest.importorskip("playwright.sync_api")

CASES = {
    "merged": ([[("trace_variable", {"variable": "HHHSex"})], [("publish_result", {"result_id": "R1", "title": "How HHHSex was merged"})]],
               "**Merged cleanly.** See `HHHSex`.\n\n- First point\n- Second point\n\n| Check | Result |\n| --- | --- |\n| Mismatches | 0 |"),
    "trend": ([[("get_indicator", {"indicator": "lcs_em"})],
               [("publish_result", {"result_id": "R1", "chart": "line", "x": "round", "y": "as_merged", "series": "series",
                                    "note": "Hollow: flagged for a known defect."})]],
              "Line."),
    "rank": ([[("rank_regions", {"indicator": "fcs_pb", "round_id": "R10"})],
              [("publish_result", {"result_id": "R1", "chart": "bar", "x": "region", "y": "value"})]], "Bars."),
    "relate": ([[("relate_indicators", {"indicator_x": "fcs_pb", "indicator_y": "rcsi_mean", "round_id": "R10"})],
                [("publish_result", {"result_id": "R1", "chart": "scatter", "x": "fcs_pb", "y": "rcsi_mean"})]], "Scatter."),
    "map": ([[("get_indicator", {"indicator": "fcs_pb", "breakdown": "region"})],
             [("publish_result", {"result_id": "R1", "chart": "heatmap", "x": "round", "y": "group", "series": "as_merged"})]], "Heatmap."),
    "invented": ([[("list_rounds", {})]], "17,255 households in all, and 987.6 elsewhere."),
    "attack": ([], "<img src=x onerror=\"window.INJECTED=1\"> <script>window.INJECTED=1</script> **plain**"),
}


class Router(WfpAgent):
    def __init__(self):
        super().__init__(client=ScriptedClient([]))

    def ask(self, question, on_event=None):
        key = next(k for k in CASES if k in question)
        self.client.script, self.client.answer = CASES[key]
        return super().ask(question, on_event)


def serve(must_change=False):
    """A server with its own accounts in a temporary folder, and one account (ana@example.org)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    state = Path(tempfile.mkdtemp(prefix="wfp_browser_"))
    acc = add(webapp.open_accounts(state), must_change=must_change)
    server = uvicorn.Server(uvicorn.Config(webapp.create_app(acc, Router, state), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server, f"http://127.0.0.1:{port}"
        time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # no Chrome on this machine
            pytest.skip(f"Chrome not available: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    server, url = serve()
    context = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
    p = context.new_page()
    p.problems, p.outside = [], []
    p.on("pageerror", lambda e: p.problems.append(str(e)))
    p.on("console", lambda m: p.problems.append(m.text) if m.type == "error" else None)
    p.on("request", lambda r: p.outside.append(r.url) if not r.url.startswith((url, "data:", "blob:")) else None)
    p.goto(url)
    sign_in(p)
    yield p
    context.close()
    server.should_exit = True


def sign_in(p, password=PASSWORD):
    p.wait_for_selector("#lock:not([hidden])")
    p.fill("#email", "ana@example.org")
    p.fill("#password", password)
    p.click("#lock button[type=submit]")


def ask(p, question):
    turns = p.locator(".turn .a").count()
    p.fill("#question", question)
    p.press("#question", "Enter")
    # Not wait_for_function: it polls with eval, which the app's Content-Security-Policy rightly blocks.
    p.locator(".turn .a").nth(turns).wait_for(timeout=15000)
    return p.locator(".turn").last


def test_home_page_shows_examples_and_the_synthetic_label(page):
    page.wait_for_selector("#app:not([hidden])")
    assert page.locator(".example").count() == 4
    assert "Ana Test" in page.locator("#who").text_content()
    assert page.locator("#welcome").is_visible()
    if "SYNTHETIC" in (page.locator("#banner").text_content() or ""):
        assert "households" in page.locator("#kicker").text_content()


@pytest.mark.parametrize(("question", "rows"), [("trend", 10), ("rank", None), ("relate", None), ("map", None)])
def test_every_chart_type_draws(page, question, rows):
    turn = ask(page, question)
    turn.locator(".chart svg").first.wait_for(timeout=10000)
    if rows:
        assert turn.locator("table.data tbody tr").count() == rows
    assert not page.problems and not page.outside


def test_answer_text_is_rendered_safely(page):
    turn = ask(page, "attack")
    assert turn.locator(".answer-text img, .answer-text script").count() == 0
    assert "<img" in turn.locator(".answer-text").text_content()
    assert turn.locator(".answer-text strong").text_content() == "plain"
    assert page.evaluate("window.INJECTED") is None


def test_table_filter_sort_and_csv(page):
    turn = ask(page, "merged")
    assert turn.locator(".answer-text table.md").count() == 1 and turn.locator(".answer-text code").text_content() == "HHHSex"
    total = turn.locator("table.data tbody tr").count()
    turn.locator(".table-bar input").fill("2018 Baseline")
    assert turn.locator("table.data tbody tr").count() == 1 < total
    with page.expect_download() as d:
        turn.locator(".table-bar .small").click()
    assert d.value.suggested_filename.endswith(".csv")
    text = Path(d.value.path()).read_text(encoding="utf-8-sig")
    assert text.count("\n") == 1 and "2018 Baseline" in text


def test_result_links_back_to_the_dashboard(page):
    turn = ask(page, "merged")
    link = turn.locator(".dashboard-link a")
    assert link.count() == 1 and "#page=crosswalk" in link.get_attribute("href") and link.get_attribute("rel") == "noopener"


def test_sources_list_the_tool_calls(page):
    turn = ask(page, "merged")
    turn.locator(".sources summary").click()
    assert "trace_variable" in turn.locator(".sources").text_content()
    assert page.locator("#tool-count").text_content() == "2"


def test_the_check_of_the_figures_and_the_trace(page):
    turn = ask(page, "invented")
    warn = turn.locator(".grounding.warn")
    assert warn.count() == 1 and "987.6" in warn.text_content() and "17,255" not in warn.text_content()
    with page.expect_download() as d:
        turn.locator("button.trace").click()
    trace = json.loads(Path(d.value.path()).read_text(encoding="utf-8-sig"))
    assert trace["grounding"]["unverified"] == ["987.6"] and trace["tool_calls"][0]["tool"] == "list_rounds"
    assert trace["tool_outputs"][0].startswith("result_id:") and trace["provenance"]["model"]
    assert not page.problems and not page.outside


def test_the_welcome_page_warns_and_opens_the_example(page):
    assert "not forecasts" in page.locator(".caution").text_content()
    page.locator(".welcome-links [data-open=example]").click()
    turn = page.locator(".turn").last
    turn.locator(".chart svg").first.wait_for(timeout=10000)
    assert turn.locator(".example-tag").count() == 1 and turn.locator(".grounding.ok").count() == 1
    assert page.locator(".history li").count() == 1 and page.locator("#tool-count").text_content() == "2"
    assert not page.problems and not page.outside


def test_how_it_works_and_rounds_pages(page):
    page.locator(".side-nav [data-open=how]").click()
    page.locator("#info-body h2").wait_for()
    assert page.locator("#info-body h2").text_content() == "How it works" and page.locator("#welcome").is_hidden()
    assert page.locator("#info-body [data-fill=merged_file]").text_content().endswith(".dta")
    page.locator(".side-nav [data-open=rounds]").click()
    page.locator("#info-body table").wait_for()
    assert page.locator("#info-body tbody tr").count() == 10 and "profile_indicator" in page.locator("#info-body").text_content()
    page.click("#info-back")
    assert page.locator("#info").is_hidden() and page.locator("#welcome").is_visible()
    assert not page.problems and not page.outside


def test_feedback_on_an_answer(page):
    turn = ask(page, "merged")
    turn.locator(".feedback .rate").nth(1).click()
    turn.locator(".feedback textarea").fill("The flag is missing.")
    turn.locator(".feedback .small").click()
    page.locator(".feedback .muted").wait_for()
    assert "saved" in turn.locator(".feedback").text_content()
    assert re.match(r"Questions today: [1-9]\d* of \d+", page.locator("#usage").text_content())


def test_history_survives_a_reload(page):
    ask(page, "merged")
    assert page.locator(".history li").count() == 1
    page.reload()
    page.wait_for_selector(".history li")
    page.locator(".history button").first.click()
    assert page.locator(".turn").count() == 1


def test_new_chat_starts_empty(page):
    ask(page, "merged")
    page.click("#new-chat")
    assert page.locator(".turn").count() == 0 and page.locator("#welcome").is_visible()


def test_no_horizontal_scroll_on_a_phone(page):
    page.set_viewport_size({"width": 390, "height": 800})
    ask(page, "trend")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_sign_in_a_temporary_password_and_sign_out(browser):
    server, url = serve(must_change=True)
    context = browser.new_context()
    p = context.new_page()
    p.goto(url)
    sign_in(p, password="wrong-password")
    p.locator("#lock-error:not(:empty)").wait_for()
    assert not p.locator("#app").is_visible()
    sign_in(p)
    p.wait_for_selector("#change:not([hidden])")
    assert p.locator("#change-cancel").is_hidden() and not p.locator("#app").is_visible()
    p.fill("#pw-current", PASSWORD)
    p.fill("#pw-new", "A-much-better-pass-7")
    p.fill("#pw-again", "A-different-pass-8")
    p.click("#change button[type=submit]")
    assert "differ" in p.locator("#change-error").text_content()
    p.fill("#pw-again", "A-much-better-pass-7")
    p.click("#change button[type=submit]")
    p.wait_for_selector("#app:not([hidden])")
    p.click("#sign-out")
    p.wait_for_selector("#lock:not([hidden])")
    assert p.locator(".turn").count() == 0
    context.close()
    server.should_exit = True


def test_chats_come_back_after_signing_in_again(page):
    ask(page, "merged")
    page.click("#sign-out")
    sign_in(page)
    page.wait_for_selector(".history li")
    page.locator(".history li button").first.click()
    page.locator(".turn").first.wait_for()
    assert page.locator(".turn").count() == 1 and not page.problems
