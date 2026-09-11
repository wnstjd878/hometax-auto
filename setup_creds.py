"""
style-guard:off  (코드 저장소 도구 — CLAUDE.md 12절 비적용 범위)

== 운영 맥락 ==
용도: 홈택스 아이디·비밀번호·보안카드를 사용자가 자기 명령 프롬프트에서 직접 입력해 저장한다(9.1 채팅 입력 금지).
실행 시점: 사람이 수동. `python setup_creds.py` 또는 `python setup_creds.py login` / `card`.
입력: 키보드(비밀번호는 화면에 안 보임). 출력: %USERPROFILE%\\.hometax\\credentials.json (본인 계정만 접근)
외부 의존: icacls (Windows 접근 권한 제한)
의도적 미구현: 사진 읽기로 보안카드 자동 입력 — 사진이 외부로 나가면 자격 유출이라 금지.
마지막 점검: 2026-09-10
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

from creds import CRED_DIR, CRED_FILE

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_existing() -> dict:
    if CRED_FILE.exists():
        try:
            return json.loads(CRED_FILE.read_text(encoding="utf-8"))
        except Exception:
            print("기존 파일을 읽지 못해 새로 만듭니다.")
    return {}


def save(data: dict) -> None:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    user = os.environ.get("USERNAME", "")
    if user:
        subprocess.run(["icacls", str(CRED_FILE), "/inheritance:r", "/grant:r", f"{user}:F"],
                       capture_output=True, text=True)
    print(f"\n저장했습니다: {CRED_FILE}")
    print("이 파일은 본인 계정만 열 수 있게 권한을 제한했습니다. 다른 곳에 복사하지 마세요.")


def ask_login(data: dict) -> None:
    print("\n[홈택스 아이디 로그인]")
    print("입력하는 동안 비밀번호는 화면에 보이지 않습니다.")
    user_id = input("홈택스 아이디: ").strip()
    if user_id:
        data["hometax_id"] = user_id
    pw = getpass.getpass("홈택스 비밀번호: ").strip()
    if pw:
        data["hometax_pw"] = pw
    # style-guard:off — 아래는 화면에 그대로 뜨는 안내 문구라 사용자 표현을 유지한다
    print("\n로그인 다음 화면이 주민등록번호 앞 6자리와 뒷자리 첫 숫자 하나를 묻습니다.")
    front = getpass.getpass("주민번호 앞 6자리(예: 900101, 안 쓰면 Enter): ").strip()
    if front:
        if front.isdigit() and len(front) == 6:
            data["rrn_front"] = front
        else:
            print("여섯 개 숫자여야 해서 저장하지 않았습니다. 다시 실행해 넣으세요.")
    back = getpass.getpass("주민번호 뒷자리 첫 숫자 1개(안 쓰면 Enter): ").strip()
    if back:
        if back.isdigit() and len(back) == 1:
            data["rrn_back1"] = back
        else:
            print("숫자 하나여야 해서 저장하지 않았습니다. 다시 실행해 넣으세요.")


def ask_card(data: dict) -> None:
    print("\n[전자세금계산서 보안카드]")
    print("카드 앞면의 일련번호와, 1~30번에 적힌 4자리 숫자를 넣습니다.")
    print("주의: 홈택스에서 5회 틀리면 카드가 정지돼 재발급해야 합니다. 옮겨 적을 때 한 번 더 확인하세요.")
    serial = input("보안카드 일련번호: ").strip()
    if serial:
        data["card_serial"] = serial

    # style-guard:off — 화면에 그대로 뜨는 안내 문구
    print("\n1번부터 차례대로 네 개짜리 숫자만 치면 됩니다. 한 줄에 하나씩이든 여러 개든 상관없습니다.")
    print("  1234")
    print("  5678 9012")
    print("건너뛰거나 고칠 때만 '7 1234' 처럼 번호를 앞에 붙이세요.")
    print("다 넣었으면 빈 줄에서 Enter 를 누르세요.")
    card = dict(data.get("card", {}))
    nxt = 1
    while nxt in [int(k) for k in card] and nxt <= 30:
        nxt += 1
    while True:
        try:
            line = input(f"{nxt}번> " if nxt <= 30 else "30번까지 다 넣었습니다. 저장하려면 Enter> ").strip()
        except EOFError:
            break
        if not line:
            break
        parts = line.replace(":", " ").replace("\t", " ").replace(",", " ").split()

        # 네 개짜리 숫자만 있으면 차례대로 붙인다
        if parts and all(p.isdigit() and len(p) == 4 for p in parts):
            for digits in parts:
                if nxt > 50:
                    print("  건너뜀(번호가 50을 넘었습니다)")
                    break
                card[str(nxt)] = digits
                nxt += 1
            while nxt in [int(k) for k in card] and nxt <= 30:
                nxt += 1
            continue

        if len(parts) < 2:
            print("  건너뜀(네 개짜리 숫자이거나 '번호 숫자' 여야 합니다):", line[:20])
            continue
        for i in range(0, len(parts) - 1, 2):
            pos, digits = parts[i], parts[i + 1]
            if not pos.isdigit() or not digits.isdigit():
                print(f"  건너뜀(숫자가 아님): {pos} {digits}")
                continue
            if not 1 <= int(pos) <= 50:
                print(f"  건너뜀(카드에 없는 번호): {pos}")
                continue
            if len(digits) != 4:
                print(f"  건너뜀({pos}번을 {len(digits)}자리로 넣으셨는데 카드는 4자리입니다): 다시 넣으세요")
                continue
            if int(pos) > 30:
                print(f"  주의: {pos}번은 30번을 넘습니다. 카드에 있는 번호가 맞는지 확인하세요")
            card[str(int(pos))] = digits
            nxt = int(pos) + 1
            while nxt in [int(k) for k in card] and nxt <= 30:
                nxt += 1
    data["card"] = card
    print(f"\n보안카드 {len(card)}개 번호를 넣었습니다.")
    missing = [str(n) for n in range(1, 31) if str(n) not in card]
    if missing:
        print("아직 안 넣은 번호:", ", ".join(missing))
        print("(30번까지 다 넣어야 어느 번호를 물어봐도 답할 수 있습니다)")


def ask_rrn(data: dict) -> None:
    # style-guard:off — 화면에 그대로 뜨는 안내 문구
    print("\n[주민등록번호]")
    front = getpass.getpass("앞 6자리(그대로 두려면 Enter): ").strip()
    if front:
        if front.isdigit() and len(front) == 6:
            data["rrn_front"] = front
        else:
            print("여섯 개 숫자여야 해서 저장하지 않았습니다.")
    back = getpass.getpass("뒷자리 첫 숫자 1개: ").strip()
    if back:
        if back.isdigit() and len(back) == 1:
            data["rrn_back1"] = back
        else:
            print("숫자 하나여야 해서 저장하지 않았습니다.")


def main() -> None:
    what = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if what == "status":
        from creds import status
        print(status())
        return
    data = load_existing()
    if what in ("all", "login"):
        ask_login(data)
    if what == "rrn":
        ask_rrn(data)
    if what in ("all", "card"):
        ask_card(data)
    save(data)
    print("\n이제 텔레그램에 '세발' 이라고 보내면 로그인부터 발급까지 사람 손 없이 돕니다.")


if __name__ == "__main__":
    main()
