#!/usr/bin/env python3
"""새 고시 후보를 텔레그램으로 알린다 — notify-candidates.yml 이 data/admrul-candidates.json 푸시 때 부른다.

직전 커밋에 없던 ID 만 보낸다. 보내기만 하고 답장은 받지 않는다(편입은 Dr. Ben 이 Claude 세션에서 지시 —
답장 실행 방식은 2026-11-27 검토 후 채택하지 않음: 연 0~2회 쓰는 경로는 고장 나도 모른다).
env: TELEGRAM_BOT_TOKEN · TELEGRAM_CHAT_ID. 없으면 경고만 하고 0 으로 끝난다(--dry-run 은 본문만 출력).
"""
import os, sys, json, subprocess, urllib.request, urllib.parse

PATH = 'data/admrul-candidates.json'


def ids_at(rev):
    try:
        raw = subprocess.run(['git', 'show', f'{rev}:{PATH}'], capture_output=True, text=True, check=True).stdout
        return {it['id'] for it in json.loads(raw)['items']}
    except Exception:
        return set()


def main():
    dry = '--dry-run' in sys.argv
    base = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--base=')), 'HEAD~1')
    items = json.load(open(PATH, encoding='utf-8'))['items']
    old = ids_at(base)
    new = [it for it in items if it['id'] not in old and it.get('status') == 'new']
    if not new:
        print('알릴 새 후보 없음')
        return 0
    lines = [f'📋 새 고시 후보 {len(new)}건 — 감시 목록에 없는 방사선 분야 행정규칙', '']
    for it in new:
        lines += [f"• {it['name']}", f"  {it['agency']} · {it['type']} {it['action']} · {it['issued']}", f"  {it['link']}", '']
    lines.append('넣을 것이 있으면 Claude 에게 "그 고시 넣어"라고 하면 됩니다. 답하지 않으면 그대로 둡니다.')
    text = '\n'.join(lines)[:4000]
    tok, chat = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
    if dry or not tok or not chat:
        print(text)
        if not dry:
            print('::warning::TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID 가 없어 보내지 못했습니다')
        return 0
    body = urllib.parse.urlencode({'chat_id': chat, 'text': text, 'disable_web_page_preview': 'true'}).encode()
    try:
        r = json.load(urllib.request.urlopen(f'https://api.telegram.org/bot{tok}/sendMessage', body, timeout=30))
    except Exception as e:                      # 토큰이 주소에 들어 있어 예외 원문을 찍지 않는다
        print(f'::error::텔레그램 전송 실패 ({type(e).__name__})')
        return 1
    print('보냄' if r.get('ok') else '::error::텔레그램 응답 ok 아님')
    return 0 if r.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
