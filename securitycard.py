"""
style-guard:off  (코드 주석이라 산문 문체 규칙 비적용 — CLAUDE.md 12절 비적용 범위)

== 운영 맥락 ==
용도: 홈택스 보안카드 인증 창이 묻는 번호를 저장해 둔 카드에서 찾아 대신 넣는다.
실행 시점: run_queue.py 가 발급하기 뒤 인증 단계에서 부른다.
입력: creds.py (%USERPROFILE%\\.hometax\\credentials.json)
출력: shots/card_*.png, logs/issue.log (번호만 남기고 숫자는 안 남긴다), logs/card_fail.lock

화면 구조 (2026-09-11 실측):
  발급하기 → 확인 → **새 창** "인증선택"(UTECMABA14)  : #mf_btnCrdcCert_type02 = 보안카드 인증
                    겉을 감싼 요소가 클릭을 가로채므로 코드로 눌러야 한다
  그 단추를 누르면 → **또 새 창** "전자세금계산서 발급용 보안카드"
                    글자: "[8]번 보안카드번호 앞 2자리", "[15]번 보안카드번호 뒤 2자리"
                    칸: #mf_inputCardNo1, #mf_inputCardNo2 (각각 두 글자)
                    단추: #mf_btnCfrm 확인하기 / #mf_btnCncl 취소

의도적 미구현: 틀렸을 때 재시도. 홈택스는 5회 틀리면 카드를 정지시키므로 1회 실패에 전체를 멈추고 사람을 부른다.
마지막 점검: 2026-09-11
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import creds
from issue import log, pause

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
FAIL_LOCK = BASE / "logs" / "card_fail.lock"

AUTH_WINDOW_TITLE = "인증선택"
CARD_WINDOW_TITLE = "보안카드"
BTN_CARD_AUTH = "#mf_btnCrdcCert_type02"
CARD_BOX1 = "#mf_inputCardNo1"
CARD_BOX2 = "#mf_inputCardNo2"
CARD_OK = "#mf_btnCfrm"


class CardBlocked(RuntimeError):
    """지난 실패가 안 풀린 상태라 사람이 확인하기 전에는 카드를 다시 쓰지 않는다."""


class CardScreenUnknown(RuntimeError):
    """화면이 예상과 달라 무엇을 물어보는지 확신할 수 없으므로 찍어보지 않고 멈춘다."""


def guard_ok() -> None:
    if FAIL_LOCK.exists():
        raise CardBlocked(
            "지난번 보안카드 인증이 실패해 멈춰 있으니, 홈택스에서 카드가 살아 있는지 확인한 뒤 "
            f"{FAIL_LOCK} 를 지우고 다시 시도한다. 5회 틀리면 카드가 정지된다."
        )


def mark_failed(reason: str) -> None:
    FAIL_LOCK.parent.mkdir(exist_ok=True)
    FAIL_LOCK.write_text(reason[:500], encoding="utf-8")
    log(f"[보안카드] 실패로 잠금: {reason[:200]}")


def clear_failed() -> None:
    if FAIL_LOCK.exists():
        FAIL_LOCK.unlink()


def find_window(ctx, title_part: str, timeout_sec: int = 20):
    """제목에 그 낱말이 든 창을 찾는다. 없으면 기다렸다가 다시 본다."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        for page in list(ctx.pages):
            try:
                if page.is_closed():
                    continue
                if title_part in (page.title() or ""):
                    return page
            except Exception:
                continue
        time.sleep(1)
    return None


def parse_asked_positions(text: str) -> list[dict]:
    """'[8]번 보안카드번호 앞 2자리' 같은 요구를 나온 차례대로 뽑는다."""
    asked: list[dict] = []
    # 실측 형태가 첫째다
    for m in re.finditer(r"\[?(\d{1,2})\]?\s*번[^\n]{0,20}?(앞|뒤)\s*(\d)\s*자리", text):
        pos, half, count = int(m.group(1)), m.group(2), int(m.group(3))
        if 1 <= pos <= 50:
            item = {"position": pos, "half": half, "count": count}
            if item not in asked:
                asked.append(item)
    if asked:
        return asked
    # 자리 표시가 없는 옛 형태
    for m in re.finditer(r"\[?(\d{1,2})\]?\s*번(?!호)", text):
        pos = int(m.group(1))
        if 1 <= pos <= 50:
            item = {"position": pos, "half": None, "count": 4}
            if item not in asked:
                asked.append(item)
    return asked


