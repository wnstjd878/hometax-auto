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


def main_eval(ctx, page, expr):
    """화면 자체 칸(main world)에서 실행 — 격리 칸 함정 회피."""
    cdp = ctx.new_cdp_session(page)
    r = cdp.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return r.get("result", {}).get("value")


def shot(page, name):
    p = SHOTS / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print("shot", p)
    return p


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
