"""화면 색과 서체 — 한 곳에서.

어두운 바탕에 라임 강조, 모노 서체. 라임은 우리 브랜드 색 계열이다.
"""
from __future__ import annotations

__all__ = ["BG", "PANEL", "VIDEO_BG", "LIME", "TEXT", "TEXT_DIM", "TRACK", "ORANGE", "RED", "MONO", "MONO_SMALL"]

BG = "#1e1e1e"          # 창 바탕
PANEL = "#2a2a2a"       # 상단바·하단바
VIDEO_BG = "#000000"    # 영상 레터박스
LIME = "#d4ff3f"        # 강조 — 활성 탭, 슬라이더, 아이콘, 값
TEXT = "#e8e8e8"
TEXT_DIM = "#8a8a8a"    # 비활성 행
TRACK = "#4a4a4a"       # 슬라이더 빈 구간
ORANGE = "#f2a33a"      # 쓰기 횟수 경고
RED = "#ff5c5c"         # 드라이버가 거부한 쓰기

MONO = ("Consolas", 10)
MONO_SMALL = ("Consolas", 9)
