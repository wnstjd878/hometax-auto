"""
style-guard:off  (코드 저장소 도구 — CLAUDE.md 12절 비적용 범위)

== 운영 맥락 ==
용도: 이미 발행한 세금계산서를 전액 취소한다(수정발급, 사유는 "착오에 의한 이중발급 등" 1장 자동 발급).
실행 시점: 사람이 수동. `python cancel.py <승인번호>`
입력: 취소할 승인번호. 자격은 creds.py, 크롬은 launch_chrome.ps1 로 띄운 9260.
출력: shots/cancel_*.png, logs/issue.log, 화면에 취소 승인번호.

화면 흐름 (2026-09-11 실측):
  검색창에 "수정발급" -> 결과 줄 클릭 -> 수정발급 화면.
  이 화면은 아이디 로그인만으로는 **잠겨 있다**. 인증선택 창이 따로 뜨고 보안카드로 통과하면 풀린다.
  승인번호 칸 #mf_txppWframe_edtAprvNo1, 확인은 #mf_txppWframe_grp1030(입력 단추가 아니라 A 요소).
  사유 카드 "착오에 의한 이중발급 등"의 발급하기 #mf_txppWframe_textbox948.
  그다음은 일반 발급과 같은 화면이라 금액만 음수로 자동으로 채워진다.

의도적 미구현: 다른 수정사유(기재사항 정정, 계약 해제, 환입). 금액 일부만 줄이는 경우.
              전액 취소만 다룬다. 나머지는 사람이 화면에서 한다.
마지막 점검: 2026-09-11
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

import securitycard as SC
from ht import connect, shot
from issue import log, read_layer_messages, sign_and_wait
from login import ensure_login, is_logged_in

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APRV_BOX = "#mf_txppWframe_edtAprvNo1"
APRV_OK = "#mf_txppWframe_grp1030"
REASON_DUP = "#mf_txppWframe_textbox948"


def open_amend_page(page) -> None:
    """검색창을 거쳐 수정발급 화면으로 간다. 메뉴는 코드로 눌러도 안 열릴 때가 있다."""
    page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    box = page.get_by_placeholder("검색어를 입력하세요!").first
    box.click()
    page.keyboard.type("수정발급", delay=60)
    page.wait_for_timeout(1500)
    page.keyboard.press("Enter")
    page.wait_for_timeout(7000)
    page.get_by_text("전자(세금)계산서 발급 > 일괄/공동매입분 발급 > 전자(세금)계산서 수정발급",
                     exact=False).first.click()
    page.wait_for_timeout(9000)


def unlock_if_needed(ctx, page) -> None:
    """화면이 잠겨 있으면 따로 뜬 인증 창을 보안카드로 통과한다."""
    locked = page.evaluate(
        f"(() => {{ const e = document.querySelector('{APRV_BOX}'); return e ? e.disabled : true; }})()"
    )
    if not locked:
        return
    log("[취소] 수정발급 화면이 잠겨 있어 보안카드로 인증합니다")
    if not SC.choose_card_auth(ctx):
        raise RuntimeError("인증 창을 찾지 못해 수정발급 화면을 열지 못했습니다.")
    SC.authenticate(ctx, "stepup")
    page.bring_to_front()
    page.wait_for_timeout(6000)
    still = page.evaluate(
        f"(() => {{ const e = document.querySelector('{APRV_BOX}'); return e ? e.disabled : true; }})()"
    )
    if still:
        raise RuntimeError("보안카드로 인증했지만 화면이 계속 잠겨 있습니다.")


def cancel(approval_no: str) -> str | None:
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        page.bring_to_front()
        page.on("dialog", lambda d: (log(f"[취소] 안내창: {d.message[:150]}"), d.accept()))

        if not is_logged_in(page):
            ensure_login(page)

        open_amend_page(page)
        unlock_if_needed(ctx, page)

        box = page.locator(APRV_BOX)
        box.click()
        page.keyboard.press("Control+A")
        page.keyboard.type(approval_no, delay=35)
        page.wait_for_timeout(800)
        page.locator(APRV_OK).click()
        page.wait_for_timeout(9000)

        text = page.evaluate("document.body.innerText") or ""
        if "착오에 의한 이중발급" not in text:
            shot(page, f"cancel_{approval_no[-8:]}_no_reason")
            raise RuntimeError(f"사유 선택 화면이 안 나왔습니다. 안내: {read_layer_messages(ctx, page)[:200]}")

        page.locator(REASON_DUP).click()
        page.wait_for_timeout(9000)
        shot(page, f"cancel_{approval_no[-8:]}_filled")

        amounts = page.evaluate(
            "(() => [...document.querySelectorAll('input')].filter(x => x.offsetWidth > 0 "
            "&& /Cft|Txamt/i.test(x.id) && (x.value||'').trim())"
            ".map(x => x.value).slice(0, 4).join(', '))()"
        )
        log(f"[취소] 당초 {approval_no} 취소분 금액: {amounts}")
        if "-" not in (amounts or ""):
            raise RuntimeError(f"취소분 금액이 음수가 아닙니다({amounts}). 화면을 직접 확인하세요.")

        got = sign_and_wait(ctx, page, f"cancel_{approval_no[-8:]}", use_card=True)
        return got


def main() -> None:
    if len(sys.argv) < 2:
        print("쓰기: python cancel.py <취소할 승인번호>")
        print("예   : python cancel.py 20260101-10260101-12345678")
        sys.exit(1)
    target = sys.argv[1].strip()
    print(f"{target} 건을 전액 취소합니다. 사유는 '착오에 의한 이중발급 등' 입니다.")
    got = cancel(target)
    if got:
        print(f"취소 발급 완료. 취소 승인번호 {got}")
    else:
        print("승인번호를 못 받았습니다. 화면을 직접 확인하세요.")


if __name__ == "__main__":
    main()
