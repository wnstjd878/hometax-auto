"""
style-guard:off  (코드 저장소 도구 — CLAUDE.md 12절 비적용 범위)

== 운영 맥락 ==
용도: 폰에서 텔레그램으로 발행 목록을 넣고 발행을 시키고 승인번호를 받는다. 이 PC 한 대만 있으면 된다.
실행 시점: 사람이 `python telegram_bot.py` 로 띄워 둔다. 창을 닫으면 멈춘다.
입력: 텔레그램 메시지. 발행 자료는 invoices.json.
출력: 텔레그램 답장, logs/issue.log, shots/
외부 의존: python-telegram-bot, 홈택스, 크롬 9260. 자격은 creds.py.
의도적 미구현: 사업자등록증 사진 읽기(글자 인식 모델이 따로 필요해서 뺐다).
              여러 사람이 쓰는 기능(정해둔 대화방 하나만 받는다).
마지막 점검: 2026-09-11
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

import creds

BASE = Path(__file__).resolve().parent
INVOICES = BASE / "invoices.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [봇] %(message)s")
# 통신 라이브러리가 주소에 든 봇 열쇠를 기록에 남기므로 반드시 낮춘다
for _n in ("httpx", "httpcore", "telegram", "apscheduler"):
    logging.getLogger(_n).setLevel(logging.WARNING)
log = logging.getLogger("봇")

HELP = """세금계산서 봇입니다.

목록          지금 발행할 건을 보여줍니다
추가 상호,사업자번호,대표자,이메일,금액,품목
              발행할 건을 하나 넣습니다
비우기        목록을 비웁니다
발행          목록에 있는 건을 전부 발행합니다
취소 승인번호  그 계산서를 전액 취소합니다

