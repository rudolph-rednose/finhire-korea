# FinHire Korea

한국 금융권의 **공식 자사 채용공고**를 모아 검색하는, 외부 패키지 없는 실행형 MVP입니다.

## 실행

```bash
python3 app.py
```

브라우저에서 `http://127.0.0.1:8000`을 열어 보세요. 목록에는 연결된 공식 공개 채용 피드에서 수집한 실제 공고만 표시됩니다.

## 포함된 기능

- 기업, 직무, 경력, 지역, 고용 형태, 3일 이내 신규 및 7일 내 마감 필터
- 공고 상세 및 공식 지원 페이지 외부 이동
- SQLite 영속 저장소와 공고 표준 스키마
- 기업별 수집 소스 관리 화면 (`/admin`)
- Greenhouse 공개 Job Board API 동기화 및 동일 공고 갱신/중복 제거
- 스케줄러에서 실행할 수 있는 일괄 동기화 명령

```bash
python3 app.py sync
```

## 실제 데이터 연동 운영 방식

관리자에서 기업과 공개 채용 API를 등록하고 동기화합니다. 현재 수집기는 Greenhouse와 국내 Midas Recruiter 공개 피드, 카카오뱅크 및 뱅크샐러드 공식 공개 피드를 지원합니다.

```text
https://boards-api.greenhouse.io/v1/boards/{board-token}/jobs?content=true
```

국내 기업별로 사용하는 ATS/API가 다르므로 `app.py`의 `sync_source()`에 JSON 피드 및 HTML 어댑터를 소스별로 추가하는 방식입니다. 이 분리는 동적 렌더링, 약관, robots.txt 등 기업별 제약을 존중하면서 안정적으로 확장하기 위한 구조입니다. 인증이 필요하거나 수집이 금지된 페이지는 등록하지 마세요.

매일 KST 정오 12시 전체 수집은 macOS `launchd`의 `com.finhire-korea.daily-sync` 작업으로 실행합니다. 각 공식 소스의 고유 공고 수와 DB 저장 수가 일치하는지 자동 검증하며, 불일치나 소스 오류가 있으면 작업을 실패 처리합니다. 수동 실행:

```bash
./sync_daily.sh
```

## 데이터와 중복 처리

`companies`, `job_sources`, `jobs` 테이블로 구성됩니다. 동일 수집 소스는 외부 공고 ID로 업데이트하고, 소스가 달라도 기업명 + 정규화 공고명 + 근무지 해시가 같으면 하나로 합칩니다. `last_seen_at`을 이용해 장기간 보이지 않는 공고를 마감 처리하는 운영 작업을 다음 단계로 추가할 수 있습니다.
