// hwp → hwpx 일괄 변환 CLI (scripts/_hwp2hwpx.py 가 빌드·호출). 인자: 입력1 출력1 입력2 출력2 ...
// 파일마다 "OK <출력>" 또는 "FAIL <입력> :: <예외>" 한 줄. 한 건이 실패해도 나머지는 계속한다.
import kr.dogfoot.hwplib.object.HWPFile;
import kr.dogfoot.hwplib.reader.HWPReader;
import kr.dogfoot.hwp2hwpx.Hwp2Hwpx;
import kr.dogfoot.hwpxlib.object.HWPXFile;
import kr.dogfoot.hwpxlib.writer.HWPXWriter;

public class Conv {
    public static void main(String[] a) {
        for (int i = 0; i + 1 < a.length; i += 2) {
            try {
                HWPFile f = HWPReader.fromFile(a[i]);
                HWPXFile x = Hwp2Hwpx.toHWPX(f);
                HWPXWriter.toFilepath(x, a[i + 1]);
                System.out.println("OK " + a[i + 1]);
            } catch (Throwable e) {
                System.out.println("FAIL " + a[i] + " :: " + e);
            }
        }
    }
}
