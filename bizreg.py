"""
style-guard:off  (코드 저장소 도구 — CLAUDE.md 12절 비적용 범위)

== 운영 맥락 ==
용도: 사업자등록증 사진이나 PDF 에서 상호·사업자번호·대표자를 읽는다. 거래처 이메일은 한 번 받아 두고 다음부터 다시 묻지 않는다.
실행 시점: telegram_bot.py 가 사진을 받을 때, 또는 사람이 `python bizreg.py 사진경로` 로 확인할 때.
입력: 사업자등록증 사진(jpg/png). 출력: 읽은 항목 dict. 거래처 장부는 %USERPROFILE%\\.hometax\\partners.json
외부 의존: 읽는 순서는 (1) 이 PC 에 설치된 claude 명령(PDF 도 읽는다) (2) 윈도우에 들어 있는 글자 인식(winocr, 사진만).
          둘 다 사진을 이 컴퓨터 안에서만 읽는다.
의도적 미구현: 사진을 바깥 인식 서비스로 올려 읽기. 거래처 서류가 밖으로 나가므로 금지.
              사업자번호 진위 확인. 홈택스 발급 화면의 [확인] 단추가 이미 걸러 준다.
마지막 점검: 2026-09-13
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from creds import CRED_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARTNERS = CRED_DIR / "partners.json"
BSNO_RE = re.compile(r"(\d{3})\s*-\s*(\d{2})\s*-\s*(\d{5})")

ASK = """{image} 파일을 Read 도구로 열어 읽어라. 사업자등록증 사진이다.
아래 JSON 한 덩어리만 출력해라. 설명, 인사, 코드 울타리를 붙이지 마라.
{{"business_no": "000-00-00000 형식의 등록번호", "company": "상호 또는 법인명", "owner": "대표자 성명"}}
읽을 수 없는 항목은 빈 문자열로 둬라."""


def _clean(got: dict) -> dict:
    out = {"business_no": "", "company": "", "owner": ""}
    for k in out:
        v = got.get(k)
        out[k] = str(v).strip() if v else ""
    m = BSNO_RE.search(out["business_no"])
    if m:
        out["business_no"] = "-".join(m.groups())
    else:
        digits = re.sub(r"\D", "", out["business_no"])
        out["business_no"] = f"{digits[:3]}-{digits[3:5]}-{digits[5:10]}" if len(digits) == 10 else ""
    return out


def _by_claude(image: Path) -> dict | None:
    """이 PC 에 설치된 claude 명령으로 사진을 읽는다. 지시문은 표준입력으로 넘긴다."""
    exe = shutil.which("claude")
    if not exe:
        return None
    cmd = [exe, "-p", "--output-format", "text", "--allowed-tools", "Read"]
    model = os.environ.get("HOMETAX_READER_MODEL", "sonnet")
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(
            cmd,
            input=ASK.format(image=image.name),
            cwd=str(image.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    body = (proc.stdout or "").strip()
    m = re.search(r"\{.*\}", body, re.S)
    if not m:
        return None
    try:
        got = _clean(json.loads(m.group(0)))
    except json.JSONDecodeError:
        return None
    return got if got["business_no"] or got["company"] else None


def _by_windows_ocr(image: Path) -> dict | None:
    """윈도우에 들어 있는 글자 인식. 표의 칸 순서가 흐트러져 나오므로 등록번호를 기준으로 잡는다."""
    try:
        from read_card import run_ocr
        lines = run_ocr(image)
    except Exception:
        return None

    out = {"business_no": "", "company": "", "owner": ""}
    hit = -1
    for i, line in enumerate(lines):
        m = BSNO_RE.search(line)
        if m:
            out["business_no"] = "-".join(m.groups())
            hit = i
            break
    if hit < 0:
        return None

    def looks_like_name(text: str) -> bool:
        t = text.strip()
        return bool(re.fullmatch(r"[가-힣]{2,5}", t)) and t not in ("법인명", "단체명", "상호", "성명", "대표자")

    for line in lines[hit + 1:hit + 6]:
        t = re.sub(r"\s+", " ", line).strip(" :：.,")
        if not out["company"] and len(t) >= 3 and not looks_like_name(t):
            out["company"] = t
        elif not out["owner"] and looks_like_name(t):
            out["owner"] = t
    return out


def read_bizreg(image: Path) -> dict:
    """사진에서 읽은 항목과 어느 방법으로 읽었는지를 돌려준다."""
    image = Path(image)
    if not image.exists():
        raise FileNotFoundError(f"사진을 찾지 못했습니다: {image}")
    got = _by_claude(image)
    if got:
        return dict(got, source="claude")
    got = _by_windows_ocr(image)
    if got:
        return dict(got, source="윈도우 글자 인식")
    raise RuntimeError(
        "사진에서 사업자등록증을 읽지 못했습니다.\n"
        "이 PC 에 claude 명령을 설치하거나, 윈도우라면 아래를 설치한 뒤 다시 보내십시오.\n"
        "  pip install winocr winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Globalization "
        "winrt-Windows.Storage.Streams winrt-Windows.Graphics.Imaging winrt-Windows.Foundation\n"
        "그래도 안 되면 상호와 사업자번호를 글자로 보내 주십시오."
    )


def load_partners() -> dict:
    if not PARTNERS.exists():
        return {}
    try:
        return json.loads(PARTNERS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def partner(bsno: str) -> dict:
    return load_partners().get(bsno, {})


def remember_partner(bsno: str, **fields) -> None:
    """거래처를 장부에 적어 둔다. 이메일을 한 번 받으면 다음부터 묻지 않는다."""
    if not bsno:
        return
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    book = load_partners()
    row = book.get(bsno, {})
    row.update({k: v for k, v in fields.items() if v})
    book[bsno] = row
    PARTNERS.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    if sys.platform == "win32":
        os.system(f'icacls "{PARTNERS}" /inheritance:r /grant:r "%USERNAME%":F >nul 2>&1')
    else:
        os.chmod(PARTNERS, 0o600)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("쓰는 법: python bizreg.py 사업자등록증사진.jpg")
        raise SystemExit(1)
    got = read_bizreg(Path(sys.argv[1]))
    print(f"읽은 방법: {got.pop('source')}")
    for key, label in (("company", "상호"), ("business_no", "등록번호"), ("owner", "대표자")):
        print(f"{label}: {got[key] or '못 읽음'}")
    saved = partner(got["business_no"]).get("email")
    print(f"장부에 적힌 이메일: {saved or '없음'}")
