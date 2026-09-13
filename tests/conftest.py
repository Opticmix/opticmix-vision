"""테스트가 설치 없이 패키지를 import 하도록 sdk/python 을 sys.path 앞에 넣는다."""

from __future__ import annotations

import sys
from pathlib import Path

_SDK_ROOT = Path(__file__).resolve().parents[1]
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))
