"""
style-guard:off  (코드 저장소 도구 — CLAUDE.md 12절 비적용 범위)

== 운영 맥락 ==
용도: 홈택스 '사업용 신용카드 매입세액 공제 확인/변경' 화면에서 달마다 카드 사용 내역을 읽어 JSON 으로 낸다.
      (계산서·영수증·카드 › 신용카드 매입 › 사업용 신용카드 사용내역 › 매입세액 공제 확인/변경)
실행 시점: AI 오피스 경영 탭이 하루 한 번 또는 사람이 '지금 읽기'를 누를 때. 홈택스 로그인이 된 9260 크롬에 붙는다.
입력: --from YYYY-MM --to YYYY-MM [--out 결과.json]
출력: {"ok":true,"months":[{"ym","n","total"}],"rows":[{sn,d,prh,merchant,biz_type,biz_item,supply,vat,tip,total,ded,ded_class,card_co,card_last4}],"errors":[]}
      마지막 줄에 같은 JSON 을 한 줄로 찍는다(부르는 쪽이 마지막 '{' 줄을 읽음).
방식: 이 화면의 조회 요청에는 화면이 계산하는 서명이 붙어 요청을 직접 만들 수 없다. 그래서 화면에서 '월별'을 고르고 '조회'와
      페이지 번호를 눌러, 그때 오는 응답(ATECRCCA001R06)을 받아 읽는다. 한 페이지 20건.
외부 의존: Playwright, ht.py(크롬 연결·공지 닫기).
의도적 미구현: 공제 여부 바꾸기('변경하기' 단추), 내려받기 파일 생성 요청. 이 스크립트는 읽기만 한다.
              카드 번호 전체 저장(끝 4자리만 남긴다).
마지막 점검: 2026-10-19
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time

from playwright.sync_api import sync_playwright

import ht

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "https://hometax.go.kr/websquare/websquare.html?w2xPath=/ui/pp/index_pp.xml&tmIdx=46&tm2lIdx=4608020000&tm3lIdx=4608020100"
P = "mf_txppWframe_"
ACTION = "ATECRCCA001R06"
MENU = ["계산서·영수증·카드", "신용카드 매입", "매입세액 공제 확인/변경"]


def js_click(page, el_id: str) -> bool:
    return bool(page.evaluate("id => { const e = document.getElementById(id); if (!e) return false; e.click(); return true; }", el_id))


def has(page, el_id: str) -> bool:
    return bool(page.evaluate("id => !!document.getElementById(id)", el_id))


def open_screen(page) -> None:
    """주소로 바로 들어가고, 안 되면 메뉴를 차례로 누른다."""
    page.goto(URL, wait_until="domcontentloaded")
    for _ in range(20):
        page.wait_for_timeout(1000)
        ht.dismiss_notices(page)
        if has(page, P + "btnSearch") and has(page, P + "rdoSearch_input_1"):
            return
    for label in MENU:
        loc = page.get_by_text(label, exact=True)
        if loc.count():
            loc.first.click()
            page.wait_for_timeout(2500)
    for _ in range(15):
        page.wait_for_timeout(1000)
        if has(page, P + "btnSearch") and has(page, P + "rdoSearch_input_1"):
            return
    raise RuntimeError("사업용 신용카드 매입세액 공제 확인 화면을 열지 못했습니다(로그인이 풀렸거나 메뉴가 바뀌었을 수 있음)")


def wait_json(page, trigger, page_no: int | None = None) -> dict:
    """trigger() 를 부르고 그때 오는 조회 응답을 JSON 으로 받는다."""
    with page.expect_response(lambda r: ACTION in r.url and r.request.method == "POST", timeout=60000) as ri:
        trigger()
    resp = ri.value
    page.wait_for_timeout(800)
    return json.loads(resp.body().decode("utf-8", "replace"))


def row_of(x: dict) -> dict:
    num = lambda v: int(v) if isinstance(v, (int, float)) else int(re.sub(r"[^\d-]", "", str(v or "0")) or 0)
    card = re.sub(r"\D", "", str(x.get("busnCrdCardNoEncCntn") or ""))
    return {
        "sn": str(x.get("busnCrdcTrsBrkdSn") or "").strip(),
        "d": str(x.get("aprvDt") or "")[:10],
        "prh": str(x.get("prhDt") or "")[:10],
        "merchant": str(x.get("mrntTxprNm") or "").strip(),
        "biz_type": str(x.get("bcNm") or "").strip(),
        "biz_item": str(x.get("tfbNm") or "").strip(),
        "supply": num(x.get("splCft")),
        "vat": num(x.get("vaTxamt")),
        "tip": num(x.get("tip")),
        "total": num(x.get("totaTrsAmt")),
        "ded": str(x.get("ddcYnNm") or "").strip(),
        "ded_class": str(x.get("vatDdcClNm") or "").strip(),
        "card_co": str(x.get("crcmClNm") or "").strip(),
        "card_last4": card[-4:] if card else "",
        "cancel": str(x.get("trsAprvCnclYn") or "") == "Y",
    }


def read_month(page, y: int, m: int) -> tuple[list[dict], int]:
    label = "%d년 %02d월" % (y, m)
    js_click(page, P + "rdoSearch_input_1")   # 월별
    page.wait_for_timeout(1200)
    opts = page.evaluate("id => { const s = document.getElementById(id); return s ? [...s.options].map(o => o.text.trim()) : []; }", P + "selectbox6")
    if label not in opts:
        return [], -1   # 홈택스가 아직 그 달을 내주지 않음(보통 다음 달 15일경부터)
    page.select_option("#" + P + "selectbox6", label)
    page.wait_for_timeout(600)
    d = wait_json(page, lambda: js_click(page, P + "btnSearch"))
    rows = [row_of(x) for x in (d.get("busnCrdcTrsBrkdAdmDVOList") or [])]
    info = d.get("pageInfoVO") or {}
    total = int(info.get("totalCount") or 0)
    size = int(info.get("pageSize") or 20) or 20
    pages = max(1, math.ceil(total / size))
    for n in range(2, pages + 1):
        link = P + "pglNavi_page_%d" % n
        if not has(page, link):
            # 다음 묶음으로: '다음' 단추가 그 묶음의 첫 페이지를 불러온다
            d = wait_json(page, lambda: js_click(page, P + "pglNavi_nextPage_btn"))
            got = int((d.get("pageInfoVO") or {}).get("pageNum") or 0)
            if got != n and has(page, link):
                d = wait_json(page, lambda: js_click(page, link))
        else:
            d = wait_json(page, lambda: js_click(page, link))
        rows += [row_of(x) for x in (d.get("busnCrdcTrsBrkdAdmDVOList") or [])]
        time.sleep(0.4)   # 사람 속도
    return rows, total


def months_between(a: str, b: str) -> list[tuple[int, int]]:
    y1, m1 = map(int, a.split("-")); y2, m2 = map(int, b.split("-"))
    out = []
    while (y1, m1) <= (y2, m2):
        out.append((y1, m1)); m1 += 1
        if m1 > 12: y1, m1 = y1 + 1, 1
    return out[-24:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", dest="to", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    res = {"ok": False, "months": [], "rows": [], "errors": []}
    try:
        with sync_playwright() as pw:
            _, ctx, page = ht.connect(pw)
            page.on("dialog", ht.accept_quiet)
            open_screen(page)
            seen = set()
            for y, m in months_between(a.frm, a.to):
                try:
                    rows, total = read_month(page, y, m)
                except Exception as e:  # 한 달이 실패해도 나머지 달은 읽는다
                    res["errors"].append("%d-%02d: %s" % (y, m, str(e)[:160])); continue
                kept = 0
                for r in rows:
                    k = r["sn"] or (r["d"] + r["merchant"] + str(r["total"]))
                    if k in seen: continue
                    seen.add(k); res["rows"].append(r); kept += 1
                res["months"].append({"ym": "%d-%02d" % (y, m), "n": kept, "total": total})
            res["ok"] = True
    except Exception as e:
        res["errors"].append(str(e)[:300])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
    print(json.dumps({k: (v if k != "rows" else len(v)) for k, v in res.items()}, ensure_ascii=False) if a.out else json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
