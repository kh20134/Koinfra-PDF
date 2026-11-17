# Koinfra PDF

Windows용 PDF 편집기(PyQt5)입니다. 여러 PDF를 합치고 페이지를 정리하며, 도장과 결재란을 넣어 저장할 수 있습니다.

## 주요 기능
- **페이지:** 병합, 추출, 삽입, 삭제, 회전, 드래그로 순서 변경, 썸네일 미리보기
- **보안:** PDF 암호 설정과 해제
- **주석:** 사각형과 형광펜
- **도장:** 도장 이미지를 넣고, 오른쪽 클릭으로 설정과 저장
- **결재:** 결재란(담당/검토/승인 등)과 내 서명을 넣고, 오른쪽 클릭으로 설정과 저장
- **인쇄**와 확대·축소
- **PDF 기본 앱 설정:** Windows 앱 선택 창 또는 설정 화면으로 연결하고, 설정이 끝나면 자동으로 확인

## 설치 (일반 사용자)
1. [Releases](../../releases)에서 `KoinfraPDF_Setup_1.0.exe`를 받아 실행합니다.
2. 마지막 화면의 **"PDF 기본 앱으로 설정"**을 체크한 채 마칩니다.
3. 뜨는 창에서 **Koinfra PDF 선택 → '항상'**을 클릭합니다.
   - 설정 화면이 열리면 **.pdf → Koinfra PDF → '기본값 설정'**을 클릭합니다.

> Windows 10/11은 기본 앱을 프로그램이 강제로 바꾸지 못하게 막아 둡니다. 그래서 마지막 선택 한 번은 사용자가 직접 해야 합니다.

## 소스에서 실행
```bash
pip install -r requirements.txt
python Koinfra.py [열 PDF 파일...]
```
Python 3.11 또는 3.12, Windows 10/11에서 동작을 확인했습니다.

## 빌드
```bash
# 1) 실행 파일 (폴더형, 빠른 시작)
pip install pyinstaller
pyinstaller --noconfirm --distpath dist_installer --workpath build_installer KoinfraPDFEdit_installer.spec

# 2) 설치 파일 (Inno Setup 6)
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" KoinfraPDF.iss   # -> installer\KoinfraPDF_Setup_1.0.exe
```
`KoinfraPDFEdit.spec`은 실행 파일 하나로 만드는(onefile) 빌드용입니다.

## 테스트
```bash
python test/static_check.py .
python test/dyn_test.py . out_dyn
python test/approval_test.py . out_appr t
python test/assoc_test.py . out_assoc   # 임시 레지스트리 키(HKCU\Software\KoinfraTest)만 사용
```

## 라이선스 안내
- 툴바 아이콘은 [Lucide](https://lucide.dev) 아이콘을 기반으로 합니다. 라이선스는 `ICONS_LICENSE.txt`(ISC)를 참고하세요.
- 이 프로젝트 자체의 라이선스는 아직 정하지 않았습니다.
