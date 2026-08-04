"""WeasyPrint를 쓰기 전에 끝나 있어야 하는 준비를 한곳에 모은다.

윈도우에서 WeasyPrint는 Pango/GObject DLL을 MSYS2에서 찾는다. 그 검색 경로를
얹는 일과, 그보다 먼저 ``ssl``을 확정 짓는 일(아래 참조)은 **weasyprint를
import하기 전에** 끝나야 한다. 준비가 렌더러 모듈 하나에 얹혀 있으면 그 모듈이
먼저 import될 때만 성립하는데, 렌더러는 아홉 개고 어느 것이 먼저 걸릴지는
호출자의 import 순서가 정한다 — ``balanced_batch_selection``은
``administrative_rule_rendering``을 먼저 집어서 준비 없이 weasyprint에 닿았고,
``OSError: cannot load library ... libgobject-2.0-0.dll``로 죽었다.

그래서 준비와 ``HTML``/``URLFetcher``를 같은 모듈에 둔다. 렌더러는
weasyprint가 아니라 여기서 가져간다 — 가져가는 행위 자체가 준비를 끝낸다.
"""

from __future__ import annotations

import os
from pathlib import Path

#: 순서가 중요하다. MSYS2 mingw64는 자체 OpenSSL(libssl-3-x64.dll,
#: libcrypto-3-x64.dll)을 갖고 있고, 이는 파이썬이 쓰는 것과 다른 빌드다.
#: WeasyPrint가 필요로 하는 Pango/GObject를 찾으려고 그 폴더를
#: ``os.add_dll_directory``로 검색 경로에 얹으면, 이 프로세스가 그 전까지
#: 한 번도 ``ssl``을 안 건드렸을 경우 다음 ``import ssl``(urllib.request가
#: weasyprint 안에서 처음 로드될 때 트리거)이 파이썬 번들 OpenSSL 대신 MSYS2
#: 쪽을 집어 ABI 불일치로 조용히 실패한다 — urllib.request의
#: ``try: import ssl except ImportError`` 가드가 그걸 삼켜서 ``HTTPSHandler``가
#: 아예 없는 채로 모듈이 캐시되고, WeasyPrint import가
#: ``AttributeError: module 'urllib.request' has no attribute 'HTTPSHandler'``로
#: 죽는다. 그래서 MSYS2 디렉터리를 추가하기 **전에** ``ssl``을 먼저 import해
#: 파이썬 자신의 OpenSSL로 확정 짓는다 — 이후 같은 프로세스에서는 캐시된 모듈을
#: 재사용하므로 뒤늦게 추가되는 검색 경로에 영향받지 않는다.
import ssl  # noqa: F401

#: ``add_dll_directory``가 돌려주는 핸들은 닫히면 검색 경로에서 빠진다.
#: 프로세스가 사는 동안 붙잡아 둔다.
_WINDOWS_DLL_HANDLES: list[object] = []
if os.name == "nt" and hasattr(os, "add_dll_directory"):
    configured = os.environ.get("WEASYPRINT_DLL_DIRECTORIES", "")
    candidates = [
        *(Path(item) for item in configured.split(os.pathsep) if item),
        Path(r"C:\tools\msys64\mingw64\bin"),
    ]
    for candidate in candidates:
        if candidate.is_dir():
            _WINDOWS_DLL_HANDLES.append(os.add_dll_directory(str(candidate)))

from weasyprint import HTML  # noqa: E402
from weasyprint.urls import URLFetcher  # noqa: E402

__all__ = ["HTML", "URLFetcher"]
