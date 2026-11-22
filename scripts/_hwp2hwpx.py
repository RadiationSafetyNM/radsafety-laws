#!/usr/bin/env python3
"""hwp(바이너리) → hwpx(OWPML) 자동 변환 — 판본 고정 hwp2hwpx (2026-11-23).

왜: 법제처가 일부 별표를 .hwp 로만 준다. LibreOffice+H2Orestart 로 읽으면 표가 통째로 빠지는
일이 있어(§예외 1 의 6건) 한컴 한글로 손수 hwpx 변환해 커밋해 왔다. 오픈소스 변환기
neolord0/hwp2hwpx(Java, Apache-2.0)로 바꾼 뒤 기존 OWPML 파서(_parse_attachments.parse_hwpx)에
넣으면 — 2026-11-23 kimbi 실측:
  · 수동 변환 6건: 한컴 변환본 파싱과 **바이트 동일 6/6**
  · hwp 만 있는 별표 59건: 글자 동일 55 · 셀 배치 순서만 다름 1 · 목록번호 1자 1 · 더 나음 2
    (「방사성핵종의 상대적 위험도에 따른 분류」는 H2Orestart 가 첫 쪽을 통째로 잃고 있었다)
  · 나빠진 곳 0
→ 수동 단계가 재현 가능한 파이프라인이 되고, hwpx 파싱은 순수 파이썬이라 Java 만 있으면 CI 에서도 돈다.

판본 고정은 _pipeline.py 와 같은 방식 — 남의 코드는 .vendor/ 에 고정 체크아웃(gitignore),
라이브러리 jar 는 Maven Central 에서 받아 sha1 대조. PIN·jar 를 올리는 건 의도적 행위:
올린 뒤에는 위 6건 바이트 동일 검사를 다시 돌릴 것(`python3 scripts/_hwp2hwpx.py --selftest`).

빌드: javac 가 없으면 `java -m jdk.compiler/com.sun.tools.javac.Main` (JRE 에 컴파일러 모듈만 있는 경우).
"""
import hashlib, os, shutil, subprocess, sys, tempfile, urllib.request

REPO_URL = 'https://github.com/neolord0/hwp2hwpx.git'
PIN = '62d95b9'      # 2026-10-26 "PR#18 한글 저장본과 대조해 찾은 HWP → HWPX 변환 차이 10가지 수정"
JARS = {             # pom.xml 이 요구하는 판본 그대로 — sha1 = Maven Central 공시값
    'hwplib-1.1.10.jar': ('kr/dogfoot/hwplib/1.1.10', '64335f538b584957a0860d16c09ba47285b65e3a'),
    'hwpxlib-1.0.9.jar': ('kr/dogfoot/hwpxlib/1.0.9', 'e10a0d019d0d7d3b449e7fd2b78cf1fcdb28b011'),
}
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VENDOR = os.path.join(ROOT, '.vendor', 'hwp2hwpx')
SRC = os.path.join(VENDOR, 'src')
LIB = os.path.join(VENDOR, 'lib')
CLASSES = os.path.join(VENDOR, f'classes-{PIN}')
CLI = os.path.join(ROOT, 'ops', 'hwp2hwpx', 'Conv.java')


def available():
    return shutil.which('java') is not None