def digits_for(item: dict) -> str:
    """저장된 카드에서 요구에 맞는 숫자만 잘라 돌려주되, 네 개가 아니면 멈춘다."""
    full = creds.card_number(item["position"])
    if len(full) != 4:
        raise CardScreenUnknown(
            f"저장된 {item['position']}번이 네 개가 아니므로 setup_creds.py 로 다시 넣어야 한다."
        )
    if item["half"] == "앞":
        return full[: item["count"]]
    if item["half"] == "뒤":
        return full[-item["count"]:]
    return full


def choose_card_auth(ctx) -> bool:
    """인증선택 창에서 보안카드 인증을 고른다. 겉을 감싼 요소 때문에 코드로 누른다.

    홈택스가 지난 선택을 기억해 인증선택을 건너뛰고 보안카드 창을 바로 띄우기도 한다.
    """
    if find_window(ctx, CARD_WINDOW_TITLE, timeout_sec=3) is not None:
        log("[보안카드] 인증선택을 건너뛰고 보안카드 창이 바로 떴다")
        return True
    auth = find_window(ctx, AUTH_WINDOW_TITLE)
    if auth is None:
        return False
    auth.bring_to_front()
    auth.wait_for_timeout(500)
    try:
        auth.evaluate(f"document.querySelector('{BTN_CARD_AUTH}').click()")
    except Exception as e:
        raise CardScreenUnknown(f"보안카드 인증 단추를 누르지 못했다: {str(e)[:150]}")
    log("[보안카드] 인증선택 창에서 보안카드 인증을 눌렀다")
    return find_window(ctx, CARD_WINDOW_TITLE, timeout_sec=25) is not None


def authenticate(ctx, tag: str) -> None:
    """보안카드 창이 묻는 번호를 채우고 확인하기를 누른다. 확신이 없으면 아무것도 누르지 않는다."""
    guard_ok()
    card_win = find_window(ctx, CARD_WINDOW_TITLE)
    if card_win is None:
        raise CardScreenUnknown("보안카드 입력 창을 찾지 못했다.")
    card_win.bring_to_front()
    card_win.wait_for_timeout(1200)
    card_win.screenshot(path=str(BASE / "shots" / f"card_{tag}_1_screen.png"))

    text = card_win.evaluate("document.body.innerText") or ""
    asked = parse_asked_positions(text)
    boxes = [card_win.locator(CARD_BOX1), card_win.locator(CARD_BOX2)]
    log(f"[보안카드][{tag}] 화면이 요구한 번호: {[(a['position'], a['half']) for a in asked]}")

    if len(asked) != 2:
        raise CardScreenUnknown(f"물어본 번호가 둘이 아니다({len(asked)}개). 화면 글자: {text[:200]}")
    if not all(b.count() and b.first.is_visible() for b in boxes):
        raise CardScreenUnknown("보안카드 입력칸 두 개를 찾지 못했다.")

    for item, box in zip(asked, boxes):
        digits = digits_for(item)
        if len(digits) != item["count"]:
            raise CardScreenUnknown(
                f"{item['position']}번에서 {item['count']}개를 잘라야 하는데 {len(digits)}개가 나왔다."
            )
        box.first.click()
        pause(0.1, 0.3)
        card_win.keyboard.press("Control+A")
        card_win.keyboard.type(digits, delay=90)
        pause(0.2, 0.4)

    card_win.screenshot(path=str(BASE / "shots" / f"card_{tag}_2_filled.png"))

    ok = card_win.locator(CARD_OK)
    if not ok.count():
        raise CardScreenUnknown("확인하기 단추를 찾지 못해, 숫자는 넣었지만 누르지 않았다.")
    try:
        ok.first.click(timeout=8000)
    except Exception:
        card_win.evaluate(f"document.querySelector('{CARD_OK}').click()")
    log(f"[보안카드][{tag}] 확인하기 눌렀다")

    # 창이 닫히면 통과한 것이다
    deadline = time.time() + 30
    while time.time() < deadline:
        if card_win.is_closed():
            log(f"[보안카드][{tag}] 인증 창이 닫혔다")
            clear_failed()
            return
        try:
            after = card_win.evaluate("document.body.innerText") or ""
        except Exception:
            log(f"[보안카드][{tag}] 인증 창이 닫혔다")
            clear_failed()
            return
        for bad in ("일치하지", "올바르지", "다시 입력", "오류", "정지", "잘못", "확인하시기"):
            if bad in after and bad not in text:
                card_win.screenshot(path=str(BASE / "shots" / f"card_{tag}_3_error.png"))
                mark_failed(f"홈택스 안내에 '{bad}' 가 떴다: {after[:200]}")
                raise CardBlocked(
                    f"보안카드 인증이 거절됐고({bad}), 5회 틀리면 카드가 정지되므로 더 시도하지 않고 멈춘다."
                )
        time.sleep(2)
    clear_failed()
    log(f"[보안카드][{tag}] 창이 남아 있지만 거절 안내는 없다")
