# run_llm_augment.py — LLM 텍스트 치환(Hard-example 증강) 실행

`find_candidates.py`가 찾아둔 후보 span(`data/candidates/clause_{N}.jsonl`)을 문서
단위로 묶어 LLM에 보내고, 일부 span을 "기밀도가 상승한 문구"로 실제로 치환해
`data/augmented/llm/`에 저장한다. 이게 실제 **원문 → 치환 텍스트 변환**을 수행하는
스크립트다 — `find_candidates.py`는 "어디를 바꿀 후보인지"만 찾을 뿐, 실제로 무엇으로
바꿀지는 여기서 LLM이 결정한다.

전체 파이프라인은 3단계다:
```
1. extract_pdf_text.py    data/*.pdf              → data/extracted/   (텍스트+위치 추출)
2. annotate_documents.py  data/extracted/          → data/annotated/  (반복헤더 등 주석)
3. find_candidates.py     data/annotated/          → data/candidates/ (조항별 후보 탐지)
4. run_llm_augment.py     data/candidates/         → data/augmented/llm/  (← 이 문서)
```

## 사전 준비

1. 최상위 [`README.md`](../README.md)의 설치 절차(venv+`requirements.txt`) 완료.
2. **`data/candidates/clause_{N}.jsonl`이 이미 있어야 한다.** 없으면 먼저
   [`find_candidates.README.md`](./find_candidates.README.md) 절차대로
   `python scripts/find_candidates.py --clause N`(또는 `all`)을 실행해서 만든다.
3. **`.env`에 `OPENAI_API_KEY`가 설정돼 있어야 한다** — `--dry-run`이 아니면 실제로
   OpenAI API를 호출해서 **비용이 발생한다**(문서당 요청 1회, 참고: 전체 코퍼스
   처리해도 실측상 $1~3 수준 — `AUGMENTATION_STATUS.md` "주요 설계 결정 요약" 참고).
4. **로컬 MariaDB가 켜져 있어야 한다**(`--dry-run`이 아닐 때만). 원본 O트랙 문서의
   실제 메타데이터(제목/기관/생산일자)를 `origin_document` 필드에 참고용으로 붙이기
   위해 `DocumentStore`로 DB를 조회한다 — 최상위 README의 "로컬 MariaDB 설치" 절차
   참고. EC2라면 `.env`의 `MARIADB_HOST`를 RDS 엔드포인트로 맞춰야 한다
   (`deploy/README.md` 참고).

## 실행

먼저 실제 호출 없이 프롬프트만 확인(비용 없음, DB 연결도 안 함):
```bash
python scripts/run_llm_augment.py --clause 5 --limit 1 --dry-run
```

실제로 1개 문서만 처리해서 결과를 눈으로 확인(과금 발생, 소액):
```bash
python scripts/run_llm_augment.py --clause 5 --limit 1
```

여러 문서 처리(예: 10개):
```bash
python scripts/run_llm_augment.py --clause 5 --limit 10
```

이미 처리된 문서(출력 파일이 이미 존재)는 기본적으로 건너뛴다. 다시 돌리려면:
```bash
python scripts/run_llm_augment.py --clause 5 --limit 10 --force
```

`--clause`는 5/6/7/8 중 하나만 지정 가능(`all` 없음 — 조항마다 시스템 프롬프트가
달라 한 번에 하나씩 처리). 전체 조항을 다 돌리려면 4번 반복 실행한다:
```bash
for c in 5 6 7 8; do
  python scripts/run_llm_augment.py --clause "$c" --limit 999999
done
```

## 출력

`data/augmented/llm/{source}/{doc_type}/{파일명}_clause{N}.json`:

```json
{
  "source_pdf_path": "data/moe/budget_material/77151_....pdf",
  "source": "moe",
  "doc_type": "budget_material",
  "doc_id": "77151",
  "clause_no": "5",
  "origin_document": {"title": "...", "ordering_agency": "...", "production_date": "..."},
  "selections": [
    {
      "span_id": 78,
      "page_no": 6,
      "clause": "5",
      "original": "▪ 수의계약(2천만원 초과, 5천만원 이하)",
      "synthetic": "▪ 내부예정가(2천180만원 초과, 4천850만원 이하)",
      "transformation": "internal_bid_estimate",
      "reason": "..."
    }
  ]
}
```

`selections`는 LLM이 실제로 채택한 것만 남는다 — 후보로 보냈다고 다 바뀌는 게
아니라, `_passes_validation`(길이비 0.5~1.5배, 원문 잔존 금지)을 통과한 것만
포함된다. 문서 하나가 후보를 갖고 있어도 LLM이 아무것도 안 고르면 selections가
비고 출력 파일 자체가 안 만들어진다(콘솔에 "LLM이 아무것도 선택하지 않음" 출력).

각 selection의 `clause` 필드는 현재 항상 `clause_no`(파일명의 `_clauseN`)와 같은
값이다 — 파일명에만 의존하지 않고 selection 자체로도 조항을 식별할 수 있게 하기
위한 필드다.

## 다음 단계 (이 스크립트 다음)

`data/augmented/llm/...`의 치환 결과는 아직 "이 span을 무엇으로 바꿀지 결정"만 된
상태다. 실제로 원본 PDF에 반영해 완성된 문서를 만드는 건 `scripts/test_reconstruct_pdf.py`
(파일럿 단계, `AUGMENTATION_STATUS.md` "다음에 할 일" 참고)가 담당한다:
```bash
python scripts/test_reconstruct_pdf.py \
  --augmented "data/augmented/llm/moe/budget_material/77151_..._clause5.json"
```
