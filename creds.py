"""
== 운영 맥락 ==
용도: 홈택스 자격(아이디·비밀번호·보안카드)을 저장소 밖 파일에서 읽는다. 화면·로그에 절대 찍지 않는다.
실행 시점: issue.py / run_queue.py 가 불러 씀. 저장은 사람이 `python setup_creds.py` 로 직접(9.1 채팅 입력 금지).
입력: %USERPROFILE%\\.hometax\\credentials.json (이 저장소 바깥, 접근 권한 본인만)
출력: 없음. 조회 함수만 제공.
외부 의존: 없음.
의도적 미구현: 화면 출력·로그 기록·채팅 전달. 암호화(운영체제 계정 권한으로만 보호 — 사용자 결정).
마지막 점검: 2026-09-10
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CRED_DIR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".hometax"
CRED_FILE = CRED_DIR / "credentials.json"


class MissingCredentials(RuntimeError):
    pass


_cache: dict | None = None
_cache_stamp: float | None = None


def _load() -> dict:
    """자격 파일을 한 번만 읽어 기억해 둔다. 파일이 바뀌면 그때만 다시 읽는다."""
    global _cache, _cache_stamp
    if not CRED_FILE.exists():
        _cache, _cache_stamp = None, None
        raise MissingCredentials(
            f"자격 파일이 없습니다: {CRED_FILE}\n"
            "명령 프롬프트에서 `python setup_creds.py` 를 실행해 직접 입력하세요."
        )
    stamp = CRED_FILE.stat().st_mtime
    if _cache is None or _cache_stamp != stamp:
        _cache = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        _cache_stamp = stamp
    return _cache


def login_id() -> str:
    got = _load().get("hometax_id", "")
    if not got:
        raise MissingCredentials("홈택스 아이디가 저장돼 있지 않습니다. setup_creds.py 를 실행하세요.")
    return got


def login_pw() -> str:
    got = _load().get("hometax_pw", "")
    if not got:
        raise MissingCredentials("홈택스 비밀번호가 저장돼 있지 않습니다. setup_creds.py 를 실행하세요.")
    return got


def rrn_front() -> str:
    """아이디 로그인 다음 화면이 묻는 주민등록번호 앞 6자리(생년월일)."""
    got = _load().get("rrn_front", "")
    if not got:
        raise MissingCredentials("주민번호 앞 6자리가 저장돼 있지 않습니다. setup_creds.py 를 실행하세요.")
    return got


def rrn_back1() -> str:
    """같은 화면이 함께 묻는 뒷자리 첫 숫자 하나."""
    got = _load().get("rrn_back1", "")
    if not got:
        raise MissingCredentials("주민번호 뒷자리 1개가 저장돼 있지 않습니다. setup_creds.py 를 실행하세요.")
    return got


def has_rrn() -> bool:
    try:
        saved = _load()
    except MissingCredentials:
        return False
    return bool(saved.get("rrn_front")) and bool(saved.get("rrn_back1"))


def card_serial() -> str:
    got = _load().get("card_serial", "")
    if not got:
        raise MissingCredentials("보안카드 일련번호가 저장돼 있지 않습니다. setup_creds.py 를 실행하세요.")
    return got


def card_number(position: str | int) -> str:
    """보안카드 자리번호(예: 7, '07')에 적힌 숫자를 돌려준다. 없으면 즉시 멈춤."""
    card = _load().get("card", {})
    want = str(position).strip().lstrip("0") or "0"
    for pos, digits in card.items():
        if str(pos).strip().lstrip("0") == want:
            return str(digits)
    raise MissingCredentials(f"보안카드 {position}번이 저장돼 있지 않습니다. setup_creds.py 로 다시 넣으세요.")


def card_positions() -> list[str]:
    """저장된 자리번호 목록(숫자는 안 돌려줌). 점검용."""
    return sorted(_load().get("card", {}).keys(), key=lambda x: int(str(x)))


def status() -> str:
    """내용을 드러내지 않고 저장 상태만 알려준다."""
    try:
        saved = _load()
    except MissingCredentials as e:
        return str(e)
    card = saved.get("card", {})
    return (
        f"자격 파일: {CRED_FILE}\n"
        f"아이디 저장됨: {'예' if saved.get('hometax_id') else '아니오'}\n"
        f"비밀번호 저장됨: {'예' if saved.get('hometax_pw') else '아니오'}\n"
        f"주민번호 앞 6자리 저장됨: {'예' if saved.get('rrn_front') else '아니오'}\n"
        f"주민번호 뒷자리 1개 저장됨: {'예' if saved.get('rrn_back1') else '아니오'}\n"
        f"보안카드 일련번호 저장됨: {'예' if saved.get('card_serial') else '아니오'}\n"
        f"보안카드 자리 수: {len(card)}개"
    )


if __name__ == "__main__":
    print(status())
