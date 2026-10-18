"""
== 운영 맥락 ==
용도: 홈택스 "전자(세금)계산서 건별발급" 화면(menuCd=UTEETBAA01)에 발행 1건을 사람처럼 입력한다.
실행 시점: 수동. `python issue.py fill invoices.json --index 0` 로 채우기만(발급 안 함) →
          `python issue.py fill ... --issue` 로 발급하기 버튼까지. 서명(인증서/보안카드) 창은 사람이 처리.
입력: invoices.json (invoices.sample.json 형식). 크롬은 launch_chrome.ps1 로 띄운 9260 프로필, 로그인은 사람이.
출력: shots/issue_*.png (채운 화면·미리보기·결과), logs/issue.log
      텔레그램 발급 알림용 캡처: capture_preview(발급 직전 계산서 양식), capture_issued(발급 직후 승인번호 화면).
외부 의존: 홈택스 WebSquare 화면. 요소 id 는 2026-09-06 정찰값(`mf_txppWframe_*`). 화면 개편되면 dump 로 재정찰.
의도적 미구현: 발급하기 뒤 서명 창 자동 입력(보안카드 값 자동 입력은 사용자 결정 뒤), 일괄발급(엑셀) 경로.
              수정발급(전액 취소)은 cancel.py 가 맡고, 서명·캡처는 이 파일의 부품을 같이 쓴다.
발급 화면 진입: 아이디 로그인이면 "사용자인증 선택" 창이 먼저 뜬다(2026-09-18). pass_entry_auth 가 보안카드로 통과한다.
          로그인이 풀려 있으면 LoginLost 를 올리고, run_queue 가 다시 로그인해 한 번 더 채운다(발급 단추 전이라 안전).
마지막 점검: 2026-09-18
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

from ht import accept_quiet, connect, main_eval, shot, wake_window, SHOTS, BASE

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


ISSUED_SHOTS: dict[str, list[Path]] = {}  # tag → 발급 직후 승인번호 안내 화면. run_queue 가 꺼내 텔레그램 알림에 붙인다
LAST_NOTES: dict[str, str] = {}  # tag → 서명 뒤 홈택스가 띄운 오류·실패 안내. 승인번호를 못 받았을 때 사유로 알린다
DETAIL_SHOTS: dict[str, Path | None] = {}  # tag → 상세조회 화면(승인번호가 찍힌 계산서). 알림에 붙는 그림은 이쪽이 먼저다


PREVIEW_POPUP = "#mf_txppWframe_isnPreviewPopup"       # 발급미리보기 창(화면 안 레이어, 닫으면 DOM 에서 사라진다)
PREVIEW_CLOSE = PREVIEW_POPUP + "_wframe_trigger1"      # 닫기(X)
PREVIEW_CANCEL = PREVIEW_POPUP + "_wframe_btnCancelView"  # 취소. 바로 옆 발급하기(btnProcessView)는 누르지 않는다
# 미리보기·상세조회 창 안쪽은 680px 에서 잘려 스크롤된다. GROW_POPUP_JS 로 창을 내용 길이만큼 늘려 한 장에 담는다.


# 상세조회 창(전자세금계산서 상세조회, 승인번호가 찍힌 계산서). 2026-09-28 실측: 목록에서 줄을 두 번 누르면
# #mf_txppWframe_UTEETBDA38 레이어로 뜬다. 승인번호는 글자가 아니라 입력칸 값이라 innerText 에 안 잡힌다.
# 이 창의 수정발급(_wframe_trigger30)·복사발급(_wframe_trigger32)은 누르지 않는다. 닫기는 _wframe_trigger1.
LIST_MENU_TEXT = "전자(세금)계산서 조회 > 조회 > 발급 목록조회"
LIST_DATE_KIND = P + "selectbox11"   # 작성일자 / 발급일자 / 전송일자
LIST_SORT_KIND = P + "srtClCd"
LIST_SEARCH = P + "trigger50"        # 조회
LIST_GRID = P + "resultGrid_body_table"
GROW_POPUP_JS = """
((id) => {
  const pop = document.getElementById(id);
  if (!pop) return -1;
  const sc = [...pop.querySelectorAll('*')].find(e => e.scrollHeight > e.clientHeight + 5 && e.clientHeight > 100
    && /auto|scroll/.test(getComputedStyle(e).overflowY));
  if (!sc) return 0;
  const extra = sc.scrollHeight - sc.clientHeight;
  for (let e = sc; e && e !== pop.parentElement; e = e.parentElement) {
    e.style.height = (e.offsetHeight + extra) + 'px'; e.style.maxHeight = 'none';
  }
  // 찍는 칸 가장자리에 창 테두리(점선)와 뒤 화면이 비쳐 찍히지 않게 한다. 창은 닫으면 사라져 남지 않는다
  sc.style.background = '#fff';
  pop.style.outline = 'none'; pop.style.borderColor = 'transparent'; pop.style.boxShadow = 'none';
  return extra;
})(%s)
"""
FIND_DETAIL_JS = """
(approval) => {
  for (const e of document.querySelectorAll('.w2popup_window')) {
    if (!e.id || e.offsetWidth === 0) continue;
    const t = (e.innerText || '') + ' ' + [...e.querySelectorAll('input')].map(i => i.value || '').join(' ');
    if (t.slice(0, 80).includes('상세조회') && t.includes(approval)) return e.id;
  }
  return null;
}
"""


def find_detail_popup(page, approval, wait_sec=10):
    """그 승인번호가 찍힌 상세조회 창의 id 를 돌려준다. 없으면 None.

    창을 순번(nth)으로 붙잡지 않는다. 상세조회 창이 뜨면서 발급 완료 안내창이 닫혀 순번이 밀리고,
    붙잡아 둔 순번이 빈 자리를 가리켜 30초 기다리다 실패했다(2026-09-28 12:07 실제 발급에서 발생).
    """
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        try:
            pid = page.evaluate(FIND_DETAIL_JS, approval)
        except Exception:  # noqa
            pid = None
        if pid:
            return pid
        page.wait_for_timeout(1000)
    return None


def open_detail_from_done(page, approval):
    """발급 완료 안내창의 '상세(팝업)' 단추로 상세조회 창을 연다. 안 되면 None(목록조회 길로 넘어간다)."""
    try:
        btn = page.locator(".w2popup_window input[type=button][value='상세(팝업)'], "
                           ".w2popup_window button:has-text('상세(팝업)'), .w2popup_window a:has-text('상세(팝업)')")
        vis = [i for i in range(btn.count()) if btn.nth(i).is_visible()]
        if not vis:
            return None
        btn.nth(vis[-1]).click(timeout=5000)
        return find_detail_popup(page, approval, wait_sec=10)
    except Exception as ex:  # noqa
        log(f"[상세조회] 완료 안내창의 상세(팝업) 단추로는 못 열었다: {str(ex).split('Call log')[0].strip()[:120]}")
        return None


def open_detail_from_list(ctx, page, approval):
    """발급 목록조회에서 오늘 발급분을 조회해 그 승인번호 줄을 두 번 눌러 상세조회 창을 연다(2026-09-28 실측)."""
    import securitycard

    page.on("dialog", accept_quiet)  # 잠겨 있으면 "인증서 또는 보안카드로 로그인" 안내창이 먼저 뜬다
    page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    box = page.get_by_placeholder("검색어를 입력하세요!").first
    box.click()
    page.keyboard.type("발급목록조회", delay=60)
    page.wait_for_timeout(1500)
    page.keyboard.press("Enter")
    page.wait_for_timeout(7000)
    page.get_by_text(LIST_MENU_TEXT, exact=False).first.click()
    page.wait_for_timeout(9000)
    if (securitycard.find_window(ctx, securitycard.AUTH_WINDOW_TITLE, timeout_sec=3) is not None
            or securitycard.find_window(ctx, securitycard.CARD_WINDOW_TITLE, timeout_sec=1) is not None):
        log("[상세조회] 목록조회 화면이 잠겨 있어 보안카드로 연다")
        if not securitycard.choose_card_auth(ctx):
            raise RuntimeError("목록조회 화면의 인증선택 창에서 보안카드 인증으로 넘어가지 못했다.")
        securitycard.authenticate(ctx, "detail")
        page.bring_to_front()
        page.wait_for_timeout(6000)
    page.locator(LIST_DATE_KIND).select_option("발급일자")  # 작성일자는 지난달일 수 있다. 발급한 날은 오늘이다
    page.locator(LIST_SORT_KIND).select_option("발급일자")
    page.wait_for_timeout(500)
    page.locator(LIST_SEARCH).click()
    page.wait_for_timeout(6000)
    cell = page.locator(f"{LIST_GRID} nobr", has_text=approval)
    if cell.count() == 0:
        log(f"[상세조회] 목록에 승인번호 {approval} 줄이 없다")
        return None
    cell.first.scroll_into_view_if_needed()
    cell.first.dblclick()
    return find_detail_popup(page, approval, wait_sec=15)


def trim_left(path, px=12):
    """상세조회 캡처 왼쪽 끝 8px 쯤에 뒤 화면이 비쳐 찍힌다(2026-09-28 실측). 그만큼 잘라낸다. 못 잘라도 그림은 그대로 쓴다."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.width > px * 10:
                cut = im.crop((px, 0, im.width, im.height))
                cut.load()
        cut.save(path)
    except Exception as ex:  # noqa
        log(f"[캡처] 가장자리 자르기 건너뜀: {str(ex)[:100]}")


