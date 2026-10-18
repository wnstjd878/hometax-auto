"""
== 운영 맥락 ==
용도: 홈택스 전자세금계산서 발행을 헤드풀 크롬(CDP 9260, 프로필 ~/.chrome-hometax)에서 사람처럼 수행.
실행 시점: 수동(ssh) 또는 정산봇 뒤에서 호출. launchd 미등록(2026-09-06 시점).
입력: 발행 목록 JSON (invoices.json) — 공급받는자 사업자번호/상호/대표자/이메일/작성일자/품목/공급가액/세액/청구·영수
출력: 화면 캡처 ~/tools/hometax-issue/shots/*.png, 결과 로그 ~/tools/hometax-issue/logs/
외부 의존: 홈택스(hometax.go.kr) 화면. 로그인은 사람이 크롬에서 직접(인증서/간편인증). 보안카드 값은 채팅에 넣지 않고
          ~/.config/hometax/securitycard.json 에서 읽는다(9.1).
의도적 미구현: 자동 타이핑 로그인(비번 채팅 입력 금지), 헤드리스 모드(봇 탐지·디버깅 불리), 발급대행 API(사용자가 헤드풀 선택).
마지막 점검: 2026-09-06
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # Windows cp949 콘솔 깨짐 방지

CDP = os.environ.get("HOMETAX_CDP", "http://127.0.0.1:9260")
BASE = Path(__file__).resolve().parent
SHOTS = BASE / "shots"
SHOTS.mkdir(exist_ok=True)


def connect(pw):
    """홈택스 본 화면을 잡는다. 인증·보안카드처럼 따로 뜨는 창은 고르지 않는다."""
    browser = pw.chromium.connect_over_cdp(CDP)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    hometax = [p for p in ctx.pages if "hometax" in (p.url or "")]
    main = [p for p in hometax if "index_pp.xml" in (p.url or "") and "popup.html" not in (p.url or "")]
    page = (main or hometax or ctx.pages or [ctx.new_page()])[0]
    return browser, ctx, page


_NOTICE_JS = """() => {
  const skip = new Set(['', '닫기', '레이어팝업시작', '레이어팝업끝', '오늘 하루 보지 않기']);
  const vis = e => e && e.offsetParent !== null;
  const out = [];
  for (const w of document.querySelectorAll('.w2popup_window')) {
    if (!vis(w)) continue;
    // 입력칸이 있는 창(2차 인증 등)은 일하는 창이라 건드리지 않는다
    const inputs = [...w.querySelectorAll('input:not([type=button]):not([type=checkbox]):not([type=hidden]), select, textarea')].filter(vis);
    if (inputs.length) continue;
    const btns = [...w.querySelectorAll('input[type=button], button, a')].filter(vis);
    const label = b => (b.value || b.innerText || b.title || '').trim();
    // 닫기 말고 다른 단추(발급, 목록조회, 확인 등)가 있으면 사람이 고를 일이 있는 창이다
    if (btns.some(b => !skip.has(label(b)))) continue;
    const close = btns.find(b => label(b) === '닫기');
    if (!close) continue;
    const title = (w.innerText || '').split('\\n').map(s => s.trim())
      .filter(s => s && !s.startsWith('레이어')).slice(0, 2).join(' / ');
    // '오늘 하루 보지 않기' 가 있으면 체크하고 닫는다(같은 공지가 오늘 다시 뜨지 않게)
    for (const c of w.querySelectorAll('input[type=checkbox]')) {
      const lb = c.id ? w.querySelector('label[for="' + c.id + '"]') : null;
      if (!c.checked && lb && lb.innerText.includes('보지 않기')) lb.click();
    }
    close.click();
    out.push(title.slice(0, 80));
  }
  return out;
}"""


def dismiss_notices(page, log=None) -> list[str]:
    """홈택스 첫 화면에 뜨는 공지 팝업(시스템 점검 안내 등)을 닫는다. 이 창이 '아이디 로그인' 단추를 덮어
    로그인이 30초 기다리다 멈췄다(2026-10-08 테크랩스). 닫기 단추만 있고 입력칸이 없는 창만 닫으므로
    인증·발급처럼 할 일이 남은 창은 건드리지 않는다. 닫은 창의 제목을 돌려준다."""
    closed = []
    for _ in range(5):  # 공지가 여러 장 겹쳐 뜨기도 한다
        try:
            got = page.evaluate(_NOTICE_JS)
        except Exception:  # noqa
            break
        if not got:
            break
        closed += got
        page.wait_for_timeout(600)
    if closed and log:
        log("[공지] 홈택스 공지 팝업을 닫았다: " + " | ".join(closed))
    return closed


def click_past_notices(page, locator, log=None, tries=6):
    """공지 팝업이 늦게 떠 단추를 덮어도 누른다. 짧게 눌러 보고, 막히면 공지를 닫고 다시 누른다.
    공지가 아닌 창이 덮고 있으면 마지막 시도의 오류를 그대로 올린다."""
    for n in range(tries):
        dismiss_notices(page, log)
        try:
            locator.click(timeout=4000)
            return
        except Exception:  # noqa
            if n == tries - 1:
                raise
            page.wait_for_timeout(1000)


def accept_quiet(dialog):
    """안내창(alert)을 확인한다. 로그인·입력 단계가 각자 받는 곳을 달아 두어 한쪽이 먼저 누르면
    다른 쪽은 "이미 처리됨" 오류가 난다(2026-09-18). 그 오류는 무시한다."""
    try:
        dialog.accept()
    except Exception:  # noqa
        pass


def main_eval(ctx, page, expr):
    """화면 자체 칸(main world)에서 실행 — 격리 칸 함정 회피."""
    cdp = ctx.new_cdp_session(page)
    r = cdp.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return r.get("result", {}).get("value")


def wake_window(page) -> bool:
    """크롬 창이 최소화돼 있으면 되살린다. 되살렸으면 True.

    최소화된 창은 화면을 그리지 않아 캡처가 30초씩 멈춘다(2026-09-29 실측: 사람이 홈택스 창을 내려 두자
    진단용 캡처가 연달아 실패했고, 그 오류가 발행 흐름까지 끊었다). 입력·클릭은 최소화 상태에서도 된다.
    """
    try:
        cdp = page.context.new_cdp_session(page)
        win = cdp.send("Browser.getWindowForTarget")
        if (win.get("bounds") or {}).get("windowState") != "minimized":
            return False
        cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
        page.wait_for_timeout(1000)
        print("최소화된 홈택스 창을 되살렸다")
        return True
    except Exception as ex:  # noqa
        print("창 상태 확인 실패:", str(ex)[:120])
        return False


def shot(page, name):
    """진단용 화면 캡처. 못 찍어도 오류를 올리지 않고 None 을 돌려준다(캡처 때문에 발행이 끊기면 안 된다)."""
    p = SHOTS / f"{name}.png"
    wake_window(page)
    try:
        page.screenshot(path=str(p), full_page=False, timeout=12000)
        print("shot", p)
        return p
    except Exception as ex:  # noqa
        print("shot 실패", name, str(ex).split("Call log")[0].strip()[:120])
        return None


def cmd_shot(a):
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        if a.url:
            page.goto(a.url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
        print("url", page.url, "title", page.title())
        shot(page, a.name)


def cmd_dump(a):
    """현재 화면(프레임 포함)의 클릭 가능한 요소·입력칸 목록을 글자로 뽑는다."""
    js = """
    (() => {
      const out = [];
      const walk = (doc, prefix) => {
        const els = doc.querySelectorAll('a,button,input,select,textarea,[role=button],[onclick]');
        els.forEach(e => {
          const r = e.getBoundingClientRect();
          if (r.width === 0 && r.height === 0) return;
          const t = (e.innerText || e.value || e.placeholder || e.title || '').trim().replace(/\\s+/g,' ').slice(0,60);
          out.push(`${prefix}${e.tagName.toLowerCase()}#${e.id||''} name=${e.name||''} type=${e.type||''} "${t}" @${Math.round(r.x)},${Math.round(r.y)}`);
        });
      };
      walk(document, '');
      return out.join('\\n');
    })()
    """
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        print("url", page.url)
        print(main_eval(ctx, page, js))
        for i, fr in enumerate(page.frames):
            if fr == page.main_frame:
                continue
            try:
                txt = fr.evaluate(js)
                if txt:
                    print(f"--- frame[{i}] {fr.url}")
                    print(txt)
            except Exception as ex:  # noqa
                print(f"--- frame[{i}] {fr.url} (읽기 실패: {ex})")


def cmd_text(a):
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        print("url", page.url)
        txt = main_eval(ctx, page, "document.body.innerText")
        print((txt or "")[: a.limit])


def cmd_goto(a):
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        page.goto(a.url, wait_until="domcontentloaded")
        page.wait_for_timeout(a.wait)
        print("url", page.url, "title", page.title())
        shot(page, a.name)


def cmd_click(a):
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        loc = page.get_by_text(a.text, exact=a.exact)
        n = loc.count()
        print("matches", n)
        if n == 0:
            sys.exit(2)
        loc.nth(a.nth).hover()
        page.wait_for_timeout(300)
        loc.nth(a.nth).click()
        page.wait_for_timeout(a.wait)
        print("url", page.url, "title", page.title())
        shot(page, a.name)


def build():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("shot"); s.add_argument("--url"); s.add_argument("--name", default="shot"); s.set_defaults(fn=cmd_shot)
    s = sp.add_parser("dump"); s.set_defaults(fn=cmd_dump)
    s = sp.add_parser("text"); s.add_argument("--limit", type=int, default=4000); s.set_defaults(fn=cmd_text)
    s = sp.add_parser("goto"); s.add_argument("url"); s.add_argument("--name", default="goto"); s.add_argument("--wait", type=int, default=4000); s.set_defaults(fn=cmd_goto)
    s = sp.add_parser("click"); s.add_argument("text"); s.add_argument("--exact", action="store_true"); s.add_argument("--nth", type=int, default=0); s.add_argument("--wait", type=int, default=3000); s.add_argument("--name", default="click"); s.set_defaults(fn=cmd_click)
    return ap


if __name__ == "__main__":
    a = build().parse_args()
    a.fn(a)