금액은 150만 이나 1500000 처럼 쓰면 됩니다. 부가세 포함 금액이면 뒤에 포함을 붙이세요."""


def load_invoices() -> list[dict]:
    if not INVOICES.exists():
        return []
    try:
        return json.loads(INVOICES.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_invoices(rows: list[dict]) -> None:
    INVOICES.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_amount(text: str) -> tuple[int, int] | None:
    """'150만', '1,500,000', '165만 포함' 을 공급가액과 세액으로 나눈다."""
    import re

    incl = "포함" in text
    m = re.search(r"(\d[\d,\.]*)\s*(억|천만|백만|만|천)?", text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    unit = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000,
            "만": 10_000, "천": 1_000, None: 1}[m.group(2)]
    amount = int(round(num * unit))
    if amount <= 0:
        return None
    if incl:
        supply = round(amount / 1.1)
        return supply, amount - supply
    return amount, round(amount * 0.1)


def add_row(body: str) -> str:
    parts = [p.strip() for p in body.split(",")]
    if len(parts) < 5:
        return "쉼표로 나눠 다섯 개는 넣어주세요.\n예) 추가 예시상사,000-00-00000,홍길동,a@b.com,150만"
    name, bsno, owner, email, money = parts[:5]
    item = parts[5] if len(parts) > 5 else "용역대금"
    got = parse_amount(money)
    if not got:
        return f"금액을 못 읽었습니다: {money}"
    supply, tax = got
    rows = load_invoices()
    rows.append({
        "작성일자": date.today().isoformat(),
        "공급받는자_사업자번호": bsno,
        "공급받는자_상호": name,
        "공급받는자_대표자": owner,
        "공급받는자_이메일": email,
        "품목": item,
        "공급가액": supply,
        "세액": tax,
        "청구영수": "청구",
        "비고": "",
    })
    save_invoices(rows)
    return (f"넣었습니다. 지금 {len(rows)}건입니다.\n"
            f"{name} 공급가액 {supply:,}원 세액 {tax:,}원 합계 {supply + tax:,}원")


def list_rows() -> str:
    rows = load_invoices()
    if not rows:
        return "발행할 건이 없습니다. 추가 명령으로 넣어주세요."
    lines = [f"{i + 1}. {r['공급받는자_상호']} {int(r['공급가액']):,}원 (세액 {int(r['세액']):,}원)"
             for i, r in enumerate(rows)]
    total = sum(int(r["공급가액"]) + int(r["세액"]) for r in rows)
    return f"발행 대기 {len(rows)}건, 합계 {total:,}원\n" + "\n".join(lines)


def run_issue_all() -> str:
    """홈택스에서 실제로 발행한다. 오래 걸리므로 다른 흐름에서 부른다."""
    import securitycard
    from playwright.sync_api import sync_playwright

    from ht import connect
    from issue import fill_invoice, reset_form, sign_and_wait
    from login import ensure_login, is_logged_in

    rows = load_invoices()
    if not rows:
        return "발행할 건이 없습니다."
    try:
        securitycard.guard_ok()
    except securitycard.CardBlocked as e:
        return f"보안카드가 잠겨 있어 시작하지 않습니다.\n{e}"

    done, left = [], list(rows)
    with sync_playwright() as pw:
        _, ctx, page = connect(pw)
        if not is_logged_in(page):
            ensure_login(page)
        for row in list(rows):
            tag = str(row.get("공급받는자_상호", ""))[:6]
            try:
                reset_form(page)
                fill_invoice(ctx, page, row, tag)
                got = sign_and_wait(ctx, page, tag, use_card=True)
                if not got:
                    return (f"{len(done)}건 발행하고 멈췄습니다.\n"
                            f"{row['공급받는자_상호']} 에서 승인번호를 못 받았습니다. 화면을 확인하세요.")
                done.append((row["공급받는자_상호"], got))
                left.remove(row)
                save_invoices(left)
            except Exception as e:  # noqa
                save_invoices(left)
                head = "\n".join(f"{n} {g}" for n, g in done)
                return (f"{len(done)}건 발행하고 멈췄습니다.\n{head}\n\n"
                        f"막힌 곳: {row.get('공급받는자_상호')} - {str(e)[:150]}")
    body = "\n".join(f"{n} 승인번호 {g}" for n, g in done)
    return f"{len(done)}건 발행을 마쳤습니다.\n{body}"


def run_cancel(approval_no: str) -> str:
    from cancel import cancel
    got = cancel(approval_no)
    if got:
        return f"취소했습니다. 취소 승인번호 {got}"
    return "승인번호를 못 받았습니다. 화면을 확인하세요."


def main() -> None:
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters

    token, chat_id = creds.telegram()

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg or not msg.text or msg.chat_id != chat_id:
            return
        text = msg.text.strip()
        head = text.split()[0] if text.split() else ""
        log.info("받음: %s", text[:40])

        if head in ("목록", "리스트"):
            await msg.reply_text(list_rows())
        elif head == "추가":
            await msg.reply_text(add_row(text[len("추가"):].strip()))
        elif head in ("비우기", "초기화"):
            save_invoices([])
            await msg.reply_text("목록을 비웠습니다.")
        elif head in ("발행", "세발", "발행해", "발행해줘"):
            rows = load_invoices()
            if not rows:
                await msg.reply_text("발행할 건이 없습니다.")
                return
            await msg.reply_text(f"{len(rows)}건 발행을 시작합니다. 한두 분 걸립니다.")
            got = await asyncio.to_thread(run_issue_all)
            await msg.reply_text(got)
        elif head == "취소":
            rest = text[len("취소"):].strip()
            if not rest:
                await msg.reply_text("취소할 승인번호를 같이 보내주세요.\n예) 취소 20260101-10260101-12345678")
                return
            await msg.reply_text(f"{rest} 건을 취소합니다.")
            got = await asyncio.to_thread(run_cancel, rest)
            await msg.reply_text(got)
        else:
            await msg.reply_text(HELP)

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("봇을 띄웠습니다. 텔레그램에서 '목록' 이라고 보내보세요. 창을 닫으면 멈춥니다.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except creds.MissingCredentials as e:
        print(e)
        print("먼저 `python setup_creds.py telegram` 을 실행해 봇 열쇠와 대화방 번호를 넣으세요.")
        sys.exit(2)
