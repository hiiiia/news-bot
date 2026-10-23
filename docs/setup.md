# news-bot 설정 가이드

보안·IT·AI 소식을 30분마다 모아 텔레그램으로 보내는 봇입니다. GitHub Actions가 실행하므로 내 PC가 꺼져 있어도 동작합니다.

## 동작 방식

1. 30분마다 Actions가 실행됩니다.
2. `config.yaml`에 적힌 소스(CISA KEV, GitHub Advisory, NVD, 주요 프로젝트 릴리스, 보안 뉴스, GeekNews, Hacker News, AI 블로그)를 가져옵니다.
3. `state/seen.json`과 비교해 **새 글만** 추립니다.
4. 실제 악용이 확인된 취약점(KEV), critical 권고문, CVSS 9.0 이상, 긴급 키워드가 들어간 보안 글은 🚨로 먼저 보냅니다.
5. 보낸 글을 `state/seen.json`에 기록하고 저장소에 커밋합니다. Actions는 매번 새 환경이라 이 파일이 "기억" 역할을 합니다.

새로 추가한 소스는 **첫 실행 때 최신 글 1건씩만 미리보기로 전송**하고(`config.yaml`의 `first_run_preview`), 나머지 기존 글은 조용히 기록만 합니다. 두 번째 실행부터 새 글만 옵니다. 미리보기를 끄려면 값을 0으로 두세요.

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

토큰은 코드, 채팅, 이슈 어디에도 붙여 넣지 말고 여기에만 넣으세요.

## 4. 동작 확인

1. Actions 탭 → `news-bot` → Run workflow → **test** 체크 → 실행. 텔레그램에 테스트 메시지가 오면 전송 설정은 끝입니다.
2. 같은 화면에서 test 체크를 빼고 한 번 더 실행합니다. 소스마다 최신 글 1건이 미리보기로 오니, 실제 알림 형태를 여기서 확인할 수 있습니다. 이미 한 번 실행해서 `state/seen.json`에 기록이 남아 있다면, 파일 내용을 `{"seeded": [], "ids": {}}`로 되돌리고 커밋한 뒤 다시 실행하세요.
3. 이후 30분마다 자동 실행됩니다. 실행 로그에서 소스별로 `[ok]`, `[fail]`, `[seed]` 상태를 볼 수 있습니다.

## 로컬에서 시험해 보기

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m bot.main --dry-run   # 전송·저장 없이 새 글만 출력
```

## 사용량과 제한

- 비공개 저장소는 Actions 무료 사용량이 월 2,000분입니다. 30분 주기는 하루 48회, 한 달 약 1,440회이고 실행당 1분으로 계산되면 한도 안에 듭니다. 5분 간격으로 줄이면 한도를 넘으니 주의하세요.
- 스케줄 실행은 정각에 시작하지 않고 몇 분씩 밀리는 경우가 많습니다.
- NVD는 새 CVE에 점수가 늦게 붙는 경우가 있어서, 점수가 아직 없는 CVE는 이 소스에서 걸러집니다. 빠른 감지는 KEV와 GitHub Advisory가 맡습니다.
- 소스 URL은 코드 작성 시점에 접속 검증을 하지 못했습니다. 첫 실행 로그에서 `[fail]`이 뜬 소스는 URL을 `config.yaml`에서 고쳐 주세요. 한 소스가 실패해도 나머지는 정상 동작합니다.
- RSS가 없는 소스(예: Anthropic 뉴스, KISA 보호나라)는 현재 포함되어 있지 않습니다.