def _git(*a, cwd=None):
    return subprocess.run(['git', *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _javac():
    if shutil.which('javac'):
        return ['javac']
    return ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']


def ensure():
    """PIN 판본을 빌드해 둔다. 이미 있으면 네트워크·컴파일 없음. 반환 = java 클래스패스."""
    cp = os.pathsep.join([CLASSES] + [os.path.join(LIB, j) for j in JARS])
    stamp = os.path.join(CLASSES, '.cli-sha1')
    cli_sha = hashlib.sha1(open(CLI, 'rb').read()).hexdigest()
    if os.path.exists(stamp) and open(stamp).read() == cli_sha:
        return cp
    os.makedirs(LIB, exist_ok=True)
    for jar, (path, sha) in JARS.items():
        dst = os.path.join(LIB, jar)
        if not os.path.exists(dst) or hashlib.sha1(open(dst, 'rb').read()).hexdigest() != sha:
            data = urllib.request.urlopen(f'https://repo1.maven.org/maven2/{path}/{jar}', timeout=60).read()
            if hashlib.sha1(data).hexdigest() != sha:
                raise RuntimeError(f'{jar}: sha1 불일치 — 받은 파일을 쓰지 않습니다')
            open(dst, 'wb').write(data)
    if not os.path.isdir(os.path.join(SRC, '.git')):
        _git('clone', '--quiet', REPO_URL, SRC)
    if not _git('rev-parse', 'HEAD', cwd=SRC).startswith(PIN):
        try:
            _git('checkout', '--quiet', PIN, cwd=SRC)
        except subprocess.CalledProcessError:
            _git('fetch', '--quiet', 'origin', cwd=SRC)
            _git('checkout', '--quiet', PIN, cwd=SRC)
    shutil.rmtree(CLASSES, ignore_errors=True)
    os.makedirs(CLASSES)
    javas = [os.path.join(d, f) for d, _, fs in os.walk(os.path.join(SRC, 'src', 'main', 'java'))
             for f in fs if f.endswith('.java')]
    r = subprocess.run(_javac() + ['-nowarn', '-encoding', 'UTF-8', '-cp',
                                   os.pathsep.join(os.path.join(LIB, j) for j in JARS),
                                   '-d', CLASSES] + javas + [CLI], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('hwp2hwpx 빌드 실패: ' + r.stderr[-800:])
    open(stamp, 'w').write(cli_sha)
    return cp


def convert(pairs):
    """[(hwp, hwpx 출력)] → {hwp: hwpx} (성공분만). 실패는 stderr 에 남기고 호출부가 폴백."""
    if not pairs:
        return {}
    cp = ensure()
    done = {}
    for i in range(0, len(pairs), 200):      # 인자 길이 제한 회피
        chunk = pairs[i:i + 200]
        args = [x for p in chunk for x in p]
        r = subprocess.run(['java', '-cp', cp, 'Conv'] + args, capture_output=True, text=True, timeout=1200)
        outs = {o: h for h, o in chunk}
        for line in r.stdout.splitlines():
            if line.startswith('OK '):
                o = line[3:]
                if os.path.exists(o) and os.path.getsize(o) > 0:
                    done[outs[o]] = o
            elif line.startswith('FAIL '):
                print('  ? hwp2hwpx ' + line[5:][:160], file=sys.stderr)
    return done


def selftest():
    """한컴 한글로 수동 변환했던 6건(ops/hwp2hwpx/golden/, 구 §예외 1)과 hwp2hwpx 결과를
    파싱 단에서 대조한다 (PIN·jar 올릴 때 필수). golden 은 정답지로만 쓰고 파이프라인은 읽지 않는다."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    att = os.path.join(ROOT, 'data', 'attachments')
    pairs = []
    tmp = tempfile.mkdtemp(prefix='h2x_selftest_')
    golden = os.path.join(ROOT, 'ops', 'hwp2hwpx', 'golden')
    for fn in sorted(os.listdir(golden)):
        if fn.endswith('.hwpx') and os.path.exists(os.path.join(att, fn[:-1])):
            pairs.append((os.path.join(att, fn[:-1]), os.path.join(tmp, fn)))
    done = convert(pairs)
    from _owpml import parse_hwpx
    bad = 0
    for h, o in done.items():
        same = parse_hwpx(o) == parse_hwpx(os.path.join(golden, os.path.basename(o)))
        bad += not same
        print(('✅ 동일 ' if same else '⛔ 다름 ') + os.path.basename(h)[:70])
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'{len(done)}/{len(pairs)} 변환 · 다름 {bad}')
    return 1 if bad or len(done) != len(pairs) else 0


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    print(ensure())
