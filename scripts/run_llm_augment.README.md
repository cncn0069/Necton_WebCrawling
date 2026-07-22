# run_llm_augment.py — LLM 텍스트 치환

`find_candidates.py`가 만든 v2 후보를 문서 단위로 묶어 LLM에 보내고, 채택된
원문→합성문 치환 결정을 `data/augmented/llm/`에 저장한다. 원본 파일과 canonical
추출본은 수정하지 않는다.

기본 경로는 세 단계다.

```text
extract_documents.py  -> data/extracted/*.json.gz
find_candidates.py    -> data/candidates/*.jsonl + _manifest.json
run_llm_augment.py     -> data/augmented/llm/*.json
```

`data/annotated/` 사본과 `data/structured/`는 기본 단계가 아니다.

## 실행

실제 호출 없이 프롬프트와 검증 결과만 확인:

```bash
python scripts/run_llm_augment.py --clause 5 --limit 1 --dry-run
```

실제 호출:

```bash
python scripts/run_llm_augment.py --clause 5 --limit 1
python scripts/run_llm_augment.py --clause 5 --limit 10 --force
```

`--clause`는 5/6/7/8 중 하나다. 실제 호출에는 `OPENAI_API_KEY`와 원본 메타데이터
조회를 위한 MariaDB 연결이 필요하다. `--dry-run`은 DB나 API를 호출하지 않는다.

통합 실행:

```bash
python scripts/run_pipeline.py --clause 5 --limit 1 --dry-run
python scripts/run_pipeline.py --clause all --limit 3
python scripts/run_pipeline.py --clause 8 --from-scratch --source all --limit 5
```

## 호출 전 무결성 검사

첫 유료 호출 전에 선택된 batch 전체를 검증한다.

- candidate manifest가 `complete`인지
- 모든 candidate record의 `run_id`가 manifest와 같은지
- `source_path`가 `data/` 밖을 가리키지 않는지
- 원본 SHA-256이 canonical artifact의 `source_sha256`과 같은지
- candidate의 `extraction_id`가 현재 artifact와 같은지
- `line_ids`, page, reconstructed text, `text_sha256`가 모두 같은지

하나라도 다르면 batch를 중단한다. 조사 목적으로 partial 후보를 읽어야 할 때만
`--allow-partial-candidates`를 명시한다. 이 옵션은 불일치를 무시하지 않으며 manifest
status만 허용한다.

기존 증강 JSON을 건너뛸 때도 저장된 extraction ID, candidate run/rule version,
조항과 각 selection의 candidate ID·line IDs·page·text hash를 현재 후보와 대조한다.
오래됐거나 손상된 결과는 자동으로 유료 재호출하지 않고 중단하며, 확인 후 `--force`로
재생성한다.

## 모델 입력

현재 모델에는 다음 두 값만 보낸다.

```json
{"candidate_id": "...", "text": "..."}
```

`line_ids`, bbox, style, extraction/text hash는 로컬 검증과 미래 layout 실험용이며 현재
프롬프트에는 넣지 않는다. 따라서 v2 저장 전환 자체가 모델 판단을 바꾸지는 않는다.

## 출력

원본 확장자를 보존해 같은 stem의 서로 다른 포맷이 충돌하지 않게 한다.

```text
data/augmented/llm/{source}/{doc_type}/sample.pdf.clause5.json
data/augmented/llm/{source}/{doc_type}/sample.hwp.clause5.json
```

```json
{
  "source_path": "data/moe/budget_material/77151_sample.pdf",
  "source": "moe",
  "doc_type": "budget_material",
  "doc_id": "77151",
  "extraction_id": "sha256...",
  "candidate_run_id": "...",
  "candidate_rule_version": "...",
  "clause_no": "5",
  "origin_document": {},
  "selections": [
    {
      "candidate_id": "sha256...",
      "line_ids": [78, 79],
      "page": 6,
      "clause": "5",
      "extraction_id": "sha256...",
      "text_sha256": "sha256...",
      "original": "수의계약 검토 기준액",
      "synthetic": "내부 협상 예정 기준액",
      "transformation": "internal_bid_estimate",
      "reason": "..."
    }
  ]
}
```

LLM이 반환한 ID가 실제 후보에 없거나, 합성문이 길이·원문 잔존 검증을 통과하지
못하면 해당 선택은 버린다. `line_ids`, extraction ID, text hash는 LLM 응답을 믿지
않고 검증된 입력 후보에서 복사한다. 선택이 하나도 없을 때도 현재 run identity와
`selections: []`를 저장하므로, `--force` 재실행 뒤 오래된 선택이 남지 않는다.

PDF에 실제로 반영하는 파일럿은 v2 line bbox를 읽는다.

```bash
python scripts/test_reconstruct_pdf.py \
  --augmented "data/augmented/llm/moe/budget_material/sample.pdf.clause5.json"
```

재구성기는 현재 candidate manifest의 run/rule version과 candidate JSONL까지 다시
검증한다. 새 규칙에서 후보가 0개가 되어 목록에서 사라진 문서의 예전 증강 JSON도
현재 후보에 속하지 않으므로 재사용되지 않는다.
