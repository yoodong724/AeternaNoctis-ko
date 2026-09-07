# 소스 빌드

리소스와 패키지 생성 명령은 Linux/WSL 기준이며, 폰트는 Windows Unity 에디터에서 생성합니다. 원본 Windows 게임, Python 3.10 이상, Unity 2022.3.62f1·TextMeshPro 3.0.7, xdelta3 3.2.0이 필요합니다. 원본은 `AeternaNoctis/`에 두고 저장소 루트에서 실행합니다. 출력 리소스에는 게임 원본 내용이 포함되므로 배포하지 않고 최종 차분 ZIP만 배포합니다.

1. `python -m pip install -r requirements.txt`를 실행합니다.
2. `font/`를 Unity 프로젝트로 열고 TMP Essential Resources를 가져옵니다.
3. [Noto CJK](https://github.com/notofonts/noto-cjk)의 Noto Sans CJK KR Regular OTF 2.004를 준비합니다. 사용한 파일의 SHA-256은 `6bcb2a0703aa137e874fc2dffa85f6c21ba9a67fa329e81b8c801663af7e992a`입니다.
4. Unity 에디터를 다음 인수로 실행합니다. 각 경로는 실제 절대 경로로 지정합니다.

```text
-batchmode -nographics -quit -projectPath <저장소/font>
-executeMethod BuildKoreanFont.Run
--source-font <NotoSansCJKkr-Regular.otf>
--charset <저장소/font/charset.hex.txt>
--bundle-output <저장소/build/font>
--sampling-point-size 38
```

5. 한국어 리소스를 생성합니다.

```sh
python scripts/build_translation.py --generated-bundle build/font/aeterna-korean-font
```

6. [xdelta3 3.2.0](https://github.com/jmacd/xdelta/releases/tag/v3.2.0)의 실행 도구와 라이선스를 준비해 패키지를 만듭니다. Windows 설치용 `xdelta3.exe`도 별도로 필요합니다.

```sh
python scripts/release/build_patch.py --xdelta3 <인코딩용-xdelta3> --windows-xdelta3 <Windows-xdelta3.exe> --xdelta-license <xdelta3-LICENSE>
python scripts/validate/check_release_patch.py --package dist/AeternaNoctis-ko --archive dist/AeternaNoctis-ko-1.0.1.zip --xdelta3 <인코딩용-xdelta3>
python -m unittest discover -s tests
```

생성기는 지원 원본과 1.0.1 결과 해시를 확인합니다. `translations/ko.tsv`는 원문 없이 한국어 변경 셀과 원문 식별값만 담습니다. TSV의 `\\`, `\t`, `\r`, `\n`은 이스케이프이며, 원본 행 순서와 키·해시를 임의로 바꾸지 않습니다. 번역이나 폰트를 수정해 새 버전을 만들 때는 결과 검수 후 생성기의 버전·결과 해시도 갱신해야 합니다.
