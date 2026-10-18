"""
style-guard:off  (코드 저장소 도구 — CLAUDE.md 12절 비적용 범위)

== 운영 맥락 ==
용도: 이미 발행한 세금계산서를 전액 취소하거나 금액 일부를 줄인다(수정발급, 음수 1장).
      사유는 세 가지다. "이중발급"(착오에 의한 이중발급 등, 작성일자=당초 작성일자)과
      "계약해제"(계약의 해제, 작성일자=해제일. 환불·계약 취소는 이쪽이다)는 전액 취소이고,
      "공급가액변동"(공급가액의 변동, 감소만)은 줄일 금액만 음수로 한 장 낸다(2026-09-29 사용자 지시로 추가).
      공급가액변동 항목은 공급가액·세액에 **줄일 금액**을 양수로 담는다(당초 금액은 당초_공급가액).
실행 시점: 대기열(run_queue.py)이 kind=cancel 항목을 만나면 prepare_cancel 을 부른다(텔레그램 "세취").
          손으로는 `python cancel.py <승인번호> [--reason 이중발급|계약해제|공급가액변동] [--date 2026-09-29]
          [--amount 80000 --tax 8000] [--dry]`. --amount 는 공급가액변동에서 줄일 공급가액이다.
          --dry 는 취소분이 채워진 화면까지만 가고 발급 단추를 누르지 않는다(화면 확인용).
입력: 취소할 승인번호. 자격은 creds.py, 크롬은 launch_chrome.ps1 로 띄운 9260.
출력: shots/issue_<tag>_*.png, logs/issue.log, 화면에 취소 승인번호.

화면 흐름 (2026-09-11 실측, 계약의 해제는 2026-09-29 실측):
  검색창에 "수정발급" -> 결과 줄 클릭 -> 수정발급 화면.
  이 화면은 아이디 로그인만으로는 **잠겨 있다**. 인증선택 창이 따로 뜨고 보안카드로 통과하면 풀린다.
  승인번호 칸 #mf_txppWframe_edtAprvNo1, 확인은 #mf_txppWframe_grp1030(입력 단추가 아니라 A 요소).
  사유 카드의 발급하기: 이중발급 #mf_txppWframe_textbox948, 계약의 해제 #mf_txppWframe_textbox956.
  계약의 해제는 안내창(mdfRsnMsgPopup)이 먼저 뜨고 확인 #..._wframe_trigger20 을 눌러야 넘어간다.
  그다음은 일반 발급과 같은 화면(요소 이름도 같다)이고 두 사유 모두 금액이 음수로 자동으로 채워진다.
  계약의 해제는 작성일자가 오늘로, 비고가 "당초 작성일자 (...), 당초 승인번호 (...)" 로 자동으로 들어온다.
  공급가액의 변동(#mf_txppWframe_textbox952)은 안내창에 증가(_wframe_trigger23)·감소(_wframe_trigger24) 단추가 뜬다.
  감소를 누르면 작성일자 오늘, 비고와 품목 이름은 자동, **공급가액과 세액은 비어 있어** 직접 넣는다.

함정: **홈택스는 이미 취소한 건을 다시 넣어도 사유 선택과 작성 화면까지 그대로 열어 준다**(2026-09-29 실측).
      같은 건이 두 번 취소되지 않게 막는 일은 접수 쪽(Mac hometax_issue.py 의 취소 장부)이 맡는다.

의도적 미구현: 다른 수정사유(기재사항 정정, 환입, 내국신용장), 공급가액을 늘리는 변동, 품목이 여러 줄인 계산서의 감액.
              나머지는 사람이 화면에서 한다.
마지막 점검: 2026-09-29
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import securitycard as SC
from ht import accept_quiet, connect, shot
from issue import F, capture_preview, human_type, log, read_layer_messages, sign_and_wait
from login import ensure_login, is_logged_in

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APRV_BOX = "#mf_txppWframe_edtAprvNo1"
APRV_OK = "#mf_txppWframe_grp1030"
REASON_DUP = "#mf_txppWframe_textbox948"
REASON_NOTICE_OK = "#mf_txppWframe_mdfRsnMsgPopup_wframe_trigger20"  # 계약의 해제 안내창의 확인
REASON_NOTICE_DOWN = "#mf_txppWframe_mdfRsnMsgPopup_wframe_trigger24"  # 공급가액의 변동 안내창의 감소
REASONS = {
    # key: (사유 선택 화면의 카드 제목, 그 카드의 발급하기, 작성 화면 수정사유 칸에 찍히는 글, 안내창에서 누를 단추)
    "이중발급": ("착오에 의한 이중발급 등", REASON_DUP, "착오에 의한 이중발급", None),
    "계약해제": ("계약의 해제", "#mf_txppWframe_textbox956", "계약의 해제", REASON_NOTICE_OK),
    "공급가액변동": ("공급가액의 변동", "#mf_txppWframe_textbox952", "공급가액 변동", REASON_NOTICE_DOWN),
}
PARTIAL = "공급가액변동"
# 수정발급 화면의 미리보기 창. 일반 발급과 창·단추 이름이 다르다(2026-09-29 실측).
# 바로 옆 _wframe_btnProcessView 는 발급하기라 누르지 않는다.
AMEND_PREVIEW = "#mf_txppWframe_mdfIsnPreviewPopup"
AMEND_PREVIEW_CLOSERS = (AMEND_PREVIEW + "_wframe_btnCancelView", AMEND_PREVIEW + "_wframe_btnCancelView2")
APPROVAL_RE = re.compile(r"^\d{8}-\d{8}-\d{8}$")

READ_FORM_JS = """
(bsno) => {
  const v = id => { const e = document.querySelector('#mf_txppWframe_' + id); return e ? (e.value || '').trim() : ''; };
  const rows = [];
  for (let i = 0; i < 16; i++) {
    if (!document.querySelector(`#mf_txppWframe_genEtxivLsatTop_${i}_edtLsatSplCftTop`)) break;
    rows.push([v(`genEtxivLsatTop_${i}_edtLsatSplCftTop`), v(`genEtxivLsatTop_${i}_edtLsatTxamtTop`)]);
  }
  // 묶음의 첫 칸은 묶음 제목("수정사유")과 항목 이름 두 글자표가 같은 칸을 가리킨다. 그래서 글자표 쪽에서 찾는다.
  const checked = [...document.querySelectorAll('label[for]')].filter(l => {
    const r = document.getElementById(l.htmlFor);
    return r && r.type === 'radio' && r.checked;
  }).map(l => (l.innerText || '').trim()).filter(Boolean);
  const body = document.body.innerText || '';
  return {name: v('edtDmnrTnmNmTop'), date: v('calWrtDtTop_input'), remark: v('edtRmrkCntnTop'),
          rows, checked, has_bsno: bsno ? body.includes(bsno) : null};
}
"""


class CancelCheckFailed(RuntimeError):
    """취소분 화면이 접수한 내용과 달라 발급 단추를 누르지 않았다. 다시 해도 같은 결과라 재시도하지 않는다."""


def _num(s) -> int:
    t = re.sub(r"[^0-9\-]", "", str(s or ""))
    return int(t) if t not in ("", "-") else 0


def fmt_bsno(raw) -> str:
    d = re.sub(r"[^0-9]", "", str(raw or ""))
    return f"{d[:3]}-{d[3:5]}-{d[5:]}" if len(d) == 10 else ""


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


def read_form(page, bsno: str = "") -> dict:
    """작성 화면에 채워진 값을 읽는다. 공급가액·세액은 줄 합계."""
    got = page.evaluate(READ_FORM_JS, bsno)
    got["공급가액"] = sum(_num(r[0]) for r in got["rows"])
    got["세액"] = sum(_num(r[1]) for r in got["rows"])
    got["줄수"] = sum(1 for r in got["rows"] if r[0])
    return got


def check_form(seen: dict, item: dict, reason: str) -> None:
    """발급 단추를 누르기 전 마지막 검사. 하나라도 어긋나면 누르지 않는다."""
    supply, tax = seen["공급가액"], seen["세액"]
    if supply >= 0 or tax > 0:
        raise CancelCheckFailed(f"취소분 금액이 음수가 아닙니다(공급가액 {supply:,}, 세액 {tax:,}).")
    want = int(item.get("확인_공급가액") or item.get("공급가액") or 0)
    if want and abs(supply) != want:
        raise CancelCheckFailed(
            f"화면의 취소 금액({abs(supply):,}원)이 접수한 금액({want:,}원)과 다릅니다. 승인번호를 확인해 주세요.")
    want_tax = item.get("세액")
    if want and want_tax is not None and abs(tax) != int(want_tax):
        raise CancelCheckFailed(
            f"화면의 취소 세액({abs(tax):,}원)이 접수한 세액({int(want_tax):,}원)과 다릅니다.")
    if seen.get("has_bsno") is False:
        raise CancelCheckFailed(
            f"화면의 공급받는자가 접수한 거래처({item.get('공급받는자_상호', '')})와 다릅니다. 화면 상호: {seen.get('name')}")
    if reason == PARTIAL:
        if not want or item.get("세액") is None:
            raise CancelCheckFailed("줄일 공급가액과 세액이 접수 내용에 없습니다.")
        if seen["줄수"] != 1:
            raise CancelCheckFailed(f"금액이 든 품목 줄이 {seen['줄수']}개입니다. 감액은 한 줄짜리만 다룹니다.")
        base = int(item.get("당초_공급가액") or 0)
        if base and want >= base:
            raise CancelCheckFailed(f"줄일 금액({want:,}원)이 당초 공급가액({base:,}원)보다 작아야 합니다.")
    label = REASONS[reason][2]
    if seen.get("checked") and not any(label in c for c in seen["checked"]):
        raise CancelCheckFailed(f"화면의 수정사유가 '{label}' 이 아닙니다({', '.join(seen['checked'])[:80]}).")


def set_written_date(page, seen: dict, want: str) -> None:
    """계약의 해제에서 해제일이 오늘이 아닐 때 작성일자와 품목 줄의 '일'을 바꾼다."""
    human_type(page, F["date"], want.replace("-", ""))
    day = want.split("-")[2]
    for i in range(max(seen["줄수"], 1)):
        human_type(page, F["row_dd"].format(i=i), day)
    page.wait_for_timeout(800)


def prepare_cancel(ctx, page, item: dict, tag: str) -> dict:
    """수정발급 화면을 열어 취소분이 채워진 작성 화면까지 간다. 발급 단추는 누르지 않는다.

    화면에서 읽은 값(상호·공급가액·세액·작성일자·비고)을 돌려준다. 여기까지는 아무것도 발급되지 않으므로
    실패하면 다시 해도 된다. 단 CancelCheckFailed 는 다시 해도 같은 결과라 그대로 올린다.
    """
    approval_no = str(item.get("당초_승인번호") or "").strip()
    reason = item.get("사유") or "이중발급"
    if not APPROVAL_RE.match(approval_no):
        raise CancelCheckFailed(f"승인번호 형식이 아닙니다: {approval_no[:40]}")
    if reason not in REASONS:
        raise CancelCheckFailed(f"다루지 않는 수정사유입니다: {reason}")
    title, card, _, notice_btn = REASONS[reason]
    if reason == PARTIAL and (int(item.get("공급가액") or 0) <= 0 or item.get("세액") is None
                              or int(item.get("세액")) < 0):
        raise CancelCheckFailed("감액은 줄일 공급가액과 세액이 있어야 합니다.")
    page.on("dialog", lambda d: (log(f"[{tag}] 안내창: {d.message[:150]}"), accept_quiet(d)))

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
    if title not in text or "발급하기" not in text:
        shot(page, f"issue_{tag}_no_reason")
        raise RuntimeError(f"사유 선택 화면이 안 나왔습니다. 안내: {read_layer_messages(ctx, page)[:200]}")

    page.locator(card).click()
    page.wait_for_timeout(3000)
    if notice_btn:
        notice = page.locator(notice_btn)
        if not (notice.count() and notice.first.is_visible()):
            shot(page, f"issue_{tag}_no_notice")
            raise RuntimeError("사유 안내창이 안 떴습니다.")
        notice.first.click()
    page.wait_for_timeout(8000)

    bsno = fmt_bsno(item.get("공급받는자_사업자번호"))
    seen = read_form(page, bsno)
    if reason == PARTIAL:
        if seen["공급가액"] or seen["세액"]:
            raise CancelCheckFailed(f"감액 화면에 금액이 이미 들어 있습니다(공급가액 {seen['공급가액']:,}). 직접 확인하세요.")
        human_type(page, F["row_supply"].format(i=0), -int(item["공급가액"]))
        human_type(page, F["row_tax"].format(i=0), -int(item["세액"]))
        page.wait_for_timeout(800)
        seen = read_form(page, bsno)
    want_date = str(item.get("작성일자") or "")
    if reason in ("계약해제", PARTIAL) and re.match(r"^\d{4}-\d{2}-\d{2}$", want_date) and seen["date"] != want_date:
        log(f"[{tag}] 작성일자를 해제일 {want_date} 로 바꾼다(화면 기본값 {seen['date']})")
        set_written_date(page, seen, want_date)
        seen = read_form(page, bsno)
        if seen["date"] != want_date:
            shot(page, f"issue_{tag}_date_mismatch")
            raise CancelCheckFailed(f"작성일자를 {want_date} 로 바꾸지 못했습니다(화면 {seen['date']}).")
    shot(page, f"issue_{tag}_2_filled")
    log(f"[{tag}] 당초 {approval_no} 취소분: {seen['name']} 공급가액 {seen['공급가액']:,} 세액 {seen['세액']:,} "
        f"작성일자 {seen['date']} 사유 {reason} 화면 사유 {seen['checked']}")
    check_form(seen, item, reason)
    return seen


def capture_cancel_preview(ctx, page, item: dict, tag: str):
    """취소분 미리보기 양식을 찍고 닫은 뒤, 닫으면서 입력값이 바뀌지 않았는지 다시 검사한다."""
    path = capture_preview(ctx, page, tag, popup=AMEND_PREVIEW, closers=AMEND_PREVIEW_CLOSERS)
    check_form(read_form(page, fmt_bsno(item.get("공급받는자_사업자번호"))), item, item.get("사유") or "이중발급")
    return path


def cancel(approval_no: str, reason: str = "이중발급", date: str = "", dry: bool = False,
           amount: int = 0, tax: int | None = None) -> str | None:
    item = {"당초_승인번호": approval_no, "사유": reason, "작성일자": date}
    if reason == PARTIAL:
        item.update({"공급가액": amount, "세액": round(amount * 0.1) if tax is None else tax})
    tag = f"cancel_{approval_no[-8:]}"
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        page.bring_to_front()
        page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if not is_logged_in(page):
            ensure_login(page)

        seen = prepare_cancel(ctx, page, item, tag)
        print(f"취소분 화면: {seen['name']} 공급가액 {seen['공급가액']:,} 세액 {seen['세액']:,} "
              f"작성일자 {seen['date']} 비고 {seen['remark'][:60]}")
        if dry:
            form = capture_cancel_preview(ctx, page, item, tag)
            print("발급 단추를 누르지 않고 끝냅니다(--dry). 미리보기", form)
            page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            return None
        return sign_and_wait(ctx, page, tag, use_card=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="발행한 세금계산서를 전액 취소한다")
    ap.add_argument("approval", help="취소할 승인번호. 예: 20260101-10260101-12345678")
    ap.add_argument("--reason", choices=sorted(REASONS), default="이중발급",
                    help="이중발급=잘못 발행(작성일자는 당초 날짜), 계약해제=환불·계약 취소(작성일자는 해제일)")
    ap.add_argument("--date", default="", help="계약해제·공급가액변동일 때 작성일자 YYYY-MM-DD. 비우면 오늘")
    ap.add_argument("--amount", type=int, default=0, help="공급가액변동에서 줄일 공급가액(양수로 적는다)")
    ap.add_argument("--tax", type=int, default=None, help="공급가액변동에서 줄일 세액. 비우면 공급가액의 10%%")
    ap.add_argument("--dry", action="store_true", help="취소분 화면까지만 가고 발급하지 않는다")
    ap.add_argument("--result", default="", help="수정발급 승인번호·상태·캡처 경로를 적을 JSON 파일(AI 오피스가 읽음)")
    a = ap.parse_args()
    target = a.approval.strip()
    started = time.time()
    def write_result(status, got="", err=""):
        if not a.result:
            return
        from ht import SHOTS as _S
        shots = sorted(str(p) for p in _S.glob("issue_cancel_*.png") if p.stat().st_mtime >= started - 1)
        try:
            Path(a.result).write_text(json.dumps({"approval": got or "", "status": status, "shots": shots, "error": err, "target": target, "reason": a.reason}, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as ex:  # noqa
            print("결과 파일 쓰기 실패:", str(ex)[:120])
    if a.reason == PARTIAL:
        if a.amount <= 0:
            ap.error("공급가액변동은 --amount 로 줄일 공급가액을 적어야 합니다")
        print(f"{target} 건의 공급가액을 {a.amount:,}원 줄입니다. 사유는 '{REASONS[a.reason][0]}'(감소) 입니다.")
    else:
        print(f"{target} 건을 전액 취소합니다. 사유는 '{REASONS[a.reason][0]}' 입니다.")
    write_result("start")
    try:
        got = cancel(target, a.reason, a.date, a.dry, a.amount, a.tax)
    except CancelCheckFailed as ex:
        write_result("rejected", err=str(ex)[:200]); raise
    except Exception as ex:  # noqa
        write_result("check", err=str(ex).split("Call log")[0].strip()[:200]); raise
    if a.dry:
        write_result("dry"); return
    if got:
        write_result("done", got)
        print(f"수정발급 완료. 승인번호 {got}")
    else:
        write_result("check")
        print("승인번호를 못 받았습니다. 화면을 직접 확인하세요.")


if __name__ == "__main__":
    main()
