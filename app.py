#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FinHire Korea — dependency-free MVP for official financial-sector job search."""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import urllib.request
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = Path(__file__).parent
DB_PATH = ROOT / "finhire.db"
TODAY = dt.date.today()

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS companies (
 id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, industry TEXT NOT NULL,
 website TEXT, logo_initials TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS job_sources (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
 name TEXT NOT NULL, source_type TEXT NOT NULL CHECK(source_type IN ('greenhouse','json_feed','html')),
 endpoint TEXT NOT NULL, config_json TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
 last_synced_at TEXT, last_status TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
 source_id INTEGER REFERENCES job_sources(id) ON DELETE SET NULL,
 external_id TEXT, title TEXT NOT NULL, original_title TEXT, job_category TEXT NOT NULL,
 experience TEXT NOT NULL DEFAULT '경력 무관', location TEXT NOT NULL DEFAULT '미정',
 employment_type TEXT NOT NULL DEFAULT '정규직', description TEXT NOT NULL DEFAULT '',
 requirements TEXT NOT NULL DEFAULT '', deadline TEXT, posted_at TEXT NOT NULL,
 source_url TEXT NOT NULL, canonical_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(source_id, external_id), UNIQUE(canonical_key)
);
CREATE INDEX IF NOT EXISTS idx_jobs_filter ON jobs(status, job_category, location, employment_type, posted_at);
CREATE TABLE IF NOT EXISTS favorites (
 job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DEMO_JOBS = [
 ("토스", "핀테크", "Product Manager (Payments)", "PM/PO", "경력 5~10년", "서울 · 강남구", "정규직", "결제 경험을 만드는 프로덕트 매니저를 찾습니다.", "금융 또는 플랫폼 서비스 기획 경험", 14, "https://toss.im/career/jobs"),
 ("카카오뱅크", "은행", "서비스 기획자 · 여신", "서비스기획", "경력 3~8년", "경기 · 성남시", "정규직", "여신 고객 경험을 설계합니다.", "금융 서비스 기획 경력 3년 이상", 7, "https://recruit.kakaobank.com/"),
 ("뱅크샐러드", "핀테크", "Data Analyst", "데이터", "경력 3년 이상", "서울 · 영등포구", "정규직", "금융 데이터로 고객 가치를 만듭니다.", "SQL과 분석 경험", 21, "https://careers.banksalad.com/"),
 ("현대카드", "카드", "디지털 서비스 기획", "서비스기획", "경력 5년 이상", "서울 · 영등포구", "정규직", "카드 고객의 디지털 여정을 설계합니다.", "서비스 기획 및 프로젝트 리딩 경험", 10, "https://www.hyundaicard.com/cpc/ca/ca0101.hdc"),
 ("삼성화재", "보험", "디지털 채널 UX 기획", "UX/UI", "경력 3~7년", "서울 · 서초구", "정규직", "보험 가입 경험을 개선할 UX 기획자를 모집합니다.", "UX 기획 또는 프로덕트 경험", 30, "https://www.samsungfire.com/recruit/"),
 ("KB국민은행", "은행", "기업금융 RM", "금융/영업", "경력 2년 이상", "서울", "계약직", "기업고객 금융 솔루션을 제안합니다.", "금융기관 또는 기업금융 경험", 3, "https://kbfg.com/kr/recruit/recruit.jsp"),
 ("한국투자증권", "증권", "MTS 서비스 운영", "서비스기획", "경력 3~6년", "서울 · 영등포구", "정규직", "모바일 투자 서비스 운영과 개선을 담당합니다.", "증권 또는 핀테크 서비스 경험", 18, "https://www.truefriend.com/main/customer/recruit.jsp"),
 ("케이뱅크", "은행", "Backend Engineer", "개발", "경력 3년 이상", "서울 · 중구", "정규직", "안전한 인터넷뱅킹 플랫폼을 개발합니다.", "Java 또는 Kotlin 기반 서버 개발", 12, "https://kbanknow.com/ib20/mnu/FPMCRG050000"),
]

# 2026-07-22에 HTTP 200 응답을 확인한 공식 채용 페이지. 공고 데이터가 아닌
# 소스 레지스트리이며, 각 페이지 전용 수집기를 구현하기 전까지 공고로 노출하지 않는다.
VERIFIED_SOURCES = [
 ("토스", "핀테크", "토스 공식 채용", "https://toss.im/career"),
 ("카카오뱅크", "은행", "카카오뱅크 인재영입", "https://recruit.kakaobank.com/"),
 ("뱅크샐러드", "핀테크", "뱅크샐러드 채용", "https://corp.banksalad.com/jobs/"),
 ("현대카드", "카드", "현대카드·현대커머셜 인재모집", "https://careerhyundai.recruiter.co.kr/"),
 ("삼성화재", "보험", "삼성채용", "https://www.samsungcareers.com/"),
 ("케이뱅크", "은행", "케이뱅크 채용", "https://recruit.kbanknow.com/"),
]

# 공식 채용 페이지에서 사용하는 Midas Recruiter 공개 공고 피드.
# 피드 응답과 상세 URL을 확인한 회사만 활성 수집 소스로 등록한다.
RECRUITER_SOURCES = [
 ("신한은행", "은행", "신한은행 채용", "https://shinhan.recruiter.co.kr"),
 ("하나은행", "은행", "하나은행 채용", "https://hanabank.recruiter.co.kr"),
 ("우리은행", "은행", "우리은행 채용", "https://wooribank.recruiter.co.kr"),
 ("NH농협은행", "은행", "NH농협은행 채용", "https://nhbank.recruiter.co.kr"),
 ("하나카드", "카드", "하나카드 채용", "https://hanacard.recruiter.co.kr"),
 ("우리카드", "카드", "우리카드 채용", "https://wooricard.recruiter.co.kr"),
 ("KB국민카드", "카드", "KB국민카드 채용", "https://kbcard.recruiter.co.kr"),
 ("NH투자증권", "증권", "NH투자증권 채용", "https://nhqv.recruiter.co.kr"),
 ("한국투자증권", "증권", "한국투자증권 채용", "https://koreainvestment.recruiter.co.kr"),
 ("미래에셋증권", "증권", "미래에셋증권 채용", "https://miraeasset.recruiter.co.kr"),
 ("KB증권", "증권", "KB증권 채용", "https://kbsec.recruiter.co.kr"),
 ("KB손해보험", "보험", "KB손해보험 채용", "https://kbinsure.recruiter.co.kr"),
 ("하나생명", "보험", "하나생명 채용", "https://hanalife.recruiter.co.kr"),
 ("메리츠금융", "금융", "메리츠금융 채용", "https://meritz.recruiter.co.kr"),
]

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con

def canonical(company, title, location):
    return hashlib.sha256(f"{company}|{re.sub(r'[^a-z0-9가-힣]', '', title.lower())}|{location}".encode()).hexdigest()

def init_db():
    con = db(); con.executescript(SCHEMA)
    # 같은 기본 소스가 재시작마다 중복 등록되지 않도록 기존 중복을 정리하고 제약을 둔다.
    con.execute("""DELETE FROM job_sources WHERE id NOT IN (
      SELECT MIN(id) FROM job_sources GROUP BY company_id, name
    )""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_job_sources_company_name ON job_sources(company_id, name)")
    # 이전 MVP의 예시 공고는 실제 연동 데이터가 아니므로 항상 제거한다.
    con.execute("DELETE FROM jobs WHERE external_id LIKE 'demo-%'")
    for name, industry, source_name, endpoint in VERIFIED_SOURCES:
        con.execute("INSERT OR IGNORE INTO companies(name, industry, website, logo_initials) VALUES(?,?,?,?)", (name, industry, endpoint, name[:2]))
        company_id = con.execute("SELECT id FROM companies WHERE name=?", (name,)).fetchone()[0]
        con.execute("""INSERT OR IGNORE INTO job_sources(company_id,name,source_type,endpoint,enabled,last_synced_at,last_status)
          VALUES(?,?,?,?,0,CURRENT_TIMESTAMP,'공식 페이지 확인 완료 · 전용 수집기 연결 대기')""", (company_id, source_name, "html", endpoint))
        con.execute("UPDATE job_sources SET enabled=0,last_status='공식 페이지 확인 완료 · 전용 수집기 연결 대기' WHERE company_id=? AND name=? AND source_type='html'", (company_id, source_name))
    for name, industry, source_name, base_url in RECRUITER_SOURCES:
        endpoint = base_url + "/app/jobnotice/list.json"
        con.execute("INSERT OR IGNORE INTO companies(name, industry, website, logo_initials) VALUES(?,?,?,?)", (name, industry, base_url, name[:2]))
        company_id = con.execute("SELECT id FROM companies WHERE name=?", (name,)).fetchone()[0]
        config = json.dumps({"adapter":"recruiter_midas", "base_url":base_url}, ensure_ascii=False)
        con.execute("""INSERT INTO job_sources(company_id,name,source_type,endpoint,config_json,enabled,last_status)
          VALUES(?,?,?,?,?,1,'공식 공개 공고 피드 연결됨')
          ON CONFLICT(company_id,name) DO UPDATE SET source_type=excluded.source_type,endpoint=excluded.endpoint,
          config_json=excluded.config_json,enabled=1""", (company_id, source_name, "json_feed", endpoint, config))
    # 하나은행의 현재 공고는 career.co.kr 기반 별도 채용관에서 운영된다.
    hana_bank_id = con.execute("SELECT id FROM companies WHERE name='하나은행'").fetchone()[0]
    hana_bank_url = "https://hanabankhr.career.co.kr/jobs/"
    con.execute("UPDATE companies SET website=? WHERE id=?", (hana_bank_url, hana_bank_id))
    con.execute("""UPDATE job_sources SET source_type='html',endpoint=?,
      config_json='{"adapter":"hana_bank_career"}',enabled=1,
      last_status='하나은행 신규 공식 채용 목록 연결됨'
      WHERE company_id=? AND name='하나은행 채용'""", (hana_bank_url, hana_bank_id))
    # 뱅크샐러드는 공식 채용 사이트가 사용하는 공개 Greeting 공고 피드를 제공한다.
    banksalad_id = con.execute("SELECT id FROM companies WHERE name='뱅크샐러드'").fetchone()[0]
    con.execute("""UPDATE job_sources SET source_type='json_feed',
      endpoint='https://www.banksalad.com/proxy/api/greeting/openings',
      config_json='{"adapter":"banksalad_openings"}', enabled=1,
      last_status='공식 공개 공고 피드 연결됨'
      WHERE company_id=? AND name='뱅크샐러드 채용'""", (banksalad_id,))
    kbank_id = con.execute("SELECT id FROM companies WHERE name='케이뱅크'").fetchone()[0]
    con.execute("""UPDATE job_sources SET source_type='json_feed',
      endpoint='https://kbank.recruiter.co.kr/app/jobnotice/list.json',
      config_json='{"adapter":"recruiter_midas","base_url":"https://kbank.recruiter.co.kr"}', enabled=1,
      last_status='공식 공개 공고 피드 연결됨'
      WHERE company_id=? AND name='케이뱅크 채용'""", (kbank_id,))
    kakaobank_id = con.execute("SELECT id FROM companies WHERE name='카카오뱅크'").fetchone()[0]
    con.execute("""UPDATE job_sources SET source_type='json_feed',
      endpoint='https://recruit.kakaobank.com/api/recruits',
      config_json='{"adapter":"kakaobank_recruits"}', enabled=1,
      last_status='공식 공개 공고 API 연결됨'
      WHERE company_id=? AND name='카카오뱅크 인재영입'""", (kakaobank_id,))
    toss_id = con.execute("SELECT id FROM companies WHERE name='토스'").fetchone()[0]
    con.execute("""UPDATE job_sources SET source_type='json_feed',
      endpoint='https://api-public.toss.im/api/v3/ipd-eggnog/career/job-groups',
      config_json='{"adapter":"toss_job_groups"}', enabled=1,
      last_status='토스 공식 커리어 공개 공고 API 연결됨'
      WHERE company_id=? AND name='토스 공식 채용'""", (toss_id,))
    hyundai_id = con.execute("SELECT id FROM companies WHERE name='현대카드'").fetchone()[0]
    con.execute("""UPDATE job_sources SET source_type='json_feed',
      endpoint='https://careerhyundai.recruiter.co.kr/app/jobnotice/list.json',
      config_json='{"adapter":"recruiter_midas","base_url":"https://careerhyundai.recruiter.co.kr"}', enabled=1,
      last_status='현대카드·현대커머셜 공식 공개 공고 피드 연결됨'
      WHERE company_id=? AND name='현대카드·현대커머셜 인재모집'""", (hyundai_id,))
    samsung_fire_id = con.execute("SELECT id FROM companies WHERE name='삼성화재'").fetchone()[0]
    con.execute("""UPDATE job_sources SET source_type='html',
      endpoint='https://www.samsungcareers.com/hr/list.data',
      config_json='{"adapter":"samsung_careers","company_code":"E21"}', enabled=1,
      last_status='삼성커리어스 공식 공고 목록 연결됨'
      WHERE company_id=? AND name='삼성채용'""", (samsung_fire_id,))
    # 신한투자증권 자체 채용 사이트의 공개 HTML 목록.
    con.execute("INSERT OR IGNORE INTO companies(name, industry, website, logo_initials) VALUES(?,?,?,?)",
                ("신한투자증권", "증권", "https://recruit.shinhaninvest.com/recruit/list.do", "신한"))
    shinhan_invest_id = con.execute("SELECT id FROM companies WHERE name='신한투자증권'").fetchone()[0]
    con.execute("""INSERT INTO job_sources(company_id,name,source_type,endpoint,config_json,enabled,last_status)
      VALUES(?,?,?,?,?,1,'신한투자증권 공식 채용 목록 연결됨')
      ON CONFLICT(company_id,name) DO UPDATE SET source_type=excluded.source_type,endpoint=excluded.endpoint,
      config_json=excluded.config_json,enabled=1""", (shinhan_invest_id, "신한투자증권 채용", "html",
      "https://recruit.shinhaninvest.com/recruit/list.do", '{"adapter":"shinhan_invest"}'))
    # 신한카드 홈페이지가 화면에서 직접 호출하는 공식 채용공고 API.
    con.execute("INSERT OR IGNORE INTO companies(name, industry, website, logo_initials) VALUES(?,?,?,?)",
                ("신한카드", "카드", "https://www.shinhancard.com/mob/MOBFMCOMN/MOBFMCOM01.shc", "신한"))
    shinhan_card_id = con.execute("SELECT id FROM companies WHERE name='신한카드'").fetchone()[0]
    con.execute("""INSERT INTO job_sources(company_id,name,source_type,endpoint,config_json,enabled,last_status)
      VALUES(?,?,?,?,?,1,'신한카드 공식 채용공고 API 연결됨')
      ON CONFLICT(company_id,name) DO UPDATE SET source_type=excluded.source_type,endpoint=excluded.endpoint,
      config_json=excluded.config_json,enabled=1""", (shinhan_card_id, "신한카드 채용", "json_feed",
      "https://www.shinhancard.com/mob/MOBFMCOMN/MOBFMCOM01.ajax", '{"adapter":"shinhan_card"}'))
    # 삼성 금융 관계사는 삼성커리어스의 회사 코드별 공식 공고 목록을 사용한다.
    for company_name, industry, company_code in [
        ("삼성생명", "보험", "E11"), ("삼성카드", "카드", "E31"), ("삼성증권", "증권", "E40")
    ]:
        website = f"https://www.samsungcareers.com/subsid/detail/{company_code}"
        con.execute("INSERT OR IGNORE INTO companies(name, industry, website, logo_initials) VALUES(?,?,?,?)",
                    (company_name, industry, website, company_name[:2]))
        company_id = con.execute("SELECT id FROM companies WHERE name=?", (company_name,)).fetchone()[0]
        con.execute("""INSERT INTO job_sources(company_id,name,source_type,endpoint,config_json,enabled,last_status)
          VALUES(?,?,?,?,?,1,'삼성커리어스 공식 공고 목록 연결됨')
          ON CONFLICT(company_id,name) DO UPDATE SET source_type=excluded.source_type,endpoint=excluded.endpoint,
          config_json=excluded.config_json,enabled=1""", (company_id, f"{company_name} 채용", "html",
          "https://www.samsungcareers.com/hr/list.data", json.dumps({"adapter":"samsung_careers", "company_code":company_code})))
    # 카카오페이 공식 Greeting 채용 페이지와 네이버파이낸셜 자체 채용 목록.
    for company_name, website, endpoint, adapter in [
        ("카카오페이", "https://kakaopay.career.greetinghr.com/ko/main", "https://kakaopay.career.greetinghr.com/ko/main", "greeting_html"),
        ("네이버페이", "https://recruit.naverfincorp.com/rcrt/list.do", "https://recruit.naverfincorp.com/rcrt/loadJobList.do?firstIndex=0", "naver_financial")
    ]:
        con.execute("INSERT OR IGNORE INTO companies(name, industry, website, logo_initials) VALUES(?,?,?,?)",
                    (company_name, "핀테크", website, company_name[:2]))
        company_id = con.execute("SELECT id FROM companies WHERE name=?", (company_name,)).fetchone()[0]
        con.execute("""INSERT INTO job_sources(company_id,name,source_type,endpoint,config_json,enabled,last_status)
          VALUES(?,?,?,?,?,1,'공식 채용 목록 연결됨')
          ON CONFLICT(company_id,name) DO UPDATE SET source_type=excluded.source_type,endpoint=excluded.endpoint,
          config_json=excluded.config_json,enabled=1""", (company_id, f"{company_name} 채용", "html", endpoint,
          json.dumps({"adapter":adapter}, ensure_ascii=False)))
    con.execute("""DELETE FROM companies
      WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.company_id=companies.id)
      AND NOT EXISTS (SELECT 1 FROM job_sources WHERE job_sources.company_id=companies.id)""")
    con.commit()
    con.close()

def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "FinHireKorea/0.1 (official-job-index; contact: admin@example.com)"})
    with urllib.request.urlopen(request, timeout=18) as response:
        return json.loads(response.read().decode("utf-8"))

def fetch_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FinHire Korea official-job-index)"})
    with urllib.request.urlopen(request, timeout=18) as response:
        return response.read().decode("utf-8", "replace")

def fetch_korean_legacy_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FinHire Korea official-job-index)"})
    with urllib.request.urlopen(request, timeout=18) as response:
        raw = response.read()
    # career.co.kr의 구형 ASP 채용관은 EUC-KR로 서비스된다.
    return raw.decode("euc-kr", "replace")

def fetch_form_json(url, values):
    payload = urlencode(values).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"User-Agent": "FinHireKorea/0.1 (official-job-index; contact: admin@example.com)", "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=18) as response:
        return json.loads(response.read().decode("utf-8"))

def fetch_form_text(url, values):
    payload = urlencode(values, doseq=True).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"User-Agent": "Mozilla/5.0 (FinHire Korea official-job-index)", "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(request, timeout=18) as response:
        return response.read().decode("utf-8", "replace")

def fetch_post_json(url, values, headers=None):
    payload = json.dumps(values).encode("utf-8")
    request_headers = {"User-Agent": "FinHireKorea/0.1 (official-job-index; contact: admin@example.com)", "Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=payload, headers=request_headers)
    with urllib.request.urlopen(request, timeout=18) as response:
        return json.loads(response.read().decode("utf-8"))

def fetch_jobflex_records(base_url):
    """Read the current Jobflex builder feed used by newer recruiter.co.kr sites.

    The builder feed also retains closed announcements.  Only IN_SUBMISSION rows
    whose explicit deadline has not passed are safe to expose as open jobs.
    """
    hostname = urlparse(base_url).netloc
    payload = {
        "pageableRq": {"page": 1, "size": 500, "sort": ["START_DATE_DESC"]},
        "filter": {
            "keyword": "", "tagSnList": [], "jobGroupSnList": [],
            "careerTypeList": [], "regionSnList": [],
            "submissionStatusList": [], "openStatusList": [],
            "resumeLanguageTypeList": ["KOR"],
        },
    }
    data = fetch_post_json(
        "https://api-recruiter.recruiter.co.kr/position/v1/jobflex",
        payload, {"prefix": hostname},
    )
    records = []
    for item in data.get("list", []):
        if item.get("submissionStatus") != "IN_SUBMISSION":
            continue
        deadline = (item.get("endDateTime") or "")[:10] or None
        if deadline and deadline < TODAY.isoformat():
            continue
        career = {"NEW": "신입", "CAREER": "경력", "NEW_CAREER": "신입/경력"}.get(item.get("careerType"), "경력/신입 공고 상세 확인")
        records.append({
            "id": f"jobflex-{item['positionSn']}",
            "title": item["title"].strip(),
            "location": "대한민국",
            "url": f"{base_url}/career/jobs/{item['positionSn']}",
            "description": f"{item.get('classificationCode') or '채용'} · 공식 채용공고",
            "experience": career,
            "employment": "고용형태 확인",
            "category": item.get("classificationCode"),
            "deadline": deadline,
            "posted_at": (item.get("startDateTime") or TODAY.isoformat())[:10],
        })
    return records

def map_category(title):
    t = title.lower()
    pairs = [("PM/PO", ["product manager", " pm", "po", "프로덕트"]), ("서비스기획", ["기획", "service planning"]), ("개발", ["engineer", "developer", "개발"]), ("데이터", ["data", "데이터", "analyst"]), ("UX/UI", ["ux", "ui", "design", "디자인"]), ("금융/영업", ["rm", "영업", "금융"])]
    return next((name for name, words in pairs if any(w in t for w in words)), "기타")

def normalize_category(raw_category, title):
    text = f"{raw_category or ''} {title}".lower()
    groups = [
        ("기획/PM", ["product", "pm", " po", "기획", "strategy", "전략"]),
        ("데이터/AI", ["data", "데이터", "analyst", "analysis", " ml", "ai", "r&d"]),
        ("개발", ["engineer", "developer", "backend", "server", "app", "개발", "engineering"]),
        ("디자인/UX", ["design", " ux", " ui", "디자인"]),
        ("보안/IT", ["security", "infra", "device", "it ", "정보보호", "보안", "qa"]),
        ("금융/투자", ["finance", "bank", "securities", "insurance", "금융", "투자", "자금", "연금", "리스크", "risk", "aml"]),
        ("영업/고객", ["sales", "customer", "service", "영업", "텔러", "상담", "고객"]),
        ("마케팅/홍보", ["marketing", "brand", "contents", " pr", "마케팅", "브랜드", "홍보"]),
        ("경영지원/인사", ["hr", "people", "recruit", "accounting", "경영", "인사", "채용", "총무", "ga"]),
        ("법무/컴플라이언스", ["legal", "compliance", "법무", "준법"]),
    ]
    return next((group for group, words in groups if any(word in text for word in words)), "기타")

def sync_source(source_id):
    con = db(); source = con.execute("""SELECT s.*, c.name company FROM job_sources s JOIN companies c ON c.id=s.company_id WHERE s.id=?""", (source_id,)).fetchone()
    if not source: con.close(); raise ValueError("소스를 찾을 수 없습니다.")
    count = 0
    try:
        config = json.loads(source["config_json"] or "{}")
        if source["source_type"] == "greenhouse":
            data = fetch_json(source["endpoint"])
            records = [{"id": item["id"], "title": item["title"].strip(), "location": item.get("location", {}).get("name") or "미정", "url": item.get("absolute_url", source["endpoint"]), "description": item.get("content", ""), "experience": "경력 무관", "employment": "정규직"} for item in data.get("jobs", [])]
        elif source["source_type"] == "json_feed" and config.get("adapter") == "banksalad_openings":
            data = fetch_json(source["endpoint"])
            records = []
            for department in data.get("jobs", []):
                for item in department.get("data", []):
                    career = item.get("careerInfo") or {}
                    experience = f"경력 {career.get('from')}년 이상" if career.get("type") == "EXPERIENCED" and career.get("from") else "경력 무관"
                    records.append({"id": item["id"], "title": item["title"].strip(), "location": "서울", "url": item["url"], "description": f"{department.get('department', '기타')} 직군 · {item.get('job', '')}", "experience": experience, "employment": item.get("employmentType") or "고용형태 확인", "category": department.get("department")})
        elif source["source_type"] == "json_feed" and config.get("adapter") == "recruiter_midas":
            data = fetch_form_json(source["endpoint"], {"recruitClassSn":"", "recruitClassName":"", "jobnoticeStateCode":"10", "pageSize":"100", "searchByNameOnly":"true", "currentPage":"1"})
            records = []
            for item in data.get("list", []):
                if item.get("receiptState") and item.get("receiptState") != "접수중":
                    continue
                deadline_ms = (item.get("applyEndDate") or {}).get("time")
                deadline = dt.datetime.fromtimestamp(deadline_ms / 1000, tz=dt.timezone(dt.timedelta(hours=9))).date().isoformat() if deadline_ms else None
                start_ms = (item.get("applyStartDate") or {}).get("time")
                posted_at = dt.datetime.fromtimestamp(start_ms / 1000, tz=dt.timezone(dt.timedelta(hours=9))).date().isoformat() if start_ms else TODAY.isoformat()
                base_url = config.get("base_url") or (urlparse(source["endpoint"]).scheme + "://" + urlparse(source["endpoint"]).netloc)
                detail_url = f"{base_url}/app/jobnotice/view?systemKindCode={item.get('systemKindCode','MRS2')}&jobnoticeSn={item['jobnoticeSn']}"
                records.append({"id": item["jobnoticeSn"], "title": item["jobnoticeName"].strip(), "location": "대한민국", "url": detail_url, "description": f"{item.get('recruitClassName', '기타')} · {item.get('recruitTypeName', '')}", "experience": "경력/신입 공고 상세 확인", "employment": "고용형태 확인", "category": item.get("recruitClassName"), "deadline": deadline, "posted_at": posted_at})
            # recruiter.co.kr의 신형 빌더는 구형 list.json과 별도 저장소를
            # 사용한다. 둘을 병합해야 메인 화면에만 보이던 신규 공고도 잡힌다.
            # 아직 이전하지 않은 도메인은 신형 API가 400을 반환하므로 무시한다.
            try:
                modern = fetch_jobflex_records(config.get("base_url"))
            except Exception:
                modern = []
            known_ids = {str(item["id"]) for item in records}
            records_by_title = {re.sub(r"[^a-z0-9가-힣]", "", item["title"].lower()): item for item in records}
            # 같은 공고가 양쪽 피드에 있으면 외부 ID는 유지하되 링크와 메타
            # 데이터는 실제로 동작하는 신형 상세 페이지 값으로 갱신한다.
            for item in modern:
                title_key = re.sub(r"[^a-z0-9가-힣]", "", item["title"].lower())
                legacy = records_by_title.get(title_key)
                if legacy:
                    legacy.update({key: value for key, value in item.items() if key != "id"})
            known_titles = set(records_by_title)
            records.extend(item for item in modern
                           if str(item["id"]) not in known_ids
                           and re.sub(r"[^a-z0-9가-힣]", "", item["title"].lower()) not in known_titles)
        elif source["source_type"] == "json_feed" and config.get("adapter") == "kakaobank_recruits":
            data = fetch_post_json(source["endpoint"], {"receiptFilterType":"ONGOING", "pageNumber":1, "pageSize":100})
            records = []
            for item in data.get("list", []):
                # API의 recruitNoticeUrl은 구형 recruiter.co.kr 주소를 반환한다.
                # 신규 카카오뱅크 인재영입 사이트가 사용하는 공식 상세 경로로 연결한다.
                url = f"https://recruit.kakaobank.com/jobs/{item['recruitNoticeSn']}"
                records.append({"id": item["recruitNoticeSn"], "title": item["recruitNoticeName"].strip(), "location": "경기 · 성남시", "url": url, "description": f"{item.get('recruitClassName', '기타')} · {item.get('recruitTypeName', '')}", "experience": "경력/신입 공고 상세 확인", "employment": "고용형태 확인", "category": item.get("recruitClassName"), "deadline": item.get("receiveEndDatetime", "")[:10], "posted_at": item.get("receiveStartDatetime", "")[:10]})
        elif source["source_type"] == "json_feed" and config.get("adapter") == "toss_job_groups":
            data = fetch_json(source["endpoint"])
            records = []
            def toss_meta(job, metadata_id, default=None):
                return next((entry.get("value") for entry in job.get("metadata", []) if entry.get("id") == metadata_id), default)
            for group in data.get("success", []):
                job = group.get("primary_job") or {}
                if job.get("internal_job_id") is None or toss_meta(job, 5038345003, False):
                    continue
                company = toss_meta(job, 4169410003, "토스커뮤니티")
                category = toss_meta(job, 24623243003) or map_category(group.get("title", ""))
                description = toss_meta(job, 4155730003, "") or ""
                employment = toss_meta(job, 4112432003, "고용형태 확인") or "고용형태 확인"
                deadline = toss_meta(job, 11431213003) or job.get("application_deadline")
                experience = "경력 무관"
                match = re.search(r"(\d+)\s*[-~]\s*(\d+)\s*년", description)
                if match: experience = f"경력 {match.group(1)}~{match.group(2)}년"
                else:
                    match = re.search(r"(\d+)\s*년\s*이상", description)
                    if match: experience = f"경력 {match.group(1)}년 이상"
                records.append({"id": group["id"], "title": group["title"].strip(), "location": (job.get("location") or {}).get("name") or "서울", "url": job.get("absolute_url") or f"https://toss.im/career/job-detail?gh_jid={group['id']}", "description": f"{company} · {description}", "experience": experience, "employment": employment, "category": category, "deadline": deadline, "posted_at": (job.get("first_published") or TODAY.isoformat())[:10]})
        elif source["source_type"] == "html" and config.get("adapter") == "samsung_careers":
            page = fetch_form_text(source["endpoint"], {"currentPageNo":"1", "intNo":"0", "strVal":"", "strTxt":"", "strKey":"", "strCompany":config["company_code"], "strType":"", "strOrderBy":"", "strEntity":""})
            records = []
            for block in re.findall(r"<li>(.*?)</li>", page, re.S):
                external = re.search(r'class="btnShare"\s+data-value="([\d,]+)"', block)
                title_match = re.search(r'<h3 class="title">(.*?)</h3>', block, re.S)
                period = re.search(r'<span class="period">\s*([\d.]+)\s*~\s*([\d.]+)', block, re.S)
                if not external or not title_match:
                    continue
                external_id = external.group(1).replace(",", "")
                title = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
                deadline = period.group(2).replace(".", "-") if period else None
                posted_at = period.group(1).replace(".", "-") if period else TODAY.isoformat()
                records.append({"id":external_id, "title":title, "location":"대한민국", "url":f"https://www.samsungcareers.com/hr/?no={external_id}", "description":"삼성화재 공식 채용공고", "experience":"경력/신입 공고 상세 확인", "employment":"고용형태 확인", "deadline":deadline, "posted_at":posted_at})
        elif source["source_type"] == "html" and config.get("adapter") == "shinhan_invest":
            page = fetch_text(source["endpoint"])
            records = []
            for block in re.findall(r'<li class="recruit_list__item"(.*?)</li>', page, re.S):
                # 공식 목록은 마감 공고에도 항목을 유지하며 end 배지를 붙인다.
                if re.search(r'<span class="end">\s*마감\s*</span>', block):
                    continue
                external = re.search(r"goView\('(\d+)'", block)
                title_match = re.search(r'<p class="recruit_list__tit[^\"]*">(.*?)</p>', block, re.S)
                if not external or not title_match:
                    continue
                title = html.unescape(re.sub(r'<[^>]+>', '', title_match.group(1))).strip()
                workstyle = html.unescape((re.search(r'data-workstyle="([^"]*)"', block) or [None, "고용형태 확인"])[1]).strip() or "고용형태 확인"
                recruit_type = html.unescape((re.search(r'data-reqtypenm="([^"]*)"', block) or [None, ""])[1]).strip()
                deadline_match = re.search(r'~\s*(\d{4}\.\d{2}\.\d{2})', block)
                deadline = deadline_match.group(1).replace('.', '-') if deadline_match else None
                records.append({"id":external.group(1), "title":title, "location":"서울", "url":f"https://recruit.shinhaninvest.com/recruit/view.do?annoId={external.group(1)}", "description":f"신한투자증권 · {recruit_type}", "experience":"경력/신입 공고 상세 확인", "employment":workstyle, "deadline":deadline, "posted_at":TODAY.isoformat()})
        elif source["source_type"] == "html" and config.get("adapter") == "greeting_html":
            page = fetch_text(source["endpoint"])
            records = []
            for match in re.finditer(r'<a[^>]+href="/ko/o/(\d+)"[^>]*>(.*?)</a>', page, re.S):
                title_match = re.search(r'OpeningListItemTitle[^>]*>(.*?)</', match.group(2), re.S)
                if not title_match:
                    continue
                title = html.unescape(re.sub(r'<[^>]+>', '', title_match.group(1))).strip()
                records.append({"id":match.group(1), "title":title, "location":"경기 · 성남시", "url":f"https://kakaopay.career.greetinghr.com/ko/o/{match.group(1)}", "description":f"{source['company']} 공식 채용공고", "experience":"경력 무관", "employment":"계약직" if "계약직" in title else "정규직", "posted_at":TODAY.isoformat()})
        elif source["source_type"] == "html" and config.get("adapter") == "naver_financial":
            data = fetch_json(source["endpoint"])
            records = []
            for item in data.get("list") or []:
                external = str(item.get("rcrtNo") or item.get("recruitNo") or item.get("seq") or "").strip()
                title = str(item.get("rcrtTitle") or item.get("title") or item.get("rcrtNm") or "").strip()
                if not external or not title:
                    continue
                records.append({"id":external, "title":title, "location":"경기 · 성남시", "url":f"https://recruit.naverfincorp.com/rcrt/view.do?rcrtNo={quote(external)}", "description":"네이버페이 공식 채용공고", "experience":"경력/신입 공고 상세 확인", "employment":"고용형태 확인", "posted_at":TODAY.isoformat()})
        elif source["source_type"] == "html" and config.get("adapter") == "hana_bank_career":
            page = fetch_korean_legacy_text(source["endpoint"])
            records = []
            for block in re.findall(r"<tr>(.*?)</tr>", page, re.S | re.I):
                if "접수중" not in block:
                    continue
                link = re.search(r"jobs_view_m\.asp\?ID=(\d+)[^>]*>(.*?)</a>", block, re.S | re.I)
                dates = re.findall(r"(\d{4}/\d{2}/\d{2})", block)
                if not link or len(dates) < 2:
                    continue
                external = link.group(1)
                title_html = re.sub(r"<span[^>]*display\s*:\s*none[^>]*>.*?</span>", "", link.group(2), flags=re.S | re.I)
                title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
                deadline = dates[1].replace("/", "-")
                if deadline < TODAY.isoformat():
                    continue
                records.append({
                    "id": external, "title": title, "location": "대한민국",
                    "url": f"https://hanabankhr.career.co.kr/jobs/jobs_view.asp?ID={external}",
                    "description": "하나은행 공식 상시채용 공고",
                    "experience": "경력/신입 공고 상세 확인",
                    "employment": "고용형태 확인", "deadline": deadline,
                    "posted_at": dates[0].replace("/", "-"),
                })
        elif source["source_type"] == "json_feed" and config.get("adapter") == "shinhan_card":
            data = fetch_json(source["endpoint"])
            records = []
            for item in (data.get("mbw_json") or {}).get("resList") or []:
                external = str(item.get("ancGdSeqN") or "").strip()
                if not external:
                    continue
                business_code = str(item.get("hmpBlbBsnCCd") or "").strip()
                deadline = str(item.get("ancEndD") or "").replace('.', '-') or None
                posted_at = str(item.get("ancStD") or "").replace('.', '-') or TODAY.isoformat()
                detail_url = "https://www.shinhancard.com/mob/MOBFMCOMN/MOBFMCOM02.shc?" + urlencode({"ancGdSeqN":external, "hmpBlbBsnCCd":business_code})
                records.append({"id":external, "title":str(item.get("sbjtNm") or "").strip(), "location":"대한민국", "url":detail_url, "description":f"신한카드 · {item.get('hmpBsnDtlTxt') or '채용공고'}", "experience":"경력/신입 공고 상세 확인", "employment":"고용형태 확인", "deadline":deadline, "posted_at":posted_at})
        else:
            raise ValueError("이 소스는 전용 수집기 연결 대기 상태입니다.")
        for item in records:
            title = item["title"]; location = item["location"]; url = item["url"]
            key = canonical(source["company"], title, location)
            category = normalize_category(item.get("category"), title)
            values = (source["company_id"],source_id,str(item["id"]),title,title,category,item["experience"],location,item["employment"],item["description"],item.get("deadline"),item.get("posted_at", TODAY.isoformat()),url,key)
            con.execute("""INSERT OR IGNORE INTO jobs(company_id,source_id,external_id,title,original_title,job_category,experience,location,employment_type,description,deadline,posted_at,source_url,canonical_key,status,last_seen_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open', CURRENT_TIMESTAMP)""",
              values)
            # 제목·지역이 같은 별도 공고도 누락하지 않는다. 기본 중복 키가 이미
            # 사용 중이지만 외부 ID가 다른 경우에만 외부 ID를 보조 키로 사용한다.
            exists = con.execute("SELECT 1 FROM jobs WHERE source_id=? AND external_id=?", (source_id, str(item["id"]))).fetchone()
            if not exists:
                fallback_key = hashlib.sha256(f"{key}|{item['id']}".encode()).hexdigest()
                con.execute("""INSERT OR IGNORE INTO jobs(company_id,source_id,external_id,title,original_title,job_category,experience,location,employment_type,description,deadline,posted_at,source_url,canonical_key,status,last_seen_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open', CURRENT_TIMESTAMP)""", values[:-1] + (fallback_key,))
            con.execute("""UPDATE jobs SET title=?,original_title=?,job_category=?,experience=?,location=?,employment_type=?,description=?,deadline=?,posted_at=?,source_url=?,status='open',last_seen_at=CURRENT_TIMESTAMP
              WHERE source_id=? AND external_id=?""", (title,title,category,item["experience"],location,item["employment"],item["description"],item.get("deadline"),item.get("posted_at", TODAY.isoformat()),url,source_id,str(item["id"])))
            count += 1
        external_ids = [str(item["id"]) for item in records]
        if external_ids:
            placeholders = ",".join("?" for _ in external_ids)
            con.execute(f"UPDATE jobs SET status='closed' WHERE source_id=? AND external_id NOT IN ({placeholders})", [source_id] + external_ids)
        else:
            con.execute("UPDATE jobs SET status='closed' WHERE source_id=?", (source_id,))
        expected_count = len(set(external_ids))
        stored_count = con.execute("SELECT COUNT(*) FROM jobs WHERE source_id=? AND status='open'", (source_id,)).fetchone()[0]
        if stored_count != expected_count:
            raise ValueError(f"수집 무결성 검증 실패: 공식 원본 {expected_count}건, DB {stored_count}건")
        status = f"성공: {count}건 반영"
        con.execute("UPDATE job_sources SET last_synced_at=CURRENT_TIMESTAMP,last_status=? WHERE id=?", (status,source_id)); con.commit()
    except Exception as e:
        status = f"실패: {str(e)[:160]}"; con.execute("UPDATE job_sources SET last_synced_at=CURRENT_TIMESTAMP,last_status=? WHERE id=?", (status,source_id)); con.commit(); raise
    finally: con.close()
    return count

CSS = '''<style>
*{box-sizing:border-box}body{margin:0;background:#f7f8fc;color:#172033;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:inherit;text-decoration:none}.wrap{max-width:1180px;margin:auto;padding:0 24px}.top{background:#101c3b;color:#fff;padding:18px 0}.brand{font-size:21px;font-weight:800}.brand i{color:#68e0c1;font-style:normal}.hero{background:#101c3b;color:#fff;padding:43px 0 58px}.hero h1{font-size:34px;line-height:1.25;margin:0 0 10px}.hero p{color:#c6d0e9}.search{display:flex;background:#fff;padding:7px;border-radius:12px;max-width:720px;box-shadow:0 8px 30px #07102955}.search input{flex:1;border:0;padding:12px 14px;font-size:15px;outline:0}.btn{border:0;border-radius:8px;background:#3158d4;color:#fff;padding:11px 18px;font-weight:700;cursor:pointer}.content{display:grid;grid-template-columns:245px 1fr;gap:25px;margin-top:0;padding-top:28px;padding-bottom:50px}.filters,.card,.detail{background:white;border:1px solid #e8eaf0;border-radius:12px;box-shadow:0 2px 8px #101c3b08}.filters{padding:18px;height:max-content}.filter-title{font-weight:800;margin:18px 0 8px}.filter-title:first-child{margin-top:0}.filters label{display:block;margin:7px 0;color:#4d5870;font-size:14px}.filters input{margin-right:7px}.toolbar{display:flex;align-items:center;justify-content:space-between;padding:15px 0}.muted{color:#69758b;font-size:13px}.card{display:block;padding:19px;margin-bottom:11px;min-width:0}.card:hover{border-color:#a9b8ec}.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px}.company{font-size:13px;color:#52617d;font-weight:700}.title{font-size:18px;font-weight:800;line-height:1.35;margin:6px 0 12px}.tags{display:flex;gap:6px;flex-wrap:wrap}.tag{background:#f0f3fa;color:#485673;border-radius:20px;padding:5px 9px;font-size:12px}.new{background:#e5fbf3;color:#08775c}.due{flex:0 0 auto;color:#dc4c44;font-size:13px;font-weight:700;white-space:nowrap;padding-top:1px}.empty{padding:35px;text-align:center}.source-links{display:grid;gap:9px;max-width:600px;margin:22px auto 0;text-align:left}.source-link{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:12px 14px;border:1px solid #e5e9f2;border-radius:8px}.source-link:hover{border-color:#a9b8ec;background:#fafbff}.source-link span{font-size:13px;color:#52617d}.admin{max-width:900px;margin:34px auto}.admin h1{font-size:27px}.table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden}.table td,.table th{padding:13px;text-align:left;border-bottom:1px solid #eef0f5;font-size:13px}.form{background:#fff;padding:20px;border-radius:12px;margin:20px 0}.form input,.form select{width:100%;padding:10px;border:1px solid #dce1ed;border-radius:7px;margin:5px 0 12px}.notice{padding:11px;border-radius:8px;background:#e9f8f2;color:#14694f;margin:12px 0}.detail{margin:32px auto;max-width:800px;padding:32px}.detail h1{margin:6px 0 14px}@media(max-width:720px){.content{grid-template-columns:1fr}.filters{display:none}.hero h1{font-size:28px}.wrap{padding:0 16px}.card-head{display:block}.due{margin-top:8px}.source-link{display:block}.source-link span{display:block;margin-top:4px}}
</style><style>.filter-toggle,.mobile-filter-head{display:none}@media(max-width:720px){.content{padding-top:18px}.toolbar{gap:12px;padding:10px 0 14px}.toolbar-meta{display:none}.filter-toggle{display:inline-flex;align-items:center;justify-content:center;gap:7px;width:auto;margin-left:auto;padding:9px 13px;border:1px solid #d5dbea;border-radius:9px;background:#fff;color:#27344f;font-weight:800;font-size:14px;box-shadow:0 2px 8px #101c3b08;cursor:pointer}.filter-count{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;padding:0 6px;border-radius:10px;background:#3158d4;color:#fff;font-size:11px}.filters{display:none}.filters.open{position:fixed;inset:0;z-index:100;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-content:start;gap:0 14px;width:100%;height:100vh;height:100dvh;max-height:100dvh;min-height:0;padding:20px 20px 118px;overflow-x:hidden;overflow-y:scroll;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;touch-action:pan-y;border:0;border-radius:0;background:#fff}.mobile-filter-head{position:sticky;top:0;z-index:2;display:flex;align-items:center;justify-content:space-between;grid-column:1/-1;margin:-20px -20px 10px;padding:18px 20px 14px;border-bottom:1px solid #e8eaf0;background:#fff;font-size:20px;font-weight:800}.filter-close{border:0;background:#f0f3fa;color:#27344f;width:36px;height:36px;border-radius:50%;font-size:22px;line-height:1;cursor:pointer}.filters .filter-title,.filters .btn,.filters>a{grid-column:1/-1}.filters.open .filter-apply{position:fixed;z-index:103;left:16px;right:16px;bottom:calc(14px + env(safe-area-inset-bottom));width:calc(100% - 32px);margin:0;padding:14px 18px;border:1px solid #3158d4;box-shadow:0 0 0 14px #fff,0 -8px 24px #101c3b20}}</style>'''

CSS += '''<style>
body{padding-bottom:78px}.card{position:relative}.card-link{display:block}.card-actions{display:flex;align-items:center;gap:9px;flex:0 0 auto}.favorite-form{margin:0}.favorite-btn{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;border:1px solid #dce1ed;border-radius:50%;background:#fff;color:#66738e;font-size:21px;line-height:1;cursor:pointer}.favorite-btn.active{border-color:#f1b9c4;background:#fff1f4;color:#e04464}.bottom-nav{position:fixed;z-index:80;left:50%;bottom:0;transform:translateX(-50%);display:grid;grid-template-columns:1fr 1fr;width:min(100%,520px);padding:8px 12px calc(8px + env(safe-area-inset-bottom));border:1px solid #e1e5ef;border-bottom:0;border-radius:18px 18px 0 0;background:#fff;box-shadow:0 -6px 24px #101c3b18}.bottom-tab{display:flex;align-items:center;justify-content:center;gap:7px;padding:11px 8px;border-radius:11px;color:#68748a;font-weight:750}.bottom-tab.active{background:#edf2ff;color:#294fc4}.bottom-badge{min-width:20px;padding:2px 6px;border-radius:10px;background:#e6eaf3;font-size:11px;text-align:center}.bottom-tab.active .bottom-badge{background:#3158d4;color:#fff}.detail-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:14px}
@media(max-width:720px){.card-head{display:flex}.card-actions{align-self:flex-start}.due{margin-top:0}.bottom-nav{width:100%;border-left:0;border-right:0;border-radius:16px 16px 0 0}}
</style>'''

CLIENT_JS = '''<script>
document.addEventListener('DOMContentLoaded',()=>{
  const key='finhire-favorites';
  let saved=new Set(JSON.parse(localStorage.getItem(key)||'[]').map(String));
  const persist=()=>localStorage.setItem(key,JSON.stringify([...saved]));
  const paint=()=>{
    document.querySelectorAll('[data-job-id]').forEach(el=>{
      const id=el.dataset.jobId, button=el.querySelector('.favorite-btn');
      if(button){const active=saved.has(id);button.classList.toggle('active',active);button.textContent=active?'♥':'♡';button.setAttribute('aria-label',active?'관심 공고에서 제거':'관심 공고에 추가');}
      if(location.pathname==='/favorites' && el.classList.contains('card')) el.hidden=!saved.has(id);
    });
    document.querySelectorAll('.bottom-badge').forEach(el=>el.textContent=saved.size);
    const empty=document.getElementById('favorites-empty');
    if(empty) empty.hidden=saved.size!==0;
    if(location.pathname==='/favorites'){
      const count=[...document.querySelectorAll('.card[data-job-id]')].filter(el=>!el.hidden).length;
      const heading=document.getElementById('result-heading');if(heading)heading.textContent=`관심 공고 ${count}개`;
    }
  };
  document.querySelectorAll('.favorite-form').forEach(form=>form.addEventListener('submit',event=>{
    event.preventDefault();const id=form.closest('[data-job-id]')?.dataset.jobId;if(!id)return;
    saved.has(id)?saved.delete(id):saved.add(id);persist();paint();
  }));
  paint();
});
</script>'''

def layout(title, content):
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} | FinHire Korea</title>{CSS}<body><header class="top"><div class="wrap"><a class="brand" href="/">Fin<i>Hire</i> Korea</a></div></header>{content}{CLIENT_JS}</body></html>'''

def job_where(qs):
    clauses=["j.status='open'", "(j.deadline IS NULL OR j.deadline >= date('now'))"]; values=[]
    keyword=qs.get("q",[""])[0].strip()
    if keyword: clauses.append("(j.title LIKE ? OR c.name LIKE ? OR j.description LIKE ?)"); values += [f"%{keyword}%"]*3
    for key,column in [("company","c.name"),("category","j.job_category"),("employment","j.employment_type")]:
        vals=qs.get(key,[])
        if vals: clauses.append("("+" OR ".join([column+" = ?"]*len(vals))+")"); values += vals
    location_parts=[]
    for value in qs.get("location",[]):
        if value == "서울": location_parts.append("(j.location LIKE '%서울%' OR lower(j.location) LIKE '%seoul%')")
        elif value == "부산": location_parts.append("(j.location LIKE '%부산%' OR lower(j.location) LIKE '%busan%')")
        elif value == "경기": location_parts.append("j.location LIKE '%경기%'")
        elif value == "전국/기타": location_parts.append("j.location IN ('대한민국','미정')")
        elif value == "해외": location_parts.append("j.location NOT LIKE '%서울%' AND lower(j.location) NOT LIKE '%seoul%' AND j.location NOT LIKE '%경기%' AND j.location NOT LIKE '%부산%' AND lower(j.location) NOT LIKE '%busan%' AND j.location NOT IN ('대한민국','미정')")
    if location_parts: clauses.append("(" + " OR ".join(f"({part})" for part in location_parts) + ")")
    if qs.get("new")==["1"]: clauses.append("j.posted_at >= date('now','-3 day')")
    if qs.get("deadline")==["7"]: clauses.append("j.deadline <= date('now','+7 day')")
    return " AND ".join(clauses),values

def checkbox(name, value, label, qs):
    checked = " checked" if value in qs.get(name,[]) else ""
    return f'<label><input type="checkbox" name="{name}" value="{html.escape(value)}"{checked} onchange="if(!window.matchMedia(\'(max-width:720px)\').matches)this.form.submit()">{html.escape(label)}</label>'

def bottom_nav(active, favorite_count):
    return f'''<nav class="bottom-nav" aria-label="공고 목록 전환"><a class="bottom-tab{' active' if active=='all' else ''}" href="/"><span>전체</span></a><a class="bottom-tab{' active' if active=='favorites' else ''}" href="/favorites"><span>관심</span><span class="bottom-badge">{favorite_count}</span></a></nav>'''

def job_card(r, is_favorite, next_path):
    return f'''<article class="card" data-job-id="{r['id']}"><div class="card-head"><a class="card-link" href="/jobs/{r['id']}"><div class="company">{html.escape(r['company'])} · {html.escape(r['industry'])}</div><div class="title">{html.escape(r['title'])}</div></a><div class="card-actions"><div class="due">마감 {r['deadline'] or '상시채용'}</div><form class="favorite-form" method="post" action="/favorites/{r['id']}/toggle"><input type="hidden" name="next" value="{html.escape(next_path, quote=True)}"><button class="favorite-btn{' active' if is_favorite else ''}" type="submit" aria-label="{'관심 공고에서 제거' if is_favorite else '관심 공고에 추가'}" title="{'관심 공고에서 제거' if is_favorite else '관심 공고에 추가'}">{'♥' if is_favorite else '♡'}</button></form></div></div><a class="card-link" href="/jobs/{r['id']}"><div class="tags"><span class="tag">{html.escape(r['job_category'])}</span><span class="tag">{html.escape(r['experience'])}</span><span class="tag">{html.escape(r['location'])}</span><span class="tag">{html.escape(r['employment_type'])}</span>{'<span class="tag new">NEW</span>' if r['posted_at'] >= (TODAY-dt.timedelta(days=3)).isoformat() else ''}</div></a></article>'''

def home(qs, favorites_only=False):
    con=db(); where,args=job_where(qs)
    rows=con.execute(f"""SELECT j.*,c.name company,c.industry
        FROM jobs j JOIN companies c ON c.id=j.company_id
        WHERE {where}
        ORDER BY CASE WHEN j.deadline IS NULL THEN 1 ELSE 0 END,
                 j.deadline ASC,
                 j.posted_at DESC,
                 j.id DESC""",args).fetchall()
    favorite_ids={r[0] for r in con.execute("SELECT job_id FROM favorites")}
    favorite_count=len(favorite_ids)
    company_rows=con.execute("""SELECT c.name,c.website,
        COALESCE((SELECT s.name FROM job_sources s WHERE s.company_id=c.id AND s.enabled=1 ORDER BY s.id LIMIT 1),'공식 채용 페이지') source_name
        FROM companies c ORDER BY c.name""").fetchall()
    companies=[r['name'] for r in company_rows]
    active_condition = "status='open' AND (deadline IS NULL OR deadline >= date('now'))"
    cat=[r[0] for r in con.execute(f"SELECT DISTINCT job_category FROM jobs WHERE {active_condition} ORDER BY job_category")]
    emp=[r[0] for r in con.execute(f"SELECT DISTINCT employment_type FROM jobs WHERE {active_condition} ORDER BY employment_type")]
    raw_locations=[(r[0] or '').lower() for r in con.execute(f"SELECT DISTINCT location FROM jobs WHERE {active_condition}")]
    loc=[]
    if any('서울' in value or 'seoul' in value for value in raw_locations): loc.append('서울')
    if any('경기' in value for value in raw_locations): loc.append('경기')
    if any('부산' in value or 'busan' in value for value in raw_locations): loc.append('부산')
    if any(value in ('대한민국','미정') for value in raw_locations): loc.append('전국/기타')
    known = lambda value: ('서울' in value or 'seoul' in value or '경기' in value or '부산' in value or 'busan' in value or value in ('대한민국','미정'))
    if any(not known(value) for value in raw_locations): loc.append('해외')
    con.close()
    filters=''.join(checkbox('company',x,x,qs) for x in companies)+ '<div class="filter-title">직무</div>'+''.join(checkbox('category',x,x,qs) for x in cat)+ '<div class="filter-title">지역</div>'+''.join(checkbox('location',x,x,qs) for x in loc)+ '<div class="filter-title">고용형태</div>'+''.join(checkbox('employment',x,x,qs) for x in emp)+ '<div class="filter-title">공고 상태</div>'+checkbox('new','1','최근 3일 신규',qs)+checkbox('deadline','7','7일 내 마감',qs)
    path = '/favorites' if favorites_only else '/'
    current_path = path + (('?' + urlencode(qs, doseq=True)) if qs else '')
    cards=''.join(job_card(r, r['id'] in favorite_ids, current_path) for r in rows)
    if favorites_only:
        cards += '''<div class="card empty" id="favorites-empty" hidden><b>관심 공고가 아직 없습니다.</b><p class="muted">전체 공고에서 ♡ 버튼을 누르면 이 브라우저에 저장됩니다.</p><a class="btn" style="display:inline-block;margin-top:8px" href="/">전체 공고 보기</a></div>'''
    if not cards:
        if favorites_only:
            cards = '''<div class="card empty"><b>관심 공고가 아직 없습니다.</b><p class="muted">전체 공고에서 ♡ 버튼을 누르면 여기에 저장됩니다.</p><a class="btn" style="display:inline-block;margin-top:8px" href="/">전체 공고 보기</a></div>'''
        else:
            selected_companies = set(qs.get('company', []))
            visible_sources = [r for r in company_rows if not selected_companies or r['name'] in selected_companies]
            links = ''.join(f'<a class="source-link" href="{html.escape(r["website"] or "#", quote=True)}" target="_blank" rel="noopener"><b>{html.escape(r["name"])}</b><span>{html.escape(r["source_name"])} · 공식 채용 페이지 ↗</span></a>' for r in visible_sources)
            selection_text = '선택한 기업의' if selected_companies else '확인된 기업의'
            cards = f'''<div class="card empty"><b>표시할 실제 수집 공고가 아직 없습니다.</b><p class="muted">{selection_text} 공식 채용 페이지를 확인해 보세요.</p><div class="source-links">{links}</div></div>'''
    query=html.escape(qs.get('q',[''])[0])
    active = bool(qs)
    filter_count = sum(len(qs.get(key,[])) for key in ('company','category','location','employment','new','deadline'))
    filter_count_html = f'<span class="filter-count">{filter_count}</span>' if filter_count else ''
    heading = '관심 공고' if favorites_only else '공식 채용공고'
    return layout('관심 공고' if favorites_only else '금융권 자사채용 통합검색',f'''<section class="hero"><div class="wrap"><h1>{'저장한 관심 공고를<br>한곳에서 확인하세요.' if favorites_only else '한국 금융권 공식 채용공고를<br>한곳에서 찾아보세요.'}</h1><p>은행 · 증권 · 카드 · 보험 · 저축은행 · 핀테크</p><form class="search" action="{path}"><input name="q" value="{query}" placeholder="기업명, 직무, 기술로 검색"><button class="btn">검색</button></form></div></section><main class="wrap content"><form class="filters" id="filters-panel" method="get"><div class="mobile-filter-head"><span>필터</span><button class="filter-close" type="button" aria-label="필터 닫기" onclick="document.getElementById('filter-toggle').click()">×</button></div><input type="hidden" name="q" value="{query}"><div class="filter-title">기업</div>{filters}<button class="btn filter-apply" style="margin-top:16px;width:100%">필터 적용</button>{f'<a class="muted" style="display:block;text-align:center;margin-top:13px" href="{path}">필터 초기화</a>' if active else ''}</form><section><div class="toolbar"><b id="result-heading">{heading} {len(rows)}개</b><span class="muted toolbar-meta">마감 임박순 · 공식 지원 페이지로 연결됩니다</span><button class="filter-toggle" id="filter-toggle" type="button" aria-expanded="false" aria-controls="filters-panel" onclick="const p=document.getElementById('filters-panel'),o=p.classList.toggle('open');this.setAttribute('aria-expanded',o);document.body.style.overflow=o?'hidden':''"><span class="filter-toggle-label">필터</span>{filter_count_html}<span aria-hidden="true">⌄</span></button></div>{cards}</section></main>{bottom_nav('favorites' if favorites_only else 'all', favorite_count)}''')

def detail(job_id):
    con=db(); r=con.execute("SELECT j.*,c.name company,c.industry,EXISTS(SELECT 1 FROM favorites f WHERE f.job_id=j.id) is_favorite FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?",(job_id,)).fetchone(); favorite_count=con.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]; con.close()
    if not r:return None
    return layout(r['title'],f'''<main class="wrap"><article class="detail" data-job-id="{r['id']}"><a class="muted" href="/">← 공고 목록</a><div class="company" style="margin-top:22px">{html.escape(r['company'])} · {html.escape(r['industry'])}</div><h1>{html.escape(r['title'])}</h1><div class="tags"><span class="tag">{html.escape(r['job_category'])}</span><span class="tag">{html.escape(r['experience'])}</span><span class="tag">{html.escape(r['location'])}</span><span class="tag">{html.escape(r['employment_type'])}</span></div><hr style="border:0;border-top:1px solid #edf0f6;margin:28px 0"><h3>업무 소개</h3><p>{r['description']}</p><h3>자격요건</h3><p>{r['requirements']}</p><p class="muted">게시일 {r['posted_at']} · 마감일 {r['deadline'] or '상시채용'}</p><div class="detail-actions"><a class="btn" href="{html.escape(r['source_url'],quote=True)}" target="_blank" rel="noopener">공식 채용 페이지에서 지원 ↗</a><form class="favorite-form" method="post" action="/favorites/{r['id']}/toggle"><input type="hidden" name="next" value="/jobs/{r['id']}"><button class="favorite-btn{' active' if r['is_favorite'] else ''}" type="submit" aria-label="{'관심 공고에서 제거' if r['is_favorite'] else '관심 공고에 추가'}">{'♥' if r['is_favorite'] else '♡'}</button></form></div></article></main>{bottom_nav('', favorite_count)}''')

def admin(message=""):
    con=db(); sources=con.execute("SELECT s.*,c.name company FROM job_sources s JOIN companies c ON c.id=s.company_id ORDER BY s.id DESC").fetchall(); companies=con.execute("SELECT * FROM companies ORDER BY name").fetchall(); con.close()
    opts=''.join(f'<option value="{x["id"]}">{html.escape(x["name"])}</option>' for x in companies)
    rows=''.join(f'<tr><td>{html.escape(r["company"])}</td><td>{html.escape(r["name"])}</td><td>{r["source_type"]}</td><td>{html.escape(r["last_status"] or "미실행")}</td><td><form method="post" action="/admin/sync/{r["id"]}"><button class="btn">지금 동기화</button></form></td></tr>' for r in sources) or '<tr><td colspan="5">등록된 수집 소스가 없습니다.</td></tr>'
    note=f'<div class="notice">{html.escape(message)}</div>' if message else ''
    return layout('관리자',f'''<main class="wrap admin"><h1>기업 공식 채용 소스 관리</h1><p class="muted">공개 API 및 공식 채용 페이지를 등록합니다. robots.txt·이용약관을 준수하고, 인증이 필요한 페이지는 수집하지 않습니다.</p>{note}<form class="form" method="post" action="/admin/sources"><h3>소스 추가</h3><label>기업</label><select name="company_id">{opts}</select><label>소스 이름</label><input required name="name" placeholder="예: 회사명 Greenhouse 채용공고"><label>유형</label><select name="source_type"><option value="greenhouse">Greenhouse 공개 API</option><option value="json_feed">공개 JSON 피드 (매핑 필요)</option><option value="html">공식 HTML 페이지 (어댑터 필요)</option></select><label>공개 엔드포인트</label><input required name="endpoint" placeholder="https://boards-api.greenhouse.io/v1/boards/{{token}}/jobs?content=true"><button class="btn">저장</button></form><table class="table"><thead><tr><th>기업</th><th>소스</th><th>유형</th><th>마지막 실행</th><th></th></tr></thead><tbody>{rows}</tbody></table><p class="muted" style="margin-top:20px">중복 제거: 기업명·정규화된 공고명·근무지를 해시 키로 사용하며, 동일 소스의 외부 ID는 업데이트합니다. 전체 동기화는 매일 KST 정오 12시에 자동 실행됩니다.</p></main>''')

class App(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): print("%s - %s" % (self.address_string(), fmt%args))
    def respond(self, content, status=200):
        self.send_response(status); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(content.encode())
    def do_GET(self):
        parsed=urlparse(self.path); qs=parse_qs(parsed.query)
        if parsed.path=="/": self.respond(home(qs))
        elif parsed.path=="/favorites": self.respond(home(qs, favorites_only=True))
        elif parsed.path=="/admin" and not os.getenv("VERCEL"): self.respond(admin(qs.get('message',[''])[0]))
        elif re.fullmatch(r"/jobs/\d+",parsed.path):
            page=detail(int(parsed.path.rsplit('/',1)[1])); self.respond(page or layout('없음','<main class="wrap"><p>공고를 찾지 못했습니다.</p></main>'), 200 if page else 404)
        else:self.respond(layout('찾을 수 없음','<main class="wrap"><p>페이지를 찾을 수 없습니다.</p></main>'),404)
    def do_POST(self):
        length=int(self.headers.get('Content-Length','0')); form=parse_qs(self.rfile.read(length).decode())
        if os.getenv("VERCEL") and self.path.startswith("/admin"):
            self.respond(layout('찾을 수 없음','<main class="wrap"><p>페이지를 찾을 수 없습니다.</p></main>'),404); return
        if re.fullmatch(r"/favorites/\d+/toggle", self.path):
            job_id=int(self.path.split('/')[2]); con=db()
            if con.execute("SELECT 1 FROM favorites WHERE job_id=?", (job_id,)).fetchone():
                con.execute("DELETE FROM favorites WHERE job_id=?", (job_id,))
            elif con.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                con.execute("INSERT INTO favorites(job_id) VALUES(?)", (job_id,))
            con.commit(); con.close()
            target=form.get('next',['/'])[0]
            if not target.startswith('/') or target.startswith('//'): target='/'
            self.send_response(303); self.send_header("Location",target); self.end_headers(); return
        if self.path=="/admin/sources":
            con=db(); con.execute("INSERT INTO job_sources(company_id,name,source_type,endpoint) VALUES(?,?,?,?)",(form['company_id'][0],form['name'][0],form['source_type'][0],form['endpoint'][0])); con.commit();con.close(); msg="수집 소스를 저장했습니다. 필요할 때 ‘지금 동기화’를 실행하세요."
        elif re.fullmatch(r"/admin/sync/\d+",self.path):
            try: msg=f"동기화 완료: {sync_source(int(self.path.rsplit('/',1)[1]))}건을 반영했습니다."
            except Exception as e: msg=f"동기화 실패: {e}"
        else:self.respond("Not found",404);return
        self.send_response(303); self.send_header("Location","/admin?"+urlencode({'message':msg})); self.end_headers()

def run_sync():
    con=db(); ids=[x[0] for x in con.execute("SELECT id FROM job_sources WHERE enabled=1")]; con.close()
    failures=[]
    for i in ids:
        try: print(f"source {i}: {sync_source(i)} jobs")
        except Exception as e:
            failures.append((i, str(e)))
            print(f"source {i}: failed: {e}")
    if failures:
        raise RuntimeError(f"전체 동기화 검증 실패: {len(failures)}개 소스")

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('command',nargs='?',default='serve',choices=['serve','sync']); parser.add_argument('--port',type=int,default=8000); args=parser.parse_args(); init_db()
    if args.command=='sync':run_sync()
    else:
        print(f"FinHire Korea is running at http://127.0.0.1:{args.port}")
        ThreadingHTTPServer(('127.0.0.1',args.port),App).serve_forever()
