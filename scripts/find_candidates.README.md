# find_candidates.py — 조항별(5~8호) 기밀도 상승 후보 span 탐지

`data/annotated/` 전체를 훑어 정보공개법 제9조 5~8호 각각에 해당할 만한 문구(span)를
정규식/키워드로 찾아 `data/candidates/clause_{5,6,7,8}.jsonl`에 저장한다. 실제 텍스트를
바꾸지는 않고 "이 span이 후보다"라는 목록만 만든다 — 다음 단계(`run_llm_augment.py`)가
이 목록을 받아 LLM으로 실제 치환 문구를 생성한다.

탐지 로직 본체는 `src/rd2/augmentation/candidates.py`에 있고, 이 스크립트는 그 로직을
`data/annotated/` 전체에 적용해 파일로 저장하는 CLI 래퍼다.

## 사전 준비 (EC2 포함 어느 환경이든 공통)

1. 저장소 clone 후 의존성 설치 (자세한 건 최상위 [`README.md`](../README.md) 참고):
   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install -e .
   ```
2. **`data/annotated/`가 이미 채워져 있어야 한다.** `data/`는 `.gitignore` 대상이라
   git clone만으로는 비어 있다. 아래 둘 중 하나로 준비한다:
   - 이미 만들어진 `data/annotated/`를 다른 인스턴스/S3 등에서 그대로 동기화해온다 (가장 빠름).
   - 없다면 파이프라인을 앞 단계부터 직접 돌린다:
     ```bash
     python scripts/extract_pdf_text.py --source all     # data/*.pdf → data/extracted/
     python scripts/annotate_documents.py --source all   # data/extracted/ → data/annotated/
     ```
     (`--source`는 `molit`/`mohw`/`moe`/`PRISM` 중 하나로 좁혀서 파일럿 실행도 가능)

이 스크립트 자체는 DB나 `.env`, OpenAI API 키가 필요 없다 — 로컬 파일만 읽고 쓴다.

## 실행

```bash
python scripts/find_candidates.py --clause 8     # 조항 하나만
python scripts/find_candidates.py --clause all   # 5/6/7/8 전부 (기본값)
```

실행할 때마다 대상 조항의 `data/candidates/clause_{N}.jsonl`을 **덮어쓴다**(append 아님).
`candidates.py`의 필터 로직을 고친 뒤에는 반드시 재실행해서 결과를 새로 만들어야 한다.

## 출력

`data/candidates/clause_{5,6,7,8}.jsonl` — 한 줄에 후보 span 하나(JSON):

```json
{"source_pdf_path": "...", "source": "moe", "doc_type": "budget_material",
 "doc_id": "...", "span_id": 94, "text": "...", "bbox": [...], "page_no": 10}
```

문서 하나당 후보는 최대 20개로 제한된다(`candidates.py`의 `_MAX_CANDIDATES_PER_DOC`) —
특정 문서가 후보 풀을 독점하지 않게 하기 위함.

실행이 끝나면 조항별로 이렇게 요약을 출력한다:

```
[8호] 후보 12660개 / 문서 847개
    molit    11426개
    moe      1187개
    mohw     47개
    저장: data/candidates/clause_8.jsonl
```

## 소요 시간

전 조항 순회는 `data/annotated/`를 조항 수만큼(최대 4번) 반복해서 읽기 때문에 디스크
I/O가 병목이다. 참고치: 약 5GB(2,164개 파일) 코퍼스 기준 조항 1개당 1~2분, `--clause all`
전체는 5~8분 정도 걸렸다(로컬 macOS, 디스크 스펙에 따라 EC2에서는 더 걸릴 수 있음).

## 필터 로직을 고칠 때 (새 부처/지자체 문서가 들어올 때)

`candidates.py`의 키워드·정규식은 moe/mohw/molit 코퍼스로 실측 검증된 것이다. 다른
기관 문서가 새로 들어오면 그 문서에서도 잘 맞는지 다시 확인해야 한다 — 무작정 키워드를
늘리면 안 된다. 이 프로젝트가 지금까지 반복해온 절차:

1. `candidates.py`에 후보 키워드/패턴을 추가한다.
2. `python scripts/find_candidates.py --clause N`으로 재생성한다.
3. 새로 늘어난 매치 중 새 키워드가 잡은 것만 골라 표본을 눈으로 확인한다(예:
   `grep`으로 텍스트만 뽑아 랜덤 샘플링). 조항과 무관한 문맥을 대량으로 잡는 키워드는
   ("평가"/"검토"/"개발"/"지정"/"도시재생"/"용도변경"/"특허" 등 실측으로 이미 여러 번
   확인됨) 다시 빼야 한다 — 단독으로 쓰이면 너무 광범위한 일반명사·정책 용어가 특히
   위험하다.
4. 뺀 이유는 `candidates.py` 주석에 남긴다(이미 위 단어들에 대한 실측 근거가 파일
   상단 주석에 적혀 있다) — 다음 사람이 같은 실수를 반복하지 않도록.

6호(개인정보)는 메커니즘이 다르다: 단독 직책/부서명(예: "홍보담당관")은 후보로 보지
않고, "라벨: 값" 구조(예: "성명:", "대표자성명:")가 명확한 것만 인정한다. 또한 직위명이
붙은 라벨(예: "...담당자: 소은주 정책관")은 공무원 직무 수행 관련 정보라 정보공개법
6호 단서에 따라 비공개 사유가 되기 어려워 제외한다(`_CLAUSE_6_GOV_TITLE_PATTERN`).
