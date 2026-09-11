"""
== 운영 맥락 ==
용도: 홈택스 아이디 로그인을 사람처럼 대신 한다(보안카드 발급 흐름의 무인화 1단계).
실행 시점: run_queue.py 가 발행 전에 부름. 손으로 확인하려면 `python login.py`.
입력: creds.py 가 읽는 %USERPROFILE%\\.hometax\\credentials.json. 크롬은 launch_chrome.ps1 로 띄운 9260.
출력: shots/login_*.png, logs/issue.log. 아이디·비밀번호는 어디에도 안 찍는다.
외부 의존: 홈택스 로그인 화면(loginboxFrame). 화면 개편되면 `python ht.py dump` 로 재정찰.
의도적 미구현: 로그인 실패 재시도 — 계정 잠금 위험이라 1회 실패면 즉시 멈추고 사람에게 알린다(CLAUDE.md 5절).
마지막 점검: 2026-09-10
"""
from __future__ import annotations

import random
import sys
import time

from playwright.sync_api import sync_playwright

import creds
from ht import connect, shot
from issue import log, pause

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ID_BOX = "#mf_txppWframe_loginboxFrame_iptUserId"
PW_BOX = "#mf_txppWframe_loginboxFrame_iptUserPw"
LOGIN_FRAME = "#mf_txppWframe_loginboxFrame"


class LoginFailed(RuntimeError):
    pass


def is_logged_in(page) -> bool:
    """머리말에 '회원정보조회' 가 있으면 로그인, '부서사용자신청' 이 있으면 로그아웃 상태다.

    예전엔 '로그아웃' 글자만 봤는데 로그인 화면에도 그 낱말이 있어 잘못 판정했다(2026-09-11).
    """
    try:
        body = page.evaluate("document.body.innerText") or ""
    except Exception:
        return False
    if "회원정보조회" in body:
        return True
    if "부서사용자신청" in body or "아이디를 입력해주세요" in body:
        return False
    return "로그아웃" in body


def _type_secret(page, selector, secret):
    """비밀번호를 사람처럼 친다. 글자를 로그·화면에 남기지 않는다."""
    box = page.locator(selector)
    box.scroll_into_view_if_needed()
    box.click()
    pause(0.1, 0.3)
    page.keyboard.press("Control+A")
    page.keyboard.type(secret, delay=random.randint(50, 150))
    pause(0.2, 0.5)


RRN_HINTS = ("주민등록번호", "주민번호", "생년월일", "2차 인증", "2차인증")
# 아이디 로그인 뒤 뜨는 2차 인증 팝업 (2026-09-11 실측)
RRN_POPUP = "#mf_txppWframe_loginboxFrame_UTXPPABC12"
RRN_BOX1 = RRN_POPUP + "_wframe_iptUserJuminNo1"   # 앞 6개
RRN_BOX2 = RRN_POPUP + "_wframe_iptUserJuminNo2"   # 뒤 1개
RRN_OK = RRN_POPUP + "_wframe_trigger46"           # 확인


