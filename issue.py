"""
== 운영 맥락 ==
용도: 홈택스 "전자(세금)계산서 건별발급" 화면(menuCd=UTEETBAA01)에 발행 1건을 사람처럼 입력한다.
실행 시점: 수동. `python issue.py fill invoices.json --index 0` 로 채우기만(발급 안 함) →
          `python issue.py fill ... --issue` 로 발급하기 버튼까지. 서명(인증서/보안카드) 창은 사람이 처리.
입력: invoices.json (invoices.sample.json 형식). 크롬은 launch_chrome.ps1 로 띄운 9260 프로필, 로그인은 사람이.
출력: shots/issue_*.png (채운 화면·미리보기·결과), logs/issue.log
외부 의존: 홈택스 WebSquare 화면. 요소 id 는 2026-09-06 정찰값(`mf_txppWframe_*`). 화면 개편되면 dump 로 재정찰.
의도적 미구현: 발급하기 뒤 서명 창 자동 입력(보안카드 값 자동 입력은 사용자 결정 뒤), 일괄발급(엑셀) 경로, 수정발급.
마지막 점검: 2026-09-06
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from ht import connect, main_eval, shot, SHOTS, BASE

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOG = BASE / "logs" / "issue.log"
ISSUE_URL = "https://hometax.go.kr/websquare/websquare.html?w2xPath=/ui/pp/index_pp.xml&menuCd=UTEETBAA01"
P = "#mf_txppWframe_"

F = {
    "buyer_bsno": P + "edtDmnrBsnoTop",
    "buyer_bsno_ok": P + "btnDmnrBsnoCnfrTop",
    "buyer_name": P + "edtDmnrTnmNmTop",
    "buyer_rep": P + "edtDmnrRprsFnmTop",
    "buyer_addr": P + "edtDmnrPfbAdrTop",
    "buyer_biz": P + "edtDmnrBcNmTop",
    "buyer_item": P + "edtDmnrItmNmTop",
    "buyer_email_id": P + "edtDmnrMchrgEmlIdTop",
    "buyer_email_dom": P + "edtDmnrMchrgEmlDmanTop",
    "date": P + "calWrtDtTop_input",
    "remark": P + "edtRmrkCntnTop",
    "row_dd": P + "genEtxivLsatTop_{i}_edtLsatSplDdTop",
    "row_name": P + "genEtxivLsatTop_{i}_edtLsatNmTop",
    "row_spec": P + "genEtxivLsatTop_{i}_edtLsatRszeNmTop",
    "row_qty": P + "genEtxivLsatTop_{i}_edtLsatQtyTop",
    "row_unit": P + "genEtxivLsatTop_{i}_edtLsatUtprcTop",
    "row_supply": P + "genEtxivLsatTop_{i}_edtLsatSplCftTop",
    "row_tax": P + "genEtxivLsatTop_{i}_edtLsatTxamtTop",
    "row_remark": P + "genEtxivLsatTop_{i}_edtLsatRmrkCntnTop",
    "cash": P + "edtStlMthd10Top",
    "credit": P + "edtStlMthd40Top",
    "btn_init": P + "btnInit",
    "btn_preview": P + "btnIsnPreview",
    "btn_hold": P + "btnIsnRsrv",
    "btn_issue": P + "btnIsn",
}


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pause(a=0.25, b=0.7):
    time.sleep(random.uniform(a, b))


def human_type(page, selector, text):
    """사람처럼: 칸 클릭 → 전체선택 → 글자 타이핑 → Tab(blur 로 화면 검증 유도)."""
    loc = page.locator(selector)
    loc.scroll_into_view_if_needed()
    loc.click()
    pause(0.1, 0.3)
    page.keyboard.press("Control+A")
    page.keyboard.type(str(text), delay=random.randint(40, 110))
    pause(0.1, 0.3)
    page.keyboard.press("Tab")
    pause()


def click_label(page, text):
    """숨은 radio 대신 눈에 보이는 라벨 글자를 클릭."""
    loc = page.get_by_text(text, exact=True)
    loc.first.scroll_into_view_if_needed()
    loc.first.click()
    pause()


UNIT_POPUP = "#mf_txppWframe_ABTIBsnoUnitPopup2"
CONFIRM_GO_CERT = "#mf_txppWframe_UTEETZZA89_wframe_trigger20"  # 발급 확인창의 확인(인증 화면 이동)


def handle_unit_popup(page, unit_no, tag):
    """공급받는자에 종사업장이 여러 개면 뜨는 선택창. 번호가 주어지면 그 행, 없으면 첫 행(본점)을 고른다."""
    pop = page.locator(UNIT_POPUP)
    if pop.count() == 0 or not pop.first.is_visible():
        return False
    rows = pop.locator("tr:has(input[type=radio])")
    n = rows.count()
    picked = None
    if unit_no:
        for i in range(n):
            if str(unit_no).zfill(4) in rows.nth(i).inner_text():
                picked = i
                break
    if picked is None:
        picked = 0
    rows.nth(picked).locator("input[type=radio], [role=radio], td").first.click()
    pause()
    pop.locator("input[type=button][value='선택'], button:has-text('선택')").first.click()
    page.wait_for_timeout(1500)
    log(f"[{tag}] 종사업장 선택창: {n}행 중 {picked+1}번째 선택")
    return True


def read_layer_messages(ctx, page):
    """WebSquare 안내창(레이어)의 글자를 읽는다. 없으면 빈 문자열."""
    js = """
    (() => {
      const els = [...document.querySelectorAll('[id*=popup], [id*=Popup], [class*=w2window], [class*=layer], [role=dialog]')];
      const seen = new Set(); const out = [];
      for (const e of els) {
        const r = e.getBoundingClientRect();
        if (r.width < 50 || r.height < 30) continue;
        const t = (e.innerText || '').trim().replace(/\\s+/g, ' ');
        if (t && !seen.has(t) && t.length < 600) { seen.add(t); out.push(t); }
      }
      return out.join(' || ');
    })()
    """
    try:
        return main_eval(ctx, page, js) or ""
    except Exception:
        return ""


def wait_result(ctx, page, tag, minutes=10):
    """사람이 인증서 비밀번호를 치는 동안 기다렸다가 승인번호 안내창을 읽는다."""
    import re
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        note = read_layer_messages(ctx, page)
        m = re.search(r"승인번호\s*:\s*([0-9\-]+)", note)
        if m:
            log(f"[{tag}] 발급 완료. 승인번호 {m.group(1)} / 안내: {note[:160]}")
            shot(page, f"issue_{tag}_7_done")
            for sel in ("input[type=button][value='확인']", "input[type=button][value='닫기']", "button:has-text('확인')", "button:has-text('닫기')"):
                b = page.locator(".w2popup_window " + sel)
                vis = [i for i in range(b.count()) if b.nth(i).is_visible()]
                if vis:
                    b.nth(vis[-1]).click()
                    break
            return m.group(1)
        if "오류" in note or "실패" in note:
            log(f"[{tag}] 발급 중 안내: {note[:200]}")
        page.wait_for_timeout(3000)
    log(f"[{tag}] {minutes}분 안에 승인번호 안내가 안 떠서 대기 종료. 화면을 직접 확인하세요")
    return None


def sign_and_wait(ctx, page, tag, use_card: bool):
    """발급하기 뒤 확인창과 인증을 지나 서명까지 하고 승인번호를 돌려준다.

    보안카드와 인증서 둘 다 따로 뜨는 창으로 열린다(2026-09-11 실측).
    """
    import securitycard  # 서로 부르는 것을 피하려고 여기서 불러온다

    page.locator(F["btn_issue"]).click()
    page.wait_for_timeout(4000)
    note = read_layer_messages(ctx, page)
    if "발급하시겠습니까" not in note:
        raise RuntimeError(f"발급 확인창이 예상과 다름: {note[:200]}")
    page.locator(CONFIRM_GO_CERT).click()
    page.wait_for_timeout(5000)

    if use_card:
        if not securitycard.choose_card_auth(ctx):
            raise RuntimeError("인증선택 창에서 보안카드 인증으로 넘어가지 못했습니다.")
        securitycard.authenticate(ctx, tag)
        page.bring_to_front()
        return wait_result(ctx, page, tag, minutes=3)

    page.locator(".w2popup_window").get_by_text("금융 인증").first.click()
    page.wait_for_timeout(5000)
    shot(page, f"issue_{tag}_cert")
    print("인증서 창이 떴습니다. 인증서를 고르고 비밀번호를 입력하세요.")
    return wait_result(ctx, page, tag, minutes=10)


def ensure_issue_page(page):
    if "UTEETBAA01" not in page.url:
        # 주소 직접 진입은 메인으로 튕김 → 사람처럼 메뉴를 눌러 들어간다
        if "hometax.go.kr" not in page.url:
            page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
        page.get_by_text("계산서·영수증·카드", exact=True).first.click()
        page.wait_for_timeout(2500)
        page.get_by_text("전자(세금)계산서 건별발급", exact=True).first.click()
        page.wait_for_timeout(6000)
    try:
        page.wait_for_selector(F["buyer_bsno"], timeout=20000)
    except Exception:
        body = page.evaluate("document.body.innerText") or ""
        if "로그아웃" not in body:
            raise SystemExit("홈택스 로그인이 풀렸습니다(30분 무동작). 크롬 창에서 다시 로그인한 뒤 재실행하세요.")
        raise


def split_email(email):
    if not email or "@" not in email:
        return "", ""
    a, b = email.split("@", 1)
    return a, b


def fill_invoice(pw_ctx, page, inv, tag):
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))

    ensure_issue_page(page)

    # 공급받는자
    bsno = str(inv["공급받는자_사업자번호"]).replace("-", "")
    human_type(page, F["buyer_bsno"], bsno)
    page.locator(F["buyer_bsno_ok"]).click()
    page.wait_for_timeout(2500)
    handle_unit_popup(page, inv.get("공급받는자_종사업장번호"), tag)
    msg = read_layer_messages(pw_ctx, page)
    log(f"[{tag}] 사업자번호 확인 응답: {msg[:300] or '(안내창 없음)'} / dialog={dialogs}")
    shot(page, f"issue_{tag}_1_bsno")
    # 안내창에 닫기/확인 버튼이 있으면 눌러 닫는다
    for label in ("확인", "닫기"):
        btn = page.locator(f"input[type=button][value='{label}'], button:has-text('{label}')").filter(has_not=page.locator(F["buyer_bsno_ok"]))
        try:
            visible = [i for i in range(btn.count()) if btn.nth(i).is_visible()]
        except Exception:
            visible = []
        if visible and msg:
            btn.nth(visible[-1]).click()
            pause()
            break

    human_type(page, F["buyer_name"], inv["공급받는자_상호"])
    human_type(page, F["buyer_rep"], inv["공급받는자_대표자"])
    if inv.get("공급받는자_주소"):
        human_type(page, F["buyer_addr"], inv["공급받는자_주소"])
    if inv.get("공급받는자_업태"):
        human_type(page, F["buyer_biz"], inv["공급받는자_업태"])
    if inv.get("공급받는자_종목"):
        human_type(page, F["buyer_item"], inv["공급받는자_종목"])
    eid, edom = split_email(inv.get("공급받는자_이메일", ""))
    if eid:
        human_type(page, F["buyer_email_id"], eid)
        human_type(page, F["buyer_email_dom"], edom)

    # 작성일자 (YYYY-MM-DD → 화면은 YYYY-MM-DD 표기)
    date = str(inv["작성일자"])
    human_type(page, F["date"], date.replace("-", ""))
    if inv.get("비고"):
        human_type(page, F["remark"], inv["비고"])

    # 품목 (최대 4행 기본 표시)
    items = inv.get("품목목록") or [{
        "품목": inv["품목"], "공급가액": inv["공급가액"], "세액": inv["세액"],
        "규격": inv.get("규격", ""), "수량": inv.get("수량", ""), "단가": inv.get("단가", ""),
    }]
    day = date.split("-")[2] if "-" in date else date[-2:]
    for i, it in enumerate(items[:4]):
        human_type(page, F["row_dd"].format(i=i), it.get("일", day))
        human_type(page, F["row_name"].format(i=i), it["품목"])
        if it.get("규격"):
            human_type(page, F["row_spec"].format(i=i), it["규격"])
        if it.get("수량"):
            human_type(page, F["row_qty"].format(i=i), it["수량"])
        if it.get("단가"):
            human_type(page, F["row_unit"].format(i=i), it["단가"])
        human_type(page, F["row_supply"].format(i=i), int(it["공급가액"]))
        human_type(page, F["row_tax"].format(i=i), int(it["세액"]))

    total = sum(int(it["공급가액"]) + int(it["세액"]) for it in items[:4])
    if inv.get("청구영수", "청구") == "영수":
        human_type(page, F["cash"], total)
        click_label(page, "영수")
    else:
        human_type(page, F["credit"], total)
        click_label(page, "청구")

    page.wait_for_timeout(800)
    shot(page, f"issue_{tag}_2_filled")
    # 합계 표기 확인
    sums = main_eval(pw_ctx, page, "(() => [...document.querySelectorAll('[id*=SumTop], [id*=TotTop], [id*=Amt]')].filter(e=>e.offsetWidth>0).map(e=>e.id+'='+(e.value||e.innerText||'').trim()).filter(s=>/\\d/.test(s)).slice(0,12).join(' | '))()")
    log(f"[{tag}] 화면 합계 칸: {sums}")
    return dialogs


def reset_form(page):
    """다음 건을 채우기 전에 화면을 비운다."""
    if "UTEETBAA01" not in (page.url or ""):
        return
    page.locator(F["btn_init"]).click()
    page.wait_for_timeout(1500)
    for label in ("확인", "예"):
        b = page.locator(f".w2popup_window input[type=button][value='{label}']")
        vis = [i for i in range(b.count()) if b.nth(i).is_visible()]
        if vis:
            b.nth(vis[-1]).click()
            break


def cmd_all(a):
    """목록에 있는 건을 차례로 발행한다. 한 건이라도 막히면 거기서 멈춘다."""
    import securitycard

    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    print(f"{len(data)}건을 차례로 발행합니다.")
    done, failed = [], []
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        for i, inv in enumerate(data):
            tag = f"{i:02d}_{str(inv.get('공급받는자_상호', ''))[:6]}"
            try:
                reset_form(page)
                log(f"[{tag}] 채우기 시작: {inv['공급받는자_상호']} "
                    f"공급가액 {int(inv['공급가액']):,} 세액 {int(inv['세액']):,}")
                fill_invoice(ctx, page, inv, tag)
                got = sign_and_wait(ctx, page, tag, use_card=a.card)
                if got:
                    done.append((inv["공급받는자_상호"], got))
                    print(f"  {i+1}/{len(data)} 발급 완료 {inv['공급받는자_상호']} 승인번호 {got}")
                else:
                    failed.append((inv["공급받는자_상호"], "승인번호를 못 받음"))
                    print(f"  {i+1}/{len(data)} 실패 {inv['공급받는자_상호']}")
                    break
            except securitycard.CardBlocked as e:
                failed.append((inv.get("공급받는자_상호"), str(e)[:120]))
                print("보안카드 인증이 거절돼 남은 건까지 모두 멈춥니다.")
                print(e)
                break
            except Exception as e:  # noqa
                failed.append((inv.get("공급받는자_상호"), str(e)[:120]))
                log(f"[{tag}] 오류: {e}")
                print(f"  {i+1}/{len(data)} 오류 {inv.get('공급받는자_상호')}: {str(e)[:100]}")
                break

    print(f"\n끝났습니다. 발급 {len(done)}건, 못 한 것 {len(data) - len(done)}건.")
    for name, got in done:
        print(f"  발급 {name} {got}")
    for name, why in failed:
        print(f"  실패 {name} {why}")


def cmd_fill(a):
    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    inv = data[a.index]
    tag = f"{a.index:02d}_{inv['공급받는자_상호'][:6]}"
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        log(f"[{tag}] 채우기 시작: {inv['공급받는자_상호']} 공급가액 {inv['공급가액']:,} 세액 {inv['세액']:,} 작성일 {inv['작성일자']}")
        fill_invoice(ctx, page, inv, tag)
        if a.preview:
            page.locator(F["btn_preview"]).click()
            page.wait_for_timeout(3000)
            shot(page, f"issue_{tag}_3_preview")
            log(f"[{tag}] 미리보기 캡처")
        if a.issue:
            log(f"[{tag}] 발급하기 클릭 (서명: {'보안카드' if a.card else '인증서'})")
            got = sign_and_wait(ctx, page, tag, use_card=a.card)
            if got:
                print("발급 완료. 승인번호", got)
            else:
                print("승인번호를 못 받았습니다. 화면을 직접 확인하세요.")
        else:
            log(f"[{tag}] 발급 안 함(--issue 없음). 화면은 채운 상태로 둠")


def cmd_reset(a):
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        ensure_issue_page(page)
        page.locator(F["btn_init"]).click()
        page.wait_for_timeout(1500)
        for label in ("확인", "예"):
            btn = page.locator(f"input[type=button][value='{label}']")
            vis = [i for i in range(btn.count()) if btn.nth(i).is_visible()]
            if vis:
                btn.nth(vis[-1]).click()
                break
        shot(page, "issue_reset")
        log("초기화 완료")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("fill"); s.add_argument("file"); s.add_argument("--index", type=int, default=0)
    s.add_argument("--preview", action="store_true"); s.add_argument("--issue", action="store_true")
    s.add_argument("--card", action="store_true", help="보안카드로 서명(없으면 인증서 창을 띄우고 사람이 입력)")
    s.add_argument("--wait-min", type=int, default=10); s.set_defaults(fn=cmd_fill)
    s = sp.add_parser("all", help="목록에 있는 건을 차례로 전부 발행")
    s.add_argument("file"); s.add_argument("--card", action="store_true")
    s.set_defaults(fn=cmd_all)
    s = sp.add_parser("reset"); s.set_defaults(fn=cmd_reset)
    a = ap.parse_args()
    a.fn(a)
