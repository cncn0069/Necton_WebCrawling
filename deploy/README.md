# AWS EC2 배포 노트

## 1. 서버 준비
```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv
sudo useradd -m -s /bin/bash rd2
sudo mkdir -p /opt/rd2 && sudo chown rd2:rd2 /opt/rd2
```

## 2. 코드 + 의존성
```bash
# 코드를 /opt/rd2로 복사(scp 또는 git clone)한 뒤:
cd /opt/rd2
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

## 3. DB 접속정보 (.env)
저장 계층은 MariaDB(RDS)를 본다(`src/rd2/storage/db.py`, 2026-07-09 SQLite→MariaDB 전환).
`/opt/rd2/.env`에 RDS 엔드포인트를 채워넣을 것 — 로컬 개발용 `.env.example`과 키 이름은
동일하고 값만 RDS로 바꾸면 된다:
```
MARIADB_HOST=<RDS 엔드포인트>
MARIADB_PORT=3306
MARIADB_USER=<RDS 계정>
MARIADB_PASSWORD=<RDS 비밀번호>
MARIADB_DATABASE=<DB 이름>
```
RDS는 퍼블릭 접근 불가로 두고, EC2 보안그룹만 RDS 보안그룹의 인바운드(3306)를
허용하도록 구성할 것.

## 4. gstack browse 바이너리 (별도 설치 — pip으로 안 깔림)
`src/rd2/adapters/browse_client.py`가 서브프로세스로 호출하는 헤드리스 브라우저
CLI. 이게 없으면 정보공개포털/PRISM 어댑터 둘 다 전혀 동작하지 않는다
(`_browse_binary()`가 `RuntimeError`로 즉시 실패). gstack의 browse 스킬
setup 스크립트로 설치하고, `~/.claude/skills/gstack/browse/dist/browse`
(또는 `PATH`)에 위치시킬 것.

## 5. systemd 서비스 등록
```bash
sudo cp rd2-crawler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rd2-crawler
sudo journalctl -u rd2-crawler -f   # 로그 확인
```

죽었다 재시작해도 `rd2.db.prism_checkpoint.json`(고정 파일명) 덕분에 처음부터 다시
돌지 않는다(`scripts/collect_prism.py` 참고). 이 파일은 DB와 무관하게 EC2 로컬
디스크에 남는 진행 상태이므로 인스턴스가 사라지면 같이 사라진다 — EBS 스냅샷 등에
포함시킬 것.

## 미포함 (별도 후속 작업)
- Dockerfile/컨테이너화 — 지금은 systemd + venv로 충분한 규모.
- CI(테스트 자동 실행) — 로컬 `pytest` 통과만 확인하고 배포.
- 여러 인스턴스 동시 실행 — MariaDB 자체는 동시 접속을 지원하지만, 같은 소스를 여러
  프로세스가 동시에 돌리면 체크포인트 파일(`rd2.db.{source}_checkpoint.json`)을 서로
  덮어써 진행 위치가 꼬인다(`storage/files.py`도 단일 프로세스 순차 저장 전제). 소스가
  다르면(molit vs mohw 등) 체크포인트 파일이 분리돼 있어 병렬 실행 가능.
- **RDS 백업**: RDS는 자동 스냅샷/백업 보관 기간을 콘솔에서 설정할 수 있어 별도 백업
  스크립트가 필요 없다(SQLite 시절엔 파일을 직접 S3로 복사해야 했지만 더 이상 아님).
  AWS 계정/RDS 인스턴스가 준비되면 백업 보관 기간만 설정할 것.
- **robots.txt/이용약관/속도제한 점검**: 로컬 PC 수동 실행 때와 달리 EC2 상시 실행은
  클라우드 IP 대역에서 지속적으로 정부 사이트에 접근하므로, systemd 서비스를 켜기 전에
  PRISM·정보공개포털의 robots.txt와 이용약관을 확인하고 요청 간격을 조정해야 한다
  (`TODOS.md` "EC2 상시 크롤링 전 robots.txt/이용약관/속도제한 점검" 항목).
