#!/usr/bin/env python3
"""legalize-pipeline 변환기 차용 — 판본 고정(pinned) 체크아웃 + XML→마크다운 한 함수.

법제처 API 를 직접 받되 **파싱은 직접 짜지 않는다.** 미러(legalize-kr)를 만드는 바로 그
변환 코드가 공개돼 있다(legalize-kr/legalize-pipeline, Apache-2.0). 그 코드를 판본 고정으로
받아 쓰면 미러와 같은 마크다운이 나온다 — 2026-11-20 실측: 원자력안전법 시행령(MST 291879)·
시행규칙(291967) 두 건이 미러 파일과 바이트 일치.

  왜 복사(vendoring)가 아니라 고정 체크아웃인가: 남의 코드를 우리 이력에 섞지 않고,
  판본을 올리는 일이 PIN 한 줄 수정이라는 **의도적 행위**가 되게 하려는 것이다.
  ⚠️ PIN 을 올릴 때는 `_collect_eflaw.py --check-mirror` 로 미러 대조를 다시 볼 것.
     상류가 변환 규칙을 바꾸면 청크 ID 가 흔들린다.

체크아웃 위치: <repo>/.vendor/legalize-pipeline (gitignore). 의존성: requests·pyyaml·python-dotenv.
"""
import os, sys, subprocess

REPO_URL = 'https://github.com/legalize-kr/legalize-pipeline.git'
PIN = '1a800f22e0c1868b086fc7b1351226961c633c6f'   # 2026-11-15
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DEST = os.path.join(ROOT, '.vendor', 'legalize-pipeline')


def _git(*a, cwd=None):
    return subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def ensure():
    """PIN 판본이 DEST 에 있게 한다. 이미 맞으면 네트워크를 타지 않는다."""
    if not os.path.isdir(os.path.join(DEST, '.git')):
        os.makedirs(os.path.dirname(DEST), exist_ok=True)
        _git('clone', '--quiet', REPO_URL, DEST)
    if _git('rev-parse', 'HEAD', cwd=DEST) != PIN:
        try:
            _git('checkout', '--quiet', PIN, cwd=DEST)
        except subprocess.CalledProcessError:
            _git('fetch', '--quiet', 'origin', cwd=DEST)
            _git('checkout', '--quiet', PIN, cwd=DEST)
    return DEST


_loaded = None


def _load():
    global _loaded
    if _loaded is None:
        ensure()
        # 변환기는 import 시점에 캐시·작업 경로를 잡는다 — 우리 저장소 밖으로 새지 않게 가둔다.
        os.environ.setdefault('WORKSPACE_ROOT', os.path.join(ROOT, '.vendor'))
        os.environ.setdefault('LEGALIZE_CACHE_DIR', os.path.join(ROOT, '.vendor', '.cache'))
        os.environ.setdefault('LAW_OC', os.environ.get('MOLEG_OC', ''))
        sys.path.insert(0, DEST)
        import logging
        logging.getLogger().setLevel(logging.WARNING)
        from laws import api_client, converter
        _loaded = (api_client, converter)
    return _loaded


def law_xml_to_markdown(raw_xml: bytes, mst: str) -> str:
    """법제처 lawService XML(target=law 또는 eflaw) → 미러와 같은 형식의 마크다운(frontmatter 포함).

    상류의 `get_law_detail` 은 조회(target=law 고정)와 파싱이 한 함수에 묶여 있다. 우리는
    시행일 기준 판본(target=eflaw&efYd=)을 따로 받아야 하므로, 그 함수의 캐시 훅에 우리가
    받은 XML 을 물려 **파싱부만** 쓴다. 상류가 이 구조를 바꾸면 여기서 깨진다 — PIN 이 그걸 막는다.
    """
    api_client, converter = _load()
    orig_get, orig_put = api_client.cache.get_detail, api_client.cache.put_detail
    api_client.cache.get_detail = lambda _m: raw_xml
    api_client.cache.put_detail = lambda *_a, **_k: None
    try:
        detail = api_client.get_law_detail(str(mst))
    finally:
        api_client.cache.get_detail, api_client.cache.put_detail = orig_get, orig_put
    return converter.law_to_markdown(detail)


if __name__ == '__main__':
    print(ensure(), PIN[:8])
