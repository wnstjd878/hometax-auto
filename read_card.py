"""
style-guard:off  (코드 저장소 도구 — CLAUDE.md 12절 비적용 범위)

== 운영 맥락 ==
용도: 보안카드 사진을 이 PC 안에서만 읽어 번호별 4자리를 자격 파일에 넣는다. 사진도 숫자도 밖으로 안 나간다.
실행 시점: 사람이 자기 명령 프롬프트에서 `python read_card.py 사진경로`. 화면에 뜬 결과를 눈으로 대조한 뒤 저장한다.
입력: 보안카드 사진(jpg/png). 출력: %USERPROFILE%\\.hometax\\credentials.json
외부 의존: 윈도우에 들어 있는 글자 인식(winocr + winrt 꾸러미, 인터넷 안 씀).
          PowerShell 로 부르는 방식은 이 윈도우에서 System.Runtime.WindowsRuntime 이 없어 실패해 버렸다.
의도적 미구현: 사진을 바깥 인식 서비스로 보내기. 자격이 유출되므로 금지.
              눈으로 확인하지 않고 바로 저장하기. 한 글자만 틀려도 카드가 정지될 수 있다.
마지막 점검: 2026-09-11
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from creds import CRED_DIR, CRED_FILE

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent


def run_ocr(image: Path) -> list[str]:
    """윈도우에 들어 있는 글자 인식으로 사진을 읽는다. 인터넷을 쓰지 않는다."""
    if sys.platform != "win32":
        raise RuntimeError(
            "사진으로 읽는 기능은 윈도우에 들어 있는 글자 인식을 씁니다.\n"
            "맥이나 리눅스에서는 `python setup_creds.py card` 로 직접 넣어 주십시오."
        )
    try:
        import winocr
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(
            f"글자 인식 꾸러미가 없습니다({e}). 이렇게 설치하세요:\n"
            "  pip install winocr winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Globalization "
            "winrt-Windows.Storage.Streams winrt-Windows.Graphics.Imaging winrt-Windows.Foundation"
        )
    img = Image.open(image)
    if img.mode != "RGB":
        img = img.convert("RGB")
    best = []
    for lang in ("ko", "en-US", None):
        try:
            result = winocr.recognize_pil_sync(img, lang) if lang else winocr.recognize_pil_sync(img)
        except Exception:
            continue
        raw = result.get("lines") if isinstance(result, dict) else getattr(result, "lines", [])
        lines = []
        for ln in raw or []:
            text = ln.get("text") if isinstance(ln, dict) else getattr(ln, "text", "")
            if text and text.strip():
                lines.append(text.strip())
        if len(" ".join(lines)) > len(" ".join(best)):
            best = lines
    if not best:
        raise RuntimeError("사진에서 글자를 읽지 못했습니다.")
    return best


def parse_pairs(lines: list[str]) -> dict[str, str]:
    """줄에서 '번호 다음에 4자리' 짝을 찾는다. 01 같은 앞자리 0도 받는다."""
    card: dict[str, str] = {}
    tokens: list[str] = []
    for line in lines:
        tokens += re.findall(r"\d+", line.replace("O", "0").replace("o", "0").replace("l", "1"))
    i = 0
    while i < len(tokens) - 1:
        pos, val = tokens[i], tokens[i + 1]
        if len(pos) <= 2 and pos.isdigit() and 1 <= int(pos) <= 50 and len(val) == 4:
            key = str(int(pos))
            if key not in card:
                card[key] = val
                i += 2
                continue
        i += 1
    return card


def show(card: dict[str, str]) -> None:
    print("\n읽은 내용입니다. 실물 카드와 한 줄씩 대조하세요.\n")
    for pos in sorted(card, key=lambda x: int(x)):
        print(f"  {int(pos):>2}번  {card[pos]}")
    missing = [str(n) for n in range(1, 31) if str(n) not in card]
    if missing:
        print("\n못 읽은 번호:", ", ".join(missing))


def fix_loop(card: dict[str, str]) -> dict[str, str]:
    print("\n틀리거나 빠진 곳을 고칩니다. '번호 숫자' 로 넣으세요. 예) 7 1234")
    print("다 맞으면 빈 줄에서 Enter 를 누르세요.")
    while True:
        try:
            line = input("고칠 내용: ").strip()
        except EOFError:
            break
        if not line:
            break
        parts = line.replace(":", " ").replace(",", " ").split()
        if len(parts) < 2:
            print("  번호와 숫자를 함께 넣으세요.")
            continue
        for i in range(0, len(parts) - 1, 2):
            pos, val = parts[i], parts[i + 1]
            if not pos.isdigit() or not val.isdigit():
                print(f"  건너뜀(숫자가 아님): {pos} {val}")
                continue
            if not 1 <= int(pos) <= 50:
                print(f"  건너뜀(카드에 없는 번호): {pos}")
                continue
            if len(val) != 4:
                print(f"  건너뜀({pos}번을 {len(val)}자리로 넣으셨는데 카드는 4자리입니다)")
                continue
            card[str(int(pos))] = val
            print(f"  {int(pos)}번 고쳤습니다.")
    return card


def values_in_order(lines: list[str]) -> list[str]:
    """왼쪽 번호를 못 읽었을 때 쓴다. 네 개짜리 숫자만 읽힌 차례대로 모은다."""
    out = []
    for line in lines:
        cleaned = line.replace("O", "0").replace("o", "0").replace("l", "1")
        for token in re.findall(r"\d+", cleaned):
            if len(token) == 4:
                out.append(token)
            elif len(token) == 8:
                continue  # 일련번호는 건너뛴다
    return out


def find_serial(lines: list[str]) -> str:
    """카드 위쪽 'NO. 12345678' 같은 줄에서 일련번호를 찾는다."""
    for line in lines:
        if "NO" in line.upper():
            hit = re.findall(r"\d{6,12}", line)
            if hit:
                return hit[0]
    for line in lines:
        hit = re.findall(r"\d{8}", line)
        if hit:
            return hit[0]
    return ""


def masked(lines: list[str]) -> list[str]:
    # style-guard:off — 화면 안내 문구
    """숫자를 전부 # 로 가린다. 카드 내용을 감춘 채 생김새만 보여줄 때 쓴다."""
    return [re.sub(r"\d", "#", ln) for ln in lines]