def fill_rrn_step(page) -> bool:
    """로그인 다음에 주민등록번호 앞 6자리를 묻는 화면이 나오면 채우고 넘어간다."""
    try:
        body = page.evaluate("document.body.innerText") or ""
    except Exception:
        return False
    if not any(h in body for h in RRN_HINTS):
        return False
    if not creds.has_rrn():
        log("[로그인] 주민번호를 묻는데 저장돼 있지 않음")
        return False

    # 실측한 2차 인증 팝업이면 그 칸을 바로 지목한다
    if page.locator(RRN_BOX1).count() and page.locator(RRN_BOX1).first.is_visible():
        for sel, digits in ((RRN_BOX1, creds.rrn_front()), (RRN_BOX2, creds.rrn_back1())):
            box = page.locator(sel).first
            box.click()
            pause(0.1, 0.3)
            page.keyboard.press("Control+A")
            page.keyboard.type(digits, delay=random.randint(60, 140))
            pause(0.2, 0.4)
        page.locator(RRN_OK).first.click()
        log("[로그인] 2차 인증 팝업에 주민번호 넣고 확인 눌렀습니다")
        page.wait_for_timeout(5000)
        # 팝업이 닫혀도 화면 글자가 늦게 바뀌어 로그인을 못 알아보므로 한 번 새로 연다
        try:
            page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
        except Exception:
            pass
        return True

    boxes = page.locator(
        "input[type=text]:visible, input[type=password]:visible, input[type=tel]:visible, input[type=number]:visible"
    )
    empty = []
    for i in range(boxes.count()):
        box = boxes.nth(i)
        try:
            if not box.is_visible() or box.is_disabled():
                continue
            if (box.input_value() or "").strip():
                continue
            empty.append(box)
        except Exception:
            continue
    if not empty:
        log("[로그인] 주민번호 입력칸을 못 찾음")
        return False

    front, back = creds.rrn_front(), creds.rrn_back1()
    if len(empty) >= 2:
        targets = [(empty[0], front), (empty[1], back)]
        log("[로그인] 주민번호 입력칸 2개에 앞 6개와 뒤 1개를 나눠 넣습니다")
    else:
        targets = [(empty[0], front + back)]
        log("[로그인] 주민번호 입력칸 1개에 일곱 개를 이어서 넣습니다")

    for box, digits in targets:
        box.click()
        pause(0.1, 0.3)
        page.keyboard.type(digits, delay=random.randint(60, 140))
        pause(0.2, 0.4)

    for label in ("확인", "로그인", "다음"):
        btn = page.locator(f"input[type=button][value='{label}'], button:has-text('{label}')")
        for i in range(btn.count()):
            if btn.nth(i).is_visible():
                btn.nth(i).click()
                log(f"[로그인] 주민번호 넣고 '{label}' 눌렀습니다")
                page.wait_for_timeout(3000)
                return True
    page.keyboard.press("Enter")
    log("[로그인] 주민번호 넣고 Enter 눌렀습니다")
    page.wait_for_timeout(3000)
    return True


def ensure_login(page, timeout_min=3) -> bool:
    """이미 로그인돼 있으면 그대로, 아니면 아이디 로그인을 한 번만 시도한다."""
    if is_logged_in(page):
        log("[로그인] 이미 로그인 상태")
        return True

    if "hometax.go.kr" not in (page.url or ""):
        page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

    # 로그인 상자가 안 보이면 "아이디 로그인" 을 눌러 연다
    if page.locator(ID_BOX).count() == 0 or not page.locator(ID_BOX).first.is_visible():
        try:
            page.get_by_text("아이디 로그인", exact=True).first.click()
            page.wait_for_timeout(3000)
        except Exception:
            page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            page.get_by_text("아이디 로그인", exact=True).first.click()
            page.wait_for_timeout(3000)

    page.wait_for_selector(ID_BOX, timeout=15000)

    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))

    _type_secret(page, ID_BOX, creds.login_id())
    _type_secret(page, PW_BOX, creds.login_pw())

    btn = page.locator(f"{LOGIN_FRAME} input[type=button][value='로그인'], {LOGIN_FRAME} button:has-text('로그인')")
    clicked = False
    for i in range(btn.count()):
        if btn.nth(i).is_visible():
            btn.nth(i).click()
            clicked = True
            break
    if not clicked:
        page.keyboard.press("Enter")

    deadline = time.time() + timeout_min * 60
    rrn_done = False
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        if is_logged_in(page):
            log("[로그인] 아이디 로그인 성공")
            return True
        if not rrn_done and fill_rrn_step(page):
            rrn_done = True
            continue
        if dialogs:
            shot(page, "login_failed")
            raise LoginFailed(f"홈택스 안내: {dialogs[-1][:200]}")
        try:
            body = page.evaluate("document.body.innerText") or ""
        except Exception:
            body = ""
        for bad in ("비밀번호가 일치하지", "아이디를 확인", "존재하지 않는", "잠겼", "5회", "일시적으로 이용"):
            if bad in body:
                shot(page, "login_failed")
                raise LoginFailed(f"로그인 화면 안내에 '{bad}' 가 떴습니다. 다시 시도하지 않고 멈춥니다.")

    shot(page, "login_timeout")
    raise LoginFailed(f"{timeout_min}분 안에 로그인이 안 끝났습니다. 추가 인증 화면일 수 있습니다.")


def main():
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        try:
            ensure_login(page)
            shot(page, "login_ok")
            print("로그인 완료")
        except creds.MissingCredentials as e:
            print(e)
            sys.exit(2)
        except LoginFailed as e:
            print("로그인 실패:", e)
            print("계정 잠금을 막으려고 다시 시도하지 않습니다. 크롬 창에서 직접 확인하세요.")
            sys.exit(3)


if __name__ == "__main__":
    main()
