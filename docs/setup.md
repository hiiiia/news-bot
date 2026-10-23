# news-bot 설정 가이드

보안·IT·AI 소식을 30분마다 모아 텔레그램으로 보내는 봇입니다. GitHub Actions가 실행하므로 내 PC가 꺼져 있어도 동작합니다.

## 동작 방식

1. 30분마다 Actions가 실행됩니다.
2. `config.yaml`에 적힌 소스(CISA KEV, GitHub Advisory, NVD, 주요 프로젝트 릴리스, 보안 뉴스, GeekNews, Hacker News, AI 블로그)를 가져옵니다.
3. `state/seen.json`과 비교해 **새 글만** 추립니다.
4. 실제 악용이 확인된 취약점(KEV), critical 권고문, CVSS 9.0 이상, 긴급 키워드가 들어간 보안 글은 🚨로 먼저 보냅니다.
5. 보낸 글을 `state/seen.json`에 기록하고 저장소에 커밋합니다. Actions는 매번 새 환경이라 이 파일이 "기억" 역할을 합니다.

새로 추가한 소스는 **첫 실행 때 최신 글 1건씩만 미리보기로 전송**하고(`config.yaml`의 `first_run_preview`, 전체 상한은 `first_run_preview_max`), 나머지 기존 글은 조용히 기록만 합니다. 두 번째 실행부터 새 글만 옵니다. 미리보기를 끄려면 값을 0으로 두세요.

## 1. 텔레그램 준비

- 봇 토큰: BotFather에서 봇을 만들면 받는 값입니다.
- 채팅 ID: 만든 봇에게 아무 메시지나 보낸 뒤, 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열면 `chat.id` 숫자가 보입니다. 이 주소는 토큰이 들어가니 다른 사람에게 공유하지 마세요.

## 2. 저장소 만들기 (비공개)

```bash
cd ~/Desktop/news-bot
git init
git add .
git commit -m "feat: news-bot 초기 구성"
# GitHub에서 비공개(Private) 저장소를 만든 뒤
git remote add origin <저장소 주소>
git branch -M main
git push -u origin main
```

## 3. Secrets 등록

저장소 → Settings → Secrets and variables → Actions → New repository secret

| 이름 | 값 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 채팅 ID |
| `ANTHROPIC_API_KEY` | Anthropic Console에서 발급한 API 키 (요약 기능용, 선택) |

토큰은 코드, 채팅, 이슈 어디에도 붙여 넣지 말고 여기에만 넣으세요.

## 4. 동작 확인

1. Actions 탭 → `news-bot` → Run workflow → **test** 체크 → 실행. 텔레그램에 테스트 메시지가 오면 전송 설정은 끝입니다.
2. 같은 화면에서 test 체크를 빼고 한 번 더 실행합니다. 소스마다 최신 글 1건이 미리보기로 오니, 실제 알림 형태를 여기서 확인할 수 있습니다. 이미 한 번 실행해서 `state/seen.json`에 기록이 남아 있다면, 파일 내용을 `{"seeded": [], "ids": {}}`로 되돌리고 커밋한 뒤 다시 실행하세요.
3. 이후 30분마다 자동 실행됩니다. 실행 로그에서 소스별로 `[ok]`, `[fail]`, `[seed]` 상태를 볼 수 있습니다.

## 요약 기능 (Claude)

새 글을 모아서 Claude(Haiku)에 한 번에 보내 **한국어 요약, 관련도 판단, 라벨**을 받습니다. 메시지에는 제목 아래 `└ 요약` 줄이 붙습니다.