def main() -> None:
    # style-guard:off — 아래는 화면에 그대로 뜨는 안내 문구
    if len(sys.argv) < 2:
        print("쓰기: python read_card.py \"C:\\경로\\카드사진.jpg\"")
        print("    : python read_card.py \"...jpg\" --shape   (숫자를 가린 생김새만 보기)")
        sys.exit(1)
    image = Path(sys.argv[1]).expanduser()
    if not image.exists():
        print("사진을 찾지 못했습니다:", image)
        sys.exit(1)

    print("이 PC 안에서만 읽습니다. 사진은 바깥으로 나가지 않습니다.")
    lines = run_ocr(image)

    if "--shape" in sys.argv:
        print(f"\n읽은 줄 {len(lines)}개입니다. 숫자는 전부 # 로 가렸습니다.")
        print("아래를 그대로 복사해서 알려주시면 읽는 방식을 맞추겠습니다.\n")
        for i, ln in enumerate(masked(lines), 1):
            print(f"  {i:>2}| {ln}")
        return

    # style-guard:off — 화면에 그대로 뜨는 안내 문구
    card = parse_pairs(lines)
    order_guess = False
    if len(card) < 10:
        # 몇 개만 걸리면 잘못 읽힌 줄에서 나온 가짜 짝이다. 차라리 순서대로 붙인다.
        card = {}
    if not card:
        values = values_in_order(lines)
        if not values:
            print("\n네 개짜리 숫자를 하나도 읽지 못했습니다.")
            print("더 밝고 반듯한 사진으로 다시 찍거나, `python setup_creds.py card` 로 직접 넣으세요.")
            sys.exit(2)
        order_guess = True
        card = {str(i): v for i, v in enumerate(values, 1)}
        print(f"\n왼쪽 번호를 못 읽어서 읽힌 순서대로 1번부터 붙였습니다. {len(values)}개를 찾았습니다.")
        if len(values) != 30:
            print("카드의 30개와 개수가 달라 중간이 밀렸을 수 있으니 특히 꼼꼼히 대조하세요.")

    show(card)
    card = fix_loop(card)
    show(card)

    ok = input("\n위 내용이 실물 카드와 같으면 y 를 눌러 저장합니다: ").strip().lower()
    if ok != "y":
        print("저장하지 않았습니다.")
        return

    data = {}
    if CRED_FILE.exists():
        try:
            data = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    guess = find_serial(lines)
    prompt = f"보안카드 일련번호(읽은 값 {guess} 를 쓰려면 Enter): " if guess else "보안카드 일련번호: "
    serial = input(prompt).strip() or guess
    if serial:
        data["card_serial"] = serial
    data["card"] = {**data.get("card", {}), **card}

    CRED_DIR.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    user = os.environ.get("USERNAME", "")
    if user:
        subprocess.run(["icacls", str(CRED_FILE), "/inheritance:r", "/grant:r", f"{user}:F"],
                       capture_output=True, text=True)
    print(f"\n저장했습니다: {CRED_FILE}")
    print(f"보안카드 {len(data['card'])}개 번호가 들어 있습니다.")
    print("사진 파일은 이제 지우셔도 됩니다.")


if __name__ == "__main__":
    main()