def capture_detail(ctx, page, tag, approval):
    """발급된 계산서의 상세조회 화면(승인번호·공급자·공급받는자·금액·품목)을 한 장으로 찍는다. 텔레그램 알림용.

    발급은 이미 끝난 뒤라 여기서 난 오류는 밖으로 올리지 않는다(올리면 발급된 건이 실패로 알려져 다시 발행된다).
    끝나면 홈택스 첫 화면으로 돌아가 떠 있던 창을 모두 없앤다. 다음 건은 메뉴로 발급 화면에 다시 들어간다.
    """
    path = None
    wake_window(page)  # 사람이 창을 내려 두면 캡처가 멈춘다
    ways = (("완료 안내창의 상세(팝업) 단추", lambda: open_detail_from_done(page, approval)),
            ("발급 목록조회", lambda: open_detail_from_list(ctx, page, approval)))
    for name, opener in ways:  # 앞 길이 열지 못하거나 찍다가 실패하면 다음 길로 넘어간다
        try:
            pid = opener()
            if not pid:
                log(f"[{tag}] 상세조회 창을 {name} 길로는 열지 못했다")
                continue
            log(f"[{tag}] 상세조회 창을 {name} 길로 열었다 ({pid})")
            try:
                main_eval(ctx, page, GROW_POPUP_JS % json.dumps(pid))
                page.wait_for_timeout(600)
            except Exception as ex:  # noqa
                log(f"[{tag}] 상세조회 창 늘리기 실패(보이는 부분만 찍는다): {str(ex)[:120]}")
            pop = page.locator(f"[id='{pid}']")
            # 양식 칸(.wrap_contarea)만 찍으면 오른쪽 끝이 잘린다(2026-09-28 실측). 여백까지 든 바깥 칸을 찍는다
            form = pop.locator(".pop_cont")
            target = form.first if form.count() else pop
            p = SHOTS / f"issue_{tag}_8_detail.png"
            target.screenshot(path=str(p), timeout=15000)
            print("shot", p)
            trim_left(p)
            path = p
            break
        except Exception as ex:  # noqa
            log(f"[{tag}] 상세조회 화면 캡처 실패({name} 길, 발급은 끝남): {str(ex).split('Call log')[0].strip()[:200]}")
    try:
        page.goto("https://hometax.go.kr", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
    except Exception:  # noqa
        pass
    return path


def _preview_open(page, popup=PREVIEW_POPUP) -> bool:
    pop = page.locator(popup)
    return pop.count() > 0 and pop.first.is_visible()


def capture_preview(ctx, page, tag, popup=PREVIEW_POPUP, closers=(PREVIEW_CLOSE, PREVIEW_CANCEL)):
    """발급하기를 누르기 직전에 발급미리보기 창을 열어 세금계산서 양식(공급자·공급받는자·금액·품목)을 찍고 닫는다.

    텔레그램 발급 알림에 붙일 그림이다(2026-09-28 실측: 닫기·취소 모두 입력값을 그대로 두고 창만 닫는다).
    찍지 못해도 발급은 그대로 진행한다. 단 창이 닫히지 않으면 발급 단추를 가리므로 오류를 올려 발급을 시작하지 않는다.
    수정발급 화면은 미리보기 창과 닫기 단추 이름이 달라 cancel.py 가 popup·closers 를 넘긴다(2026-09-29 실측).
    """
    path = None
    wake_window(page)  # 사람이 창을 내려 두면 캡처가 멈춘다
    try:
        page.locator(F["btn_preview"]).click(timeout=10000)
        page.locator(popup).wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(1500)
        try:
            main_eval(ctx, page, GROW_POPUP_JS % json.dumps(popup.lstrip("#")))
            page.wait_for_timeout(600)
        except Exception as ex:  # noqa
            log(f"[{tag}] 미리보기 창 늘리기 실패(보이는 부분만 찍는다): {str(ex)[:120]}")
        form = page.locator(popup + " .wrap_contarea")
        target = form.first if form.count() else page.locator(popup)
        p = SHOTS / f"issue_{tag}_3_preview.png"
        target.screenshot(path=str(p), timeout=15000)
        print("shot", p)
        path = p
    except Exception as ex:  # noqa
        log(f"[{tag}] 미리보기 양식 캡처 실패(발급은 계속한다): {str(ex).split('Call log')[0].strip()[:160]}")
    for sel in closers:
        if not _preview_open(page, popup):
            break
        try:
            page.locator(sel).click(timeout=5000)
            page.wait_for_timeout(1500)
        except Exception:  # noqa
            pass
    if _preview_open(page, popup):
        shot(page, f"issue_{tag}_preview_stuck")
        raise RuntimeError("발급미리보기 창이 닫히지 않아 발급을 시작하지 않았습니다.")
    return path


def capture_issued(page, tag):
    """승인번호 안내창이 떠 있는 발급 화면을 찍는다. 발급은 이미 끝난 뒤라 여기서 난 오류는 밖으로 올리지 않는다
    (올리면 발급된 건이 실패로 알려져 다시 발행하게 된다).
    화면 전체 찍기는 쓰지 않는다. 홈택스 머리말이 화면 가운데에 겹쳐 찍힌다(2026-09-28 실측)."""
    path = SHOTS / f"issue_{tag}_7_done.png"
    wake_window(page)
    try:
        page.screenshot(path=str(path), full_page=False, timeout=15000)
        print("shot", path)
        return [path]
    except Exception as ex:  # noqa
        log(f"[{tag}] 발급 화면 캡처 실패: {str(ex)[:120]}")
    return []


def wait_result(ctx, page, tag, minutes=10):
    """사람이 인증서 비밀번호를 치는 동안 기다렸다가 승인번호 안내창을 읽는다."""
    import re
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        note = read_layer_messages(ctx, page)
        m = re.search(r"승인번호\s*:\s*([0-9\-]+)", note)
        if m:
            log(f"[{tag}] 발급 완료. 승인번호 {m.group(1)} / 안내: {note[:160]}")
            ISSUED_SHOTS[tag] = capture_issued(page, tag)
            DETAIL_SHOTS[tag] = capture_detail(ctx, page, tag, m.group(1))
            # 발급은 이미 끝났다. 안내창 닫기가 실패해도 승인번호는 돌려줘야 한다(오류를 올리면 발급된 건이
            # 실패로 알려져 다시 발행된다).
            try:
                for sel in ("input[type=button][value='확인']", "input[type=button][value='닫기']", "button:has-text('확인')", "button:has-text('닫기')"):
                    b = page.locator(".w2popup_window " + sel)
                    vis = [i for i in range(b.count()) if b.nth(i).is_visible()]
                    if vis:
                        b.nth(vis[-1]).click(timeout=5000)
                        break
            except Exception as ex:  # noqa
                log(f"[{tag}] 발급 완료 안내창 닫기 실패(발급은 끝남): {str(ex).split('Call log')[0].strip()[:120]}")
            return m.group(1)
        if "오류" in note or "실패" in note:
            log(f"[{tag}] 발급 중 안내: {note[:200]}")
            LAST_NOTES[tag] = note[:200]
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


class LoginLost(RuntimeError):
    """발급 화면에 들어가 보니 로그인이 풀려 있었다. 발급 전이라 다시 로그인하고 재시도해도 안전하다."""


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
    pass_entry_auth(page)  # 보안카드 잠김·화면 불명은 그대로 올려 보내 남은 건까지 멈추게 한다
    try:
        page.wait_for_selector(F["buyer_bsno"], timeout=40000)
    except Exception:
        body = page.evaluate("document.body.innerText") or ""
        note = read_layer_messages(page.context, page)
        # 2026-09-18 15:06: 끊긴 세션이면 "로그인 정보가 없습니다" 안내가 뜬다. 예전엔 SystemExit 를 던져
        # run_queue 의 except Exception 을 빠져나가 건이 processing 에 갇힐 수 있었다 → 일반 오류로 올린다.
        if "로그인 정보가 없습니다" in note or "로그아웃" not in body:
            raise LoginLost("홈택스 로그인이 풀려 있었습니다(30분 무동작 등).")
        # 2026-09-18: 로그인 직후 발급 화면에 들어가자 본문이 회색으로 덮이고 별도 창(UTECMABA14)이 떠 있었다.
        # connect() 는 별도 창을 일부러 안 잡으므로 여기서 내용을 남긴다. 모르는 창은 누르지 않는다.
        pops = describe_other_windows(page)
        log(f"[발급화면] 입력칸이 안 뜸. 화면 안내: {read_layer_messages(page.context, page)[:300] or '없음'} / 별도 창: {pops or '없음'}")
        shot(page, "issue_page_blocked")
        raise RuntimeError(f"발급 화면이 열리지 않았습니다. 홈택스가 띄운 창: {pops[:300] or '없음'}")


def pass_entry_auth(page, wait_sec=30):
    """아이디 로그인으로 건별발급 화면에 들어가면 '사용자인증 선택'(UTECMABA14) 창이 먼저 뜬다(2026-09-18 실측).
    보안카드가 저장돼 있으면 발급 때와 같은 부품으로 통과하고, 없으면 사람이 인증하도록 알린다.
    입력칸이 먼저 보이면(이미 인증한 세션) 아무것도 안 한다."""
    import securitycard
    import creds

    ctx = page.context
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        box = page.locator(F["buyer_bsno"])
        if box.count() and box.first.is_visible():
            return
        auth_win = securitycard.find_window(ctx, securitycard.AUTH_WINDOW_TITLE, timeout_sec=1)
        card_win = securitycard.find_window(ctx, securitycard.CARD_WINDOW_TITLE, timeout_sec=1)
        if auth_win is None and card_win is None:
            time.sleep(1)
            continue
        try:
            creds.card_serial()
            creds.card_number(1)
        except creds.MissingCredentials:
            log("[발급화면] 추가 본인인증 창이 떴는데 보안카드가 저장돼 있지 않다. 크롬 창에서 사람이 인증해야 한다")
            print("건별발급 화면이 추가 본인인증을 요구합니다. 크롬 창에서 인증하세요(10분 기다립니다).")
            page.wait_for_selector(F["buyer_bsno"], timeout=10 * 60 * 1000)
            return
        log("[발급화면] 추가 본인인증 창이 떠서 보안카드로 통과한다")
        if not securitycard.choose_card_auth(ctx):
            raise securitycard.CardScreenUnknown("발급 화면 진입 때 인증선택 창에서 보안카드 인증으로 넘어가지 못했다.")
        securitycard.authenticate(ctx, "entry")
        page.bring_to_front()
        return


def describe_other_windows(page):
    """본 화면 말고 떠 있는 창(홈택스 팝업)의 제목·글·단추를 적고 캡처한다. 누르지는 않는다."""
    out = []
    for i, p in enumerate(page.context.pages):
        if p == page:
            continue
        try:
            text = (p.evaluate("document.body.innerText") or "").strip().replace("\n", " ")[:250]
            btns = p.evaluate("[...document.querySelectorAll('input[type=button],button')].filter(e=>e.offsetWidth>0)"
                              ".map(e=>(e.value||e.innerText||'').trim()).filter(Boolean).slice(0,10)")
            p.screenshot(path=str(SHOTS / f"issue_page_blocked_win{i}.png"))
            out.append(f"[{p.title()} | {p.url.split('?')[-1][:80]} | 글: {text} | 단추: {btns}]")
        except Exception as ex:  # noqa
            out.append(f"[창{i} 읽기 실패: {ex}]")
    return " ".join(out)


def split_email(email):
    if not email or "@" not in email:
        return "", ""
    a, b = email.split("@", 1)
    return a, b


def fill_invoice(pw_ctx, page, inv, tag):
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), accept_quiet(d)))

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


