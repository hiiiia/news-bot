# news-bot

보안·IT·AI 이슈를 1차 소스(CISA KEV, GitHub Advisory, NVD, 주요 프로젝트 릴리스 등)에서 모아 텔레그램으로 보내는 봇. GitHub Actions에서 30분마다 실행됩니다.

- 설정과 사용법: [docs/setup.md](docs/setup.md)
- 소스·키워드 수정: `config.yaml`
- 보낸 글 기록: `state/seen.json` (Actions가 자동 커밋)
