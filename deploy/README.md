# AWS EC2 배포 노트

## 1. 서버 준비
```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv
sudo useradd -m -s /bin/bash rd2
sudo mkdir -p /opt/rd2 && sudo chown rd2:rd2 /opt/rd2
```

## 2. 코드 + 의존성
```bash
# 코드를 /opt/rd2로 복사(scp 또는 git clone)한 뒤:
cd /opt/rd2
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

## 3. gstack browse 바이너리 (별도 설치 — pip으로 안 깔림)
`src/rd2/adapters/browse_client.py`가 서브프로세스로 호출하는 헤드리스 브라우저
CLI. 이게 없으면 정보공개포털/PRISM 어댑터 둘 다 전혀 동작하지 않는다
(`_browse_binary()`가 `RuntimeError`로 즉시 실패). gstack의 browse 스킬
setup 스크립트로 설치하고, `~/.claude/skills/gstack/browse/dist/browse`
(또는 `PATH`)에 위치시킬 것.

## 4. systemd 서비스 등록
```bash
sudo cp rd2-crawler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rd2-crawler
sudo journalctl -u rd2-crawler -f   # 로그 확인
```

죽었다 재시작해도 `<db경로>.prism_checkpoint.json` 덕분에 처음부터 다시
돌지 않는다(`scripts/collect_prism.py` 참고).

## 미포함 (별도 후속 작업)
- Dockerfile/컨테이너화 — 지금은 systemd + venv로 충분한 규모.
- CI(테스트 자동 실행) — 로컬 `pytest` 통과만 확인하고 배포.
- 여러 인스턴스 동시 실행 — DocumentStore가 단일 프로세스 순차 실행을 전제로
  설계됨(`storage/files.py` 참고), 병렬 실행하려면 별도 설계 필요.
- **S3 DB 백업**: `rd2_prod.db`를 주기적으로 압축해 S3에 스냅샷 업로드하는 스크립트.
  SQLite를 S3에서 직접 마운트해 쓰는 방식은 파일 잠금 문제로 깨지므로 사용하지 않는다 —
  로컬(EBS)에서 실행 중인 DB의 스냅샷만 S3로 백업한다. AWS 계정/S3 버킷이 준비된 뒤 구현.
- **robots.txt/이용약관/속도제한 점검**: 로컬 PC 수동 실행 때와 달리 EC2 상시 실행은
  클라우드 IP 대역에서 지속적으로 정부 사이트에 접근하므로, systemd 서비스를 켜기 전에
  PRISM·정보공개포털의 robots.txt와 이용약관을 확인하고 요청 간격을 조정해야 한다
  (`TODOS.md` "EC2 상시 크롤링 전 robots.txt/이용약관/속도제한 점검" 항목).