def _write_result(path, result):
    """다른 프로그램(AI 오피스)이 읽는 결과 파일. 승인번호·상태·캡처 경로만 담고 자격은 절대 안 담는다."""
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as ex:  # noqa
        log(f"결과 파일 쓰기 실패 {path}: {str(ex)[:120]}")


def _wait_confirm(path, minutes, tag):
    """미리보기를 찍은 뒤 사람이 앱에서 '발급'을 누를 때까지 기다린다. 파일 내용이 go 면 발급, stop 이면 발급 안 함, 시간이 지나면 발급 안 함."""
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        p = Path(path)
        if p.exists():
            v = p.read_text(encoding="utf-8", errors="replace").strip().lower()
            try:
                p.unlink()
            except Exception:  # noqa
                pass
            if v == "go":
                log(f"[{tag}] 발급 확인 받음")
                return "go"
            log(f"[{tag}] 발급 중단 신호 받음({v or '빈 파일'})")
            return "stop"
        time.sleep(2)
    log(f"[{tag}] {minutes}분 안에 발급 확인이 없어 발급하지 않음")
    return "timeout"


def cmd_fill(a):
    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    inv = data[a.index]
    tag = f"{a.index:02d}_{inv['공급받는자_상호'][:6]}"
    result = {"tag": tag, "status": "start", "approval": "", "shots": [], "error": ""}
    def shots_now():
        return sorted(str(p) for p in SHOTS.glob(f"issue_{tag}_*.png"))
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        log(f"[{tag}] 채우기 시작: {inv['공급받는자_상호']} 공급가액 {inv['공급가액']:,} 세액 {inv['세액']:,} 작성일 {inv['작성일자']}")
        try:
            fill_invoice(ctx, page, inv, tag)
            if a.preview or a.confirm_file:
                p = capture_preview(ctx, page, tag)   # 양식 창을 찍고 닫는다(닫히지 않으면 오류를 올려 발급 안 함)
                result["preview"] = str(p) if p else ""
                result["shots"] = shots_now()
                log(f"[{tag}] 미리보기 캡처")
            if a.confirm_file:
                result["status"] = "preview"
                _write_result(a.result, result)
                v = _wait_confirm(a.confirm_file, a.wait_min, tag)
                if v != "go":
                    result["status"] = "stopped" if v == "stop" else "timeout"
                    result["shots"] = shots_now()
                    _write_result(a.result, result)
                    print("발급하지 않았습니다(" + ("중단" if v == "stop" else "확인 시간 초과") + "). 화면은 채운 상태로 둡니다.")
                    return
                a.issue = True
            if a.issue:
                log(f"[{tag}] 발급하기 클릭 (서명: {'보안카드' if a.card else '인증서'})")
                result["status"] = "signing"
                _write_result(a.result, result)
                got = sign_and_wait(ctx, page, tag, use_card=a.card)
                result["approval"] = got or ""
                result["status"] = "done" if got else "check"
                result["shots"] = shots_now()
                _write_result(a.result, result)
                if got:
                    print("발급 완료. 승인번호", got)
                else:
                    print("승인번호를 못 받았습니다. 화면을 직접 확인하세요.")
            else:
                result["status"] = "filled"
                result["shots"] = shots_now()
                _write_result(a.result, result)
                log(f"[{tag}] 발급 안 함(--issue 없음). 화면은 채운 상태로 둠")
        except Exception as ex:  # noqa
            # 발급 단추를 누른 뒤의 오류는 check(사람이 화면 확인), 그 전은 error(다시 접수 가능)
            result["status"] = "check" if result["status"] == "signing" else "error"
            result["error"] = str(ex).split("Call log")[0].strip()[:200]
            result["shots"] = shots_now()
            _write_result(a.result, result)
            raise


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
    s.add_argument("--wait-min", type=int, default=10)
    s.add_argument("--result", default="", help="승인번호·상태·캡처 경로를 적을 JSON 파일(AI 오피스가 읽음)")
    s.add_argument("--confirm-file", default="", help="미리보기를 찍은 뒤 이 파일이 생길 때까지 기다렸다가(내용 go) 발급. stop 이면 발급 안 함")
    s.set_defaults(fn=cmd_fill)
    s = sp.add_parser("reset"); s.set_defaults(fn=cmd_reset)
    a = ap.parse_args()
    a.fn(a)
