# find_candidates.py — 조항별(5~8호) 기밀도 상승 후보 span 탐지

`data/annotated/` 전체를 훑어 정보공개법 제9조 5~8호 각각에 해당할 만한 문구(span)를
정규식/키워드로 찾아 `data/candidates/clause_{5,6,7,8}.jsonl`에 저장한다. 실제 텍스트를
바꾸지는 않고 "이 span이 후보다"라는 목록만 만든다 — 다음 단계
([`run_llm_augment.README.md`](./run_llm_augment.README.md))가 이 목록을 받아 LLM으로
실제 치환 문구를 생성한다("원문 → 치환 텍스트 변환"을 실제로 하는 스크립트는 그쪽이다).

탐지 로직 본체는 `src/rd2/augmentation/candidates.py`에 있고, 이 스크립트는 그 로직을
`data/annotated/` 전체에 적용해 파일로 저장하는 CLI 래퍼다.

이 스크립트 + `run_llm_augment.py`를 매번 따로 실행하기 번거로우면
`python scripts/run_pipeline.py --clause N ...` 하나로 이어서 실행할 수 있다 —
자세한 건 [`run_llm_augment.README.md`](./run_llm_augment.README.md) 참고.

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
     (`--source`는 고정된 목록이 아니다 — `data/{폴더명}/`에 PDF를 두고 그 폴더명을
     그대로 주면 된다. 예: 새 지자체 문서를 넣었다면 `data/new_agency/`에 두고
     `--source new_agency`. 특정 소스로 좁혀서 파일럿 실행하고 싶을 때도 이렇게 쓴다)

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

1. `candidates.py`의 `_CLAUSE_KEYWORDS`(5/7/8호) 또는 `_CLAUSE_6_LABEL_PATTERN`/
   `_CLAUSE_6_GOV_TITLE_PATTERN`(6호)에 후보 키워드/패턴을 추가한다.
2. `python scripts/find_candidates.py --clause N`으로 재생성한다(해당 조항의
   `data/candidates/clause_N.jsonl`을 덮어씀 — "소요 시간" 절 참고).
3. **새로 늘어난 매치 중 새 키워드가 잡은 것만 골라 표본을 직접 확인한다.** 아래는
   그대로 복붙해서 쓸 수 있는 스니펫이다(`새_키워드` 리스트만 이번에 추가한 단어로
   바꾸면 됨) — 문서 앞뒤 맥락 없이 텍스트만 보고 "이게 진짜 그 조항과 관련 있는
   내용인가"를 사람이 눈으로 판단해야 한다(코드로는 판단 불가):
   ```bash
   source .venv/bin/activate
   python3 -c "
   import json, random, collections

   새_키워드 = ['여기에', '추가한', '키워드', '나열']  # <- 이번에 추가한 단어로 교체
   clause_no = '5'  # <- 대상 조항

   cnt = collections.Counter()
   hits = collections.defaultdict(list)
   with open(f'data/candidates/clause_{clause_no}.jsonl', encoding='utf-8') as f:
       for line in f:
           c = json.loads(line)
           for kw in 새_키워드:
               if kw in c['text']:
                   cnt[kw] += 1
                   hits[kw].append(c['text'][:70])

   print('키워드별 매치 건수:', dict(cnt))
   random.seed(0)
   for kw, texts in hits.items():
       print(f'--- {kw} 표본(최대 8개) ---')
       for t in random.sample(texts, min(8, len(texts))):
           print(' ', repr(t))
   "
   ```
   매치 건수가 수백 건 이상으로 튀거나(예: "도시재생" 487건, "용도변경" 130건 —
   실제로 조항과 무관한 일반 정책·행정 서술이 대부분이었음), 표본 8개 중 절반
   이상이 조항과 무관해 보이면 그 키워드는 과매칭이다.
4. 과매칭으로 판단되면 키워드를 빼고 1~2번을 반복한다. 뺀 이유(매치 건수, 표본
   예시, 왜 무관한지)는 `candidates.py` 상단 주석에 남긴다 — 다음 사람이 같은
   단어를 다시 추가하는 실수를 반복하지 않도록. 회귀 방지를 위해
   `tests/test_candidates.py::test_clause_5_7_8_pruned_keywords_stay_removed`에도
   빠진 단어를 추가해두면, 나중에 실수로 다시 넣었을 때 테스트가 바로 잡아준다.

## 테스트

`candidates.py`의 탐지 로직(6호 오탐 가드레일, 과매칭으로 뺀 키워드가 다시 안
들어오는지 등)은 `tests/test_candidates.py`가 검증한다. 필터 로직을 고쳤으면
반드시 실행:

```bash
pytest tests/test_candidates.py -v   # 이 파일만
pytest                                 # 전체 스위트(146개, 이 PR 기준)
```

6호(개인정보)는 메커니즘이 다르다: 단독 직책/부서명(예: "홍보담당관")은 후보로 보지
않고, "라벨: 값" 구조(예: "성명:", "대표자성명:")가 명확한 것만 인정한다. 또한 직위명이
붙은 라벨(예: "...담당자: 소은주 정책관")은 공무원 직무 수행 관련 정보라 정보공개법
6호 단서에 따라 비공개 사유가 되기 어려워 제외한다(`_CLAUSE_6_GOV_TITLE_PATTERN`).
