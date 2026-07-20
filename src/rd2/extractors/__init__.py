"""O트랙 수집 문서(PDF/HWP/HWPX)에서 텍스트/표를 추출하는 파이프라인.

모델 추론(OCR/VLM) 없이 파일 자체의 구조 정보(PDF 콘텐츠 스트림, HWP 레코드,
HWPX XML)만으로 파싱 가능한 형식을 우선 처리하고, 텍스트 레이어가 없거나
깨진 경우만 별도 배치 단계(OCR/VLM 폴백)의 대상으로 표시한다.

adapters/(수집)·generators/(C/S 합성 생성)와는 책임이 다른 세 번째 축이다.
"""

from __future__ import annotations