- **API 키**: Anthropic Console에서 발급해 Secrets에 `ANTHROPIC_API_KEY`로 등록하세요. 채팅이나 코드에는 붙여 넣지 마세요.
- **Max 구독자 크레딧**: Max 5x는 월 $100, Max 20x는 월 $200의 API 크레딧이 포함됩니다. claude.ai → Settings → Billing → API credits에서 Console 조직을 연결(Link organization)하면 되고, 해당 플랜에 가입한 지 7일이 지나야 신청할 수 있습니다. 크레딧은 매 결제 주기에 사라지며 이월되지 않습니다. Pro와 Free는 해당되지 않습니다.
- **크레딧이 떨어지거나 호출이 실패하면** 요약 없이 기존 형식으로 전송됩니다. 알림은 끊기지 않습니다.
- **관련도 판단**: `relevance_filter: false`인 1차 보안 소스(KEV, GitHub Advisory, NVD, 주요 릴리스)는 항상 전송되고 요약만 붙습니다. 뉴스·커뮤니티·탐색 소스는 Claude가 "관련 없음"으로 판단한 글을 보내지 않습니다.
- **탐색형 소스**: `gh-skills`(GitHub 토픽/키워드 검색)와 `hn-ai`(Hacker News 키워드 검색)가 새 에이전트 스킬, MCP 서버, 신모델 소식을 찾아 `[AI-스킬]` 같은 라벨로 보냅니다. 너무 많으면 `min_stars`, `min_points`를 올리세요.
- **연결 점검**: Actions에서 `news-bot`을 **test 체크로 실행**하면 테스트 메시지에 이어 "요약 연결 정상" 또는 "요약 기능을 쓰지 못했어요: 원인"이 텔레그램으로 옵니다.
- **요약이 없을 때**: 실제 실행 중 요약이 실패하면 같은 원인이 텔레그램으로 6시간에 한 번 알려집니다. 자주 보는 원인은 `ANTHROPIC_API_KEY` 미등록, 크레딧 소진·미연결, 키 오타입니다. 실행 로그에서는 `[summary-fail]`을 검색하세요.
- **끄기**: `config.yaml`에서 `summarizer.enabled: false`.
- **안전장치**: 외부 글은 요약 대상 데이터로만 다루고 Claude에게 도구나 권한은 주지 않습니다. 응답에서 URL은 제거하고 라벨은 허용 목록만 받습니다.
- **비용 확인**: Console의 사용량 화면에서 확인하세요. 로컬 `--dry-run`도 키가 환경변수에 있으면 API를 호출합니다.

## 로컬에서 시험해 보기

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m bot.main --dry-run   # 전송·저장 없이 새 글만 출력
```

## 사용량과 제한

- 비공개 저장소는 Actions 무료 사용량이 월 2,000분입니다. 30분 주기는 하루 48회, 한 달 약 1,440회이고 실행당 1분으로 계산되면 한도 안에 듭니다. 5분 간격으로 줄이면 한도를 넘으니 주의하세요.
- GitHub의 스케줄 실행은 **최선 노력**이라서 몇 분에서 몇 시간까지 밀리거나 가끔 누락될 수 있고, 정각·30분 근처에서 특히 심합니다. 그래서 크론을 `7,37 * * * *`(UTC)로 정각을 피해 두었습니다. 지연이 있어도 글을 놓치지 않도록 NVD는 최근 12시간을 보고(`lookback_hours`), 나머지 소스는 중복을 기록으로 걸러냅니다.
- 실행이 안 도는 것 같으면 Actions 탭에서 `news-bot`의 이벤트가 `schedule`인 실행의 시각을 확인하세요. 워크플로우가 기본 브랜치에 있는지, 비활성화되지 않았는지, 월 무료 사용량(비공개 저장소 2,000분)이 남았는지도 봅니다. 정확한 간격이 꼭 필요하면 외부 스케줄러가 `workflow_dispatch`를 호출하게 할 수도 있지만, 저장소 접근 토큰을 외부 서비스에 맡겨야 합니다.
- NVD는 새 CVE에 점수가 늦게 붙는 경우가 있어서, 점수가 아직 없는 CVE는 이 소스에서 걸러집니다. 빠른 감지는 KEV와 GitHub Advisory가 맡습니다.
- 소스 주소 중 `config.yaml`에 `[확인]`이라고 적힌 것은 설정을 만들 때 실제로 열어 본 것이고, `[미확인]`과 GitHub 릴리스 피드는 열어 보지 못했습니다. 첫 실행 로그에서 `[fail]`이 뜬 소스는 주소를 고치거나 지우세요. 한 소스가 실패해도 나머지는 정상 동작합니다.
- 소스는 동시에 가져오므로 소스가 50개 안팎이어도 실행 시간은 크게 늘지 않습니다. 첫 실행에는 새 소스가 많아서 미리보기가 몰리지 않도록 전체 상한(`first_run_preview_max`, 기본 8건)을 둡니다.
- **포함하지 않은 소스**: Anthropic 뉴스(공식 RSS 없음), 보안뉴스와 KISA 보호나라(RSS 주소를 확인하지 못함), Lobsters(로봇 접근을 허용하지 않음), 네이버 D2, Cloudflare·AWS 상황판(예정된 점검까지 올라와서 잡음이 많음), MS 보안 업데이트와 Ubuntu 보안 공지(한 번에 수십 건이 올라옴). 필요하면 `config.yaml`에 추가하세요.
- 릴리스 소스는 `relevance_filter: false`라서 새 버전이 나오면 항상 전송됩니다. 너무 잦은 프로젝트는 이 줄을 지우면 Claude가 사소한 릴리스를 거릅니다.
