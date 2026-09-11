# -*- coding: utf-8 -*-
"""
GitHub Actions 위에서 실행되는 자동 블로그 파이프라인 스크립트 (통합판)
- [개편] 구글 트렌드 기반 소싱을 폐기하고, 에버그린 주제 뱅크(가이드/비교/체크리스트/FAQ/용어정리) 기반으로 전면 전환
- [개편] 카테고리별 수익화 가중치(재테크·보험대출·정부지원금·헬스 우선) 반영, 하루 자동 발행 2회 상한(수동 실행은 무제한)
- [FIX] KST(한국시간) 고정 — 요일별 우선 테마/일일 집계/기사 내 날짜 표기가 UTC로 어긋나던 문제 수정
- [FIX] 에버그린 주제 뱅크를 엔티티×앵글 조합형으로 확장(카테고리당 72개, 총 1,080개) — 365일 이상 재사용 없이 운영 가능
- [업그레이드] 방문자 언어 감지 자동 번역 (버튼 숨김) 및 표 1.5배 확대 기능
"""

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import random
import re
import subprocess
import sys
import textwrap
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont

# =====================================================================
# 로깅 설정
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# =====================================================================
# [FIX] 타임존 고정 (KST)
# - GitHub Actions 실행 환경은 UTC입니다. datetime.now()를 그대로 쓰면 한국 자정~오전 9시
#   사이에 요일/날짜가 하루 밀려서, 요일별 우선 테마 선정·일일 발행량 집계·기사 본문의
#   "오늘 날짜" 표기가 서로 어긋나는 문제가 있었습니다. 이제 항상 한국 시간(KST, UTC+9)
#   기준으로 계산하도록 고정합니다.
# =====================================================================
KST = timezone(timedelta(hours=9))

def now_kst() -> datetime:
    return datetime.now(KST)

# =====================================================================
# 큐 파일 설정
# =====================================================================
QUEUE_FILE = "keywords_queue.json"

# =====================================================================
# 환경변수로 받는 설정값
# =====================================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SITE_TITLE = os.environ.get("SITE_TITLE", "내 자동 블로그")
SITE_TAGLINE = os.environ.get("SITE_TAGLINE", "매일 자동으로 업데이트되는 정보 큐레이션 블로그")
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID", "")
GOOGLE_SITE_VERIFICATION = os.environ.get("GOOGLE_SITE_VERIFICATION", "")
ADSENSE_CLIENT_ID = os.environ.get("ADSENSE_CLIENT_ID", "")
ADSENSE_SLOT_ID = os.environ.get("ADSENSE_SLOT_ID", "")

COUPANG_PARTNER_TAG = os.environ.get("COUPANG_PARTNER_TAG", "")
COUPANG_ACCESS_KEY = os.environ.get("COUPANG_ACCESS_KEY", "")
COUPANG_SECRET_KEY = os.environ.get("COUPANG_SECRET_KEY", "")

BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

# --- [NEW] 워드프레스 동시 자동 발행 관련 환경변수 ---
# [FIX] *.wordpress.com 호스팅 블로그(예: kresonate.wordpress.com)는 자체 호스팅 워드프레스와
# 완전히 다른 API(public-api.wordpress.com)를 쓰고, Basic Auth(Application Password)가 아니라
# OAuth2만 지원합니다. 그래서 WORDPRESS_CLIENT_ID/SECRET이 설정되면 워드프레스닷컴 OAuth2 방식을,
# 없으면 자체 호스팅용 Basic Auth 방식(wp-json/wp/v2)을 자동으로 사용합니다.
WORDPRESS_URL = os.environ.get("WORDPRESS_URL", "").rstrip("/")          # 예: kresonate.wordpress.com 또는 https://myblog.com
WORDPRESS_USERNAME = os.environ.get("WORDPRESS_USERNAME", "")            # 워드프레스 로그인 아이디
WORDPRESS_APP_PASSWORD = os.environ.get("WORDPRESS_APP_PASSWORD", "")    # Application Password (워드프레스닷컴은 계정보안>2단계인증 페이지에서 발급)
# 워드프레스닷컴 전용: https://developer.wordpress.com/apps/new/ 에서 앱 등록 후 발급되는 값
WORDPRESS_CLIENT_ID = os.environ.get("WORDPRESS_CLIENT_ID", "")
WORDPRESS_CLIENT_SECRET = os.environ.get("WORDPRESS_CLIENT_SECRET", "")

# --- [NEW] 네이버 블로그 자동 발행 관련 환경변수 ---
# 주의: 네이버 '글쓰기 오픈API'(writePost)는 2020-05-06자로 공식 종료되어 더 이상 사용할 수 없습니다.
# 따라서 아래는 Playwright 브라우저 자동화(로그인 → 글쓰기 화면 조작) 방식을 사용합니다.
FONT_CANDIDATES = [
    "font.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic-Bold.ttf",
    # [FIX] 나눔고딕 설치가 실패할 경우를 대비한 2차 후보 (Noto Sans CJK, 한글 지원)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]

DOCS_DIR = "docs"
POSTS_DIR = os.path.join(DOCS_DIR, "posts")
POSTS_JSON = os.path.join(DOCS_DIR, "posts.json")

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent?key={api_key}"
)

# [개편] 트렌드 스토리텔링 프레이밍을 걷어내고, 처음부터 순수 가이드/정보성 콘텐츠로 전환
SYSTEM_PROMPT = """당신은 독자에게 실질적으로 도움이 되는 정보를 정확하고 친절하게 전달하는 한국어 실용 정보 콘텐츠 전문 작가입니다.
검색을 통해 찾아온 독자가 궁금한 것에 바로 답을 얻고, 다음 행동(가입/신청/구매/실천)까지 자신 있게 결정할 수 있도록 돕는 글을 씁니다.
호기심을 자극하는 제목은 쓰되, 본문은 과장이나 감탄 없이 사람이 직접 정리해준 것처럼 담백하고 신뢰가 가는 톤으로 작성하세요.

아래 규칙을 지켜 작성하세요:
1. 제목은 검색 의도를 반영하되 흥미를 유발하도록 작성한다. 아래 9가지 후킹(hook) 기법 중 이 주제에 가장 잘 맞는 것을 1~2개 골라 제목과 도입부에 녹여낸다:
   ① 호기심 갭(정보의 틈을 열어 궁금하게) ② 구체성/숫자(정확한 수치로 신뢰감) ③ 손실회피(놓치면 손해라는 프레이밍)
   ④ 정체성/소속(나와 같은 부류가 하는 행동) ⑤ 대조(vs, 어느 쪽이 맞는지) ⑥ Before-After(변화의 폭을 자극)
   ⑦ 사회적 증거(다수의 선택을 근거로) ⑧ 의외성(예상을 살짝 배신하는 반전) ⑨ 낮은 진입장벽(나도 할 수 있겠다는 안도감)
   (예: "OO 신청 조건, 이것부터 확인하세요" / "OO 완벽 비교: 뭐가 다를까?") 단, 입력받은 주제의 의미를 벗어나지 않으며 25~40자 내외로 구글 검색결과에서 잘리지 않게 한다.
1-1. meta_description은 검색결과 스니펫에 노출되는 요약문이다. 핵심 키워드를 앞부분에 배치하고, "이 글에서 무엇을 얻어갈 수 있는지"가 분명히 드러나는 문장으로 100~140자 내외로 작성한다.
2. 소제목(H2)을 4~6개 사용해 구조화한다.
3. [매우 중요] 사전적 뜻풀이 나열이나 알맹이 없는 서론으로 시간을 끌지 마세요. 독자가 검색으로 이 글에 들어온 이유(무엇을 알고 싶어서, 무엇을 결정하고 싶어서)에 처음부터 집중하고, 조건·절차·비교·주의사항처럼 실제로 쓸모 있는 구체적 정보를 우선 배치하세요.
3-1. [문체/가독성 — 매우 중요] 다음 AI 특유의 어색한 말투를 피한다:
   - 모든 문단을 "~일까요?", "~습니다!" 같은 같은 패턴으로 끝맺지 말고 평서문/의문문/짧은 문장을 자연스럽게 섞는다.
   - 같은 내용을 표현만 바꿔 반복하지 않는다(패딩 금지). 한 문단에서 한 이야기를 하면 다음 문단은 반드시 새로운 정보로 넘어간다.
   - "정말", "충격적인", "놀라운", "발칵" 같은 과장 수식어는 글 전체에서 2~3회 이하로 아껴 쓴다.
   - 문단은 2~4문장, 대략 70~90자 내외(PC 기준 2줄, 모바일 기준 5~6줄 정도)로 짧게 끊어 모바일 가독성을 높인다.
   - 사람이 친구에게 설명하듯 구체적 사실·숫자·사례 위주로 쓰고, 막연한 감탄이나 분위기 묘사로 문단을 채우지 않는다.
3-2. [콘텐츠 품질 — 매우 중요] 이 글은 시간이 지나도 유효한(에버그린) 정보 가치를 지녀야 합니다. 검색엔진용으로 양산된 듯한 글, 이미 알려진 사실의 재탕, 알맹이 없이 분량만 채운 글은 금지합니다.
   독자가 실제로 "도움이 됐다"고 느낄 구체적 정보(배경, 맥락, 숫자, 비교, 실용적 시사점)를 반드시 포함해 독창적이고 유용한 콘텐츠를 작성하세요. 사실에 근거하지 않은 추측성 서술은 "~로 보인다", "~라는 분석이 나온다"처럼 단정하지 않는 표현을 쓰고, 확인 안 된 사실을 확정적으로 단언하지 않는다.
3-3. [에버그린 구조] 이 글은 특정 날짜/사건에 묶이지 않는 주제를 다룹니다. 본문 소제목 중 최소 1~2개는 "~하는 방법", "~ 비교", "체크리스트", "초보자를 위한 안내", "주의할 점", "자주 묻는 질문", "용어 정리"처럼 독자가 검색을 통해 몇 달 뒤에 들어와도 바로 답을 얻을 수 있는 실용적 구조로 구성한다.
3-4. [전문용어 해설] 일반 독자에게 낯설 수 있는 전문용어·업계 용어·줄임말이 나오면, 처음 등장하는 곳 바로 뒤에 괄호나 짧은 문장으로 간단히 풀어준다. (예: "PER(주가수익비율, 주가가 순이익 대비 몇 배인지 보여주는 지표)")
4. 글자 수는 1500~2200자 내외.
5. [중요] 서론(Hook)은 독자가 이 글을 클릭한 이유(궁금한 점, 해결하고 싶은 문제)를 짚어주며 시작합니다. (예: "OO을 신청하려는데 조건이 헷갈리시나요? 흔히 놓치는 부분부터 실제 신청 절차까지 한 번에 정리해드립니다.")
6. 가독성을 위해 본문 중 최소 1곳에 <table> (수치/스펙 비교용 정리표) 또는 <ul>/<ol> 목록을 반드시 포함한다. (질문-답변 내용은 표로 만들지 않음)
7. "product_keyword"에는 이 글 내용과 실제로 관련된, 쿠팡에서 검색했을 때 진짜 상품이 나올 만한 쇼핑 키워드(2~4단어)를 넣는다. 억지로 연결하기 어렵다면 반드시 빈 문자열("")로 둔다.
8. 콘텐츠 내용을 보고 아래 3가지 중 구글 상위노출에 가장 유리한 스키마 타입을 스스로 판단해서 고른다:
   - "FAQPage": 질문/답변 형태로 정리하기 좋은 주제일 때
   - "HowTo": 순서가 있는 절차/방법을 안내하는 주제일 때
   - "Article": 위 둘에 해당하지 않는 스토리텔링, 정보, 이슈형 글일 때
9. 고른 스키마 타입에 맞는 데이터를 함께 채운다: (FAQPage는 "faq_items", HowTo는 "howto_steps" 채우기, Article은 빈 배열)
10. 제목/키워드를 보고 카테고리 중 가장 알맞은 것 하나를 "category"에 고른다. ["뷰티패션", "푸드맛집", "여행", "테크IT", "재테크머니", "헬스운동", "홈인테리어", "대출보험", "정부지원금", "라이프스타일", "산사워케이션", "종가음식", "한방웰니스", "K공예인테리어", "가양주"]
11. category가 "대출보험" 또는 "정부지원금"이면 일반적인 조건 위주로 설명하고 공식 기관 확인이 필요하다는 점을 덧붙인다.
11-1. [출처 투명성 — 매우 중요] 확인 가능한 공식 출처(정부기관, 지자체, 공식 협회/진흥원, 공식 웹사이트 등)를 언급할 수 있는 내용이면 본문에서 "OO(기관명)에 따르면" 형태로 자연스럽게 언급한다. 실제로 존재하는지 확신할 수 없는 기관명·통계·수치는 지어내지 말고, 확실하지 않으면 출처를 특정하지 않은 채 일반적 서술로 남긴다.
11-2. [변동성 데이터 표기] 가격, 예약 방법, 영업시간, 지원금액, 신청 기간처럼 시간이 지나면 바뀔 수 있는 구체적 수치나 절차를 언급할 때는 사용자 메시지에 적힌 "오늘 날짜"를 기준으로 "OOOO년 O월 기준"처럼 시점을 명시하고, 문단 끝에 "정확한 사항은 공식 홈페이지나 해당 기관에서 다시 확인하시길 권장합니다" 같은 안내를 자연스럽게 덧붙인다.
12. 이 글이 여러 구체적인 대상을 비교/소개하는 성격이면 "product_list"에 1문장 설명과 함께 채운다. (최대 6개). 아니면 빈 배열.
12-1. "image_keywords"에는 이 글의 썸네일/본문 이미지로 쓸 무료 스톡사진을 검색하기 위한 영어 키워드 2~4단어를 넣는다.
   [매우 중요] 키워드를 그대로 번역하지 말 것. 스톡사진 사이트에는 한국 밈/특정 인물/드라마 대사 같은 고유명사 사진이 없으므로,
   글의 핵심 "장면/분위기/사물"을 일반적인 영어로 묘사한다. (예: 키워드가 "그래 이혼하자"라는 드라마 대사 밈이면
   "couple emotional conversation" 처럼 실제 촬영 가능한 보편적 장면으로, 키워드가 "아이온큐"라는 양자컴퓨터 기업이면
   "quantum computer technology"처럼 그 산업/사물을 나타내는 명사로 변환한다.)
13. 출력은 반드시 아래 JSON 형식만 반환한다. 다른 설명, 코드블록 기호(```) 없이 순수 JSON만 출력한다:
{
  "title": "...",
  "html_body": "...",
  "meta_description": "...",
  "schema_type": "Article 또는 FAQPage 또는 HowTo",
  "faq_items": [{"question": "...", "answer": "..."}],
  "howto_steps": [{"name": "...", "text": "..."}],
  "category": "위 10개 중 하나",
  "product_keyword": "쇼핑 키워드 또는 빈 문자열",
  "product_list": [{"name": "...", "description": "..."}],
  "image_keywords": "영어 스톡사진 검색어 2~4단어"
}
html_body는 <h2>, <p>, <table>, <ul> 등을 사용한 HTML 조각이어야 한다."""

CATEGORY_THEMES = {
    "뷰티패션": {"gradient": [(255, 107, 157), (255, 154, 158), (250, 208, 196)], "accent": "#ff6b9d", "badge": "💄 뷰티·패션", "label": "BEAUTY", "font": "Gowun+Dodum", "decor": ["💄", "💅", "👗", "👠", "💋", "🎀", "💎", "🌸"]},
    "푸드맛집": {"gradient": [(255, 107, 53), (247, 147, 30), (255, 210, 63)], "accent": "#ff6b35", "badge": "🍽️ 푸드·맛집", "label": "FOOD", "font": "Jua", "decor": ["🍕", "🍔", "🍰", "🍜", "🍩", "☕", "🍓", "🧁"]},
    "여행": {"gradient": [(17, 153, 142), (56, 239, 125), (100, 210, 255)], "accent": "#11998e", "badge": "✈️ 여행", "label": "TRAVEL", "font": "Gowun+Dodum", "decor": ["✈️", "🌴", "🗺️", "🧳", "🏖️", "📸", "🚗", "🗼"]},
    "테크IT": {"gradient": [(30, 60, 114), (42, 82, 152), (0, 198, 255)], "accent": "#2a5298", "badge": "💻 테크·IT", "label": "TECH", "font": "Noto+Sans+KR:wght@700", "decor": ["💻", "⌨️", "🖥️", "📱", "🔌", "🤖", "⚡", "🛰️"]},
    "재테크머니": {"gradient": [(17, 105, 79), (56, 173, 118), (168, 224, 99)], "accent": "#11694f", "badge": "💰 재테크", "label": "MONEY", "font": "Noto+Sans+KR:wght@700", "decor": ["💰", "💵", "📈", "🪙", "🏦", "💳", "📊", "🐷"]},
    "헬스운동": {"gradient": [(19, 78, 94), (113, 178, 128), (168, 224, 99)], "accent": "#134e5e", "badge": "💪 헬스·운동", "label": "FITNESS", "font": "Jua", "decor": ["💪", "🏋️", "🥗", "🧘", "🏃", "⏱️", "🚴", "🥑"]},
    "홈인테리어": {"gradient": [(196, 132, 88), (218, 170, 122), (238, 210, 175)], "accent": "#c48458", "badge": "🏠 홈·인테리어", "label": "HOME", "font": "Gowun+Dodum", "decor": ["🏠", "🪴", "🕯️", "🛋️", "🖼️", "🧺", "🪞", "🛏️"]},
    "라이프스타일": {"gradient": [(66, 133, 244), (156, 39, 176), (234, 67, 121)], "accent": "#4a90d9", "badge": "✨ 라이프스타일", "label": "LIFESTYLE", "font": "Noto+Sans+KR:wght@700", "decor": ["✨", "🌸", "☕", "📓", "🎧", "🕊️", "🌿", "⭐"]},
    "대출보험": {"gradient": [(20, 30, 48), (36, 59, 85), (65, 90, 119)], "accent": "#1e3a5f", "badge": "🏦 대출·보험", "label": "FINANCE", "font": "Noto+Sans+KR:wght@700", "decor": ["🏦", "📄", "💳", "🔍", "📞", "✅", "💼", "🧾"], "ymyl": True},
    "정부지원금": {"gradient": [(0, 91, 82), (0, 128, 105), (82, 183, 136)], "accent": "#00695c", "badge": "🏛️ 정부지원금", "label": "SUPPORT", "font": "Noto+Sans+KR:wght@700", "decor": ["🏛️", "📋", "🖊️", "📅", "✅", "💌", "🪪", "📢"], "ymyl": True},
    # --- [NEW] K-문화 블루오션 카테고리 (요일별 테마) ---
    "산사워케이션": {"gradient": [(58, 90, 64), (95, 133, 97), (168, 197, 158)], "accent": "#3a5a40", "badge": "🏔️ 산사 워케이션", "label": "TEMPLE STAY", "font": "Gowun+Dodum", "decor": ["🏔️", "🧘", "📶", "🍵", "🔔", "🌲", "☁️", "💻"]},
    "종가음식": {"gradient": [(122, 45, 35), (168, 92, 56), (222, 170, 108)], "accent": "#7a2d23", "badge": "🍲 종가 내림음식", "label": "JONGGA", "font": "Jua", "decor": ["🍲", "🥢", "🏯", "🍶", "🌾", "🫕", "🍚", "🏮"]},
    "한방웰니스": {"gradient": [(91, 60, 100), (139, 94, 148), (196, 160, 202)], "accent": "#5b3c64", "badge": "🌿 한방 웰니스", "label": "HANBANG", "font": "Gowun+Dodum", "decor": ["🌿", "♨️", "🍵", "🪷", "💆", "🧴", "🌾", "🩺"]},
    "K공예인테리어": {"gradient": [(139, 108, 66), (181, 148, 96), (222, 197, 158)], "accent": "#8b6c42", "badge": "🏺 K-공예 인테리어", "label": "K-CRAFT", "font": "Gowun+Dodum", "decor": ["🏺", "🪔", "🖌️", "🪵", "🧵", "🎨", "🕯️", "🏮"]},
    "가양주": {"gradient": [(150, 40, 27), (196, 84, 39), (231, 156, 92)], "accent": "#96281b", "badge": "🍶 가양주", "label": "GAYANGJU", "font": "Jua", "decor": ["🍶", "🌾", "🍇", "🏺", "🥃", "🍂", "🏮", "🎋"]},
}
DEFAULT_THEME = CATEGORY_THEMES["라이프스타일"]

def get_theme(category: str) -> Dict[str, Any]:
    return CATEGORY_THEMES.get(category, DEFAULT_THEME)

# --- [NEW] 썸네일용 무료 스톡 이미지(출처 표기) 검색 설정 ---
# 기존 "AI로 썸네일 이미지 생성" 방식을 없애고, Pexels 무료 이미지 API에서 실제 사진을 검색해
# 저작권 출처(작가명/링크)를 함께 표기하는 방식으로 변경합니다.
# 준비물: https://www.pexels.com/api/ 에서 무료로 발급받는 API 키를 PEXELS_API_KEY 환경변수로 전달.
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
STOCK_SEARCH_TERMS = {
    "뷰티패션": "cosmetics makeup fashion",
    "푸드맛집": "food restaurant dish",
    "여행": "travel landscape destination",
    "테크IT": "laptop computer technology",
    "재테크머니": "money finance coins",
    "헬스운동": "fitness workout gym",
    "홈인테리어": "home interior cozy",
    "대출보험": "bank finance document",
    "정부지원금": "government building document",
    "라이프스타일": "lifestyle coffee cozy",
    "산사워케이션": "temple mountain meditation workspace",
    "종가음식": "korean traditional dining table",
    "한방웰니스": "korean traditional spa wellness tea",
    "K공예인테리어": "korean pottery craft interior",
    "가양주": "korean traditional rice wine bottle",
}


# =====================================================================
# [개편] 에버그린 주제 뱅크 + 큐 관리
# - 기존 "오늘의 구글 트렌드" 소싱을 완전히 제거하고, 미리 큐레이션한 에버그린(시간이 지나도
#   검색되는) 주제 뱅크에서 주제를 뽑아 큐를 채웁니다. 가이드/비교/체크리스트/FAQ/용어정리 형태로
#   구성해 검색 의도에 바로 답이 되는 콘텐츠를 지향합니다.
# - CATEGORY_WEIGHT: 카테고리별 수익화 가중치. AdSense CPC/제휴 전환율이 높은 재테크·보험대출·
#   정부지원금·헬스 카테고리에 더 자주 노출되도록 가중치를 부여합니다.
# =====================================================================
# =====================================================================
# [개편] 에버그린 주제 뱅크: 엔티티 × 앵글 조합형 생성
# - [FIX] 기존에는 카테고리당 8~10개씩 수작업으로 나열해, 하루 여러 건 발행 시 몇 주 안에
#   소재가 고갈되고(재사용 로직도 없어 고갈 시 파이프라인이 조용히 멈춤) 365일 무재사용
#   운영이 불가능했습니다.
# - 이제 카테고리마다 "구체적 대상(엔티티)"과 "실용적 관점(앵글)"을 따로 큐레이션해두고,
#   두 목록을 조합(entity × angle)해 카테고리당 수십~100개 이상의 실제로 쓸모 있는 주제를
#   생성합니다. 각 조합은 의미 없는 키워드 뭉치가 아니라 실제 검색 의도에 맞는 구체적
#   문장이며, 최종 제목/본문은 SYSTEM_PROMPT가 별도로 다시 다듬으므로 이 문자열은 AI에게
#   전달하는 "주제 시드" 역할만 합니다.
# - 하루 자동 발행 2회 기준으로도 1년(365일) 이상 중복 없이 소진되지 않는 분량을 확보했고,
#   완전 소진 전에는 절대 재사용하지 않습니다(재사용 로직 없음, 의도적).
# =====================================================================
CATEGORY_ENTITIES: Dict[str, List[str]] = {
    "재테크머니": [
        "ISA 계좌", "연금저축펀드", "IRP(개인형퇴직연금)", "청년도약계좌", "예금자보호제도",
        "코스피 지수 투자", "코스닥 소형주 투자", "채권형 펀드", "리츠(REITs)", "달러 예금",
        "금 투자(골드바·골드뱅킹)", "배당주 투자",
    ],
    "대출보험": [
        "전세자금대출", "신용대출", "주택담보대출", "자동차보험", "실손의료보험", "암보험",
        "정책서민금융상품", "중도상환수수료", "DSR(총부채원리금상환비율)", "신용점수 관리",
        "카드론", "보험 리모델링",
    ],
    "정부지원금": [
        "청년내일저축계좌", "근로장려금", "기초연금", "육아휴직급여", "소상공인 정책자금",
        "국민취업지원제도", "에너지바우처", "청년월세지원", "귀농귀촌 지원금", "국가장학금",
        "청년구직활동지원금", "출산장려금",
    ],
    "헬스운동": [
        "홈트레이닝", "간헐적 단식", "런닝(달리기)", "필라테스", "요가", "근력운동(웨이트트레이닝)",
        "스트레칭", "단백질 보충제", "체지방률 관리", "수면의 질 관리", "다이어트 정체기 극복",
        "유산소운동",
    ],
    "테크IT": [
        "클라우드 저장소", "비밀번호 관리자 앱", "OTT 서비스", "생성형 AI 서비스", "노트북 구매",
        "스마트폰 배터리 관리", "무료 이미지 편집 프로그램", "이메일 피싱 대응", "와이파이 속도 문제",
        "중고 전자기기 구매", "VPN 서비스", "스마트워치",
    ],
    "홈인테리어": [
        "원룸 인테리어", "곰팡이 제거", "커튼과 블라인드", "이사 준비", "미니멀 라이프",
        "베란다 확장", "친환경 세제", "좁은 주방 수납", "반려동물 인테리어", "겨울철 난방비 절약",
        "중고가구 활용", "셀프 페인트",
    ],
    "푸드맛집": [
        "제철 채소 보관", "에어프라이어 요리", "홈베이킹", "식품 유통기한 관리", "다이어트 도시락",
        "커피 원두 로스팅", "냉동식품 보관", "비건 식단", "장보기 계획", "집들이 요리",
        "밀프렙(meal prep)", "제철 과일 고르기",
    ],
    "여행": [
        "저가항공 티켓", "여행자보험", "캐리어 구매", "해외 유심·로밍", "국내 캠핑",
        "여권 재발급", "면세점 쇼핑", "혼자 떠나는 여행", "짐 싸기 노하우", "공항 라운지 이용",
        "국내 항공 마일리지", "장기 배낭여행",
    ],
    "뷰티패션": [
        "스킨케어 루틴", "쿠션 파운데이션", "자외선차단제", "헤어 손상 관리", "체형별 코디",
        "저자극 화장품", "각질 관리", "향수 사용법", "겨울철 피부 건조 관리", "미니멀 옷장",
        "메이크업 지속력", "네일케어",
    ],
    "라이프스타일": [
        "미루는 습관 고치기", "아침 루틴", "가계부 작성", "번아웃 관리", "독서 습관",
        "디지털 디톡스", "감정일기", "집중력 관리", "새해 목표 설정", "인간관계 스트레스 관리",
        "미니멀리즘 실천", "시간관리(타임블로킹)",
    ],
    # --- K-문화 블루오션 카테고리 (요일별 우선 테마) ---
    "산사워케이션": [
        "전국 워케이션 사찰", "장기 체류형 템플스테이", "사찰 와이파이·업무환경", "디지털 노마드 사찰",
        "명상 프로그램", "사찰 예약 플랫폼", "계절별 산사 워케이션", "지자체 워케이션 지원사업",
        "산사 워케이션 비용", "업무 집중도 높은 사찰", "산사 워케이션 짐 싸기", "1인 산사 워케이션",
    ],
    "종가음식": [
        "안동 종가음식", "종가 프라이빗 다이닝", "종택 숙박", "고택 체험", "종가 예약 플랫폼",
        "지역별 종가음식", "종부 내림음식", "내림음식 코스요리", "한옥 미식 여행", "종가음식 에티켓",
        "종가 장류(간장·된장)", "종가 다과상",
    ],
    "한방웰니스": [
        "사상체질 진단", "체질별 한방차", "한방 스파", "약선 음식", "체질별 식단", "한방 족욕",
        "웰니스 여행", "약선 밀키트 구독", "한방 힐링 프로그램", "사상체질 생활습관",
        "체질별 운동법", "한방 입욕제",
    ],
    "K공예인테리어": [
        "달항아리 인테리어", "나전칠기 소품", "소반 활용법", "공예 원데이클래스", "신진 공예작가 브랜드",
        "옻칠 식기", "한지 공예", "전통 자수 소품", "한옥 인테리어 소품", "공예 선물 고르기",
        "도자기 공방 체험", "전통 매듭공예",
    ],
    "가양주": [
        "가양주 양조장 투어", "이화주(떠먹는 막걸리)", "전통주 구독 서비스", "막걸리 안주 페어링",
        "숨은 지역 양조장", "계절 전통주", "홈텐딩(집에서 술 만들기)", "전통주 시음회",
        "증류식 소주", "약주와 청주 차이", "전통주 선물세트", "전통주 페스티벌",
    ],
}

# 카테고리군별 앵글(실용적 관점) 템플릿. {e}에 위 엔티티가 대입됩니다.
_FINANCE_ANGLES = [
    "{e} 조건과 신청 방법 총정리", "{e} 신청 전 체크리스트", "{e} 장단점 완벽 비교",
    "{e} 초보자를 위한 안내", "{e} 자주 묻는 질문(FAQ) 정리", "{e} 주의할 점과 유의사항",
]
_HEALTH_ANGLES = [
    "{e} 시작하는 법, 초보자 가이드", "{e} 효과와 부작용 비교", "{e} 루틴 짜는 방법",
    "{e} 전 꼭 확인할 체크리스트", "{e} 자주 묻는 질문 정리", "{e} 고를 때 주의할 점",
]
_TECH_ANGLES = [
    "{e} 고를 때 체크리스트", "{e} 무료 vs 유료 비교", "{e} 설정 방법 총정리",
    "{e} 초보자를 위한 안내", "{e} 자주 묻는 질문 정리", "{e} 사용 시 주의할 점",
]
_HOME_ANGLES = [
    "{e} 예산별 가이드", "{e} 고를 때 비교 정리", "{e} 셀프로 하는 방법",
    "{e} 전 체크리스트", "{e} 아이디어 모음", "{e} 주의할 점",
]
_FOOD_ANGLES = [
    "{e} 보관법과 유통기한 정리", "{e} 고르는 법 비교", "{e} 초보자를 위한 레시피 가이드",
    "{e} 준비 체크리스트", "{e} 자주 묻는 질문 정리", "{e} 활용 팁 모음",
]
_TRAVEL_ANGLES = [
    "{e} 준비물 체크리스트", "{e} 예약 꿀팁 정리", "{e} 초보자를 위한 가이드",
    "{e} 비교 가이드", "{e} 주의할 점", "{e} 후기 모음",
]
_BEAUTY_ANGLES = [
    "{e} 루틴 가이드", "{e} 고르는 법 비교", "{e} 성분·주의사항 체크리스트",
    "{e} 초보자를 위한 안내", "{e} 자주 묻는 질문 정리", "{e} 관리 방법",
]
_LIFESTYLE_ANGLES = [
    "{e} 시작하는 방법", "{e} 습관 만들기 가이드", "{e} 체크리스트",
    "{e} 초보자를 위한 안내", "{e} 비교 정리", "{e} 주의할 점",
]
_KCULTURE_ANGLES = [
    "{e} 체험 방법과 준비물", "{e} 예약 방법 총정리", "{e} 비교 가이드",
    "{e} 체크리스트", "{e} 초보자를 위한 안내", "{e} 후기와 주의할 점",
]

CATEGORY_ANGLE_TEMPLATES: Dict[str, List[str]] = {
    "재테크머니": _FINANCE_ANGLES, "대출보험": _FINANCE_ANGLES, "정부지원금": _FINANCE_ANGLES,
    "헬스운동": _HEALTH_ANGLES, "테크IT": _TECH_ANGLES, "홈인테리어": _HOME_ANGLES,
    "푸드맛집": _FOOD_ANGLES, "여행": _TRAVEL_ANGLES, "뷰티패션": _BEAUTY_ANGLES,
    "라이프스타일": _LIFESTYLE_ANGLES,
    "산사워케이션": _KCULTURE_ANGLES, "종가음식": _KCULTURE_ANGLES, "한방웰니스": _KCULTURE_ANGLES,
    "K공예인테리어": _KCULTURE_ANGLES, "가양주": _KCULTURE_ANGLES,
}

def _build_topic_bank() -> Dict[str, List[str]]:
    """엔티티 × 앵글 조합으로 카테고리별 주제 목록을 생성합니다.
    (12개 엔티티 × 6개 앵글 = 카테고리당 72개, 총 15개 카테고리 약 1,080개 →
    하루 자동 2회 발행 기준으로도 1년 이상 중복 없이 운용 가능한 분량입니다.)"""
    bank: Dict[str, List[str]] = {}
    for category, entities in CATEGORY_ENTITIES.items():
        angles = CATEGORY_ANGLE_TEMPLATES.get(category, _LIFESTYLE_ANGLES)
        topics: List[str] = []
        seen = set()
        for angle_tpl in angles:
            for e in entities:
                topic = angle_tpl.format(e=e)
                if topic not in seen:
                    seen.add(topic)
                    topics.append(topic)
        bank[category] = topics
    return bank

EVERGREEN_TOPIC_BANK: Dict[str, List[str]] = _build_topic_bank()
# 카테고리별 수익화 가중치 (숫자가 클수록 큐에 더 자주 편성됨)
CATEGORY_WEIGHT: Dict[str, int] = {
    "재테크머니": 3, "대출보험": 3, "정부지원금": 3, "헬스운동": 2, "테크IT": 2,
    "홈인테리어": 1, "푸드맛집": 1, "여행": 1, "뷰티패션": 1, "라이프스타일": 1,
    "산사워케이션": 2, "종가음식": 2, "한방웰니스": 2, "K공예인테리어": 2, "가양주": 2,
}
# [NEW] 요일별 우선 테마 (월=0 ~ 금=4). 해당 요일엔 이 카테고리의 미발행 주제를 최우선으로 편성.
# 검색엔진에 체계적인 카테고리 구조를 인식시키고, 한 카테고리 연속 발행으로 인한 전문성 분산을 방지.
WEEKDAY_THEME_CATEGORY: Dict[int, str] = {
    0: "산사워케이션",   # 월요일
    1: "종가음식",       # 화요일
    2: "한방웰니스",     # 수요일
    3: "K공예인테리어",  # 목요일
    4: "가양주",         # 금요일
}

def _topic_category(topic: str) -> Optional[str]:
    for category, topics in EVERGREEN_TOPIC_BANK.items():
        if topic in topics:
            return category
    return None

def pick_next_topic(queue: Dict[str, Any]) -> Optional[str]:
    """[NEW] 오늘이 월~금이면 해당 요일의 우선 테마 카테고리에 속한 대기 주제를 최우선으로 뽑고,
    없으면 기존 순서(FIFO)대로 뽑습니다."""
    pending: List[str] = queue.get("pending", [])
    if not pending:
        return None

    today_category = WEEKDAY_THEME_CATEGORY.get(now_kst().weekday())
    if today_category:
        matches = [t for t in pending if _topic_category(t) == today_category]
        if matches:
            chosen = random.choice(matches)
            pending.remove(chosen)
            return chosen

    return pending.pop(0)

def load_queue() -> Dict[str, Any]:
    if not os.path.exists(QUEUE_FILE):
        return {"pending": [], "completed": [], "daily_stats": {"date": "", "count": 0}}
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("pending", [])
            data.setdefault("completed", [])
            data.setdefault("daily_stats", {"date": "", "count": 0})
            return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"큐 파일을 불러오는 데 실패했습니다: {e}")
        return {"pending": [], "completed": [], "daily_stats": {"date": "", "count": 0}}

def save_queue(queue: Dict[str, Any]) -> None:
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

def refill_evergreen_queue(target_size: int = 20) -> None:
    """[개편] 에버그린 주제 뱅크에서 카테고리 가중치를 반영해 큐를 채웁니다.
    이미 사용(pending/completed)된 주제는 다시 넣지 않습니다."""
    logger.info("=" * 60)
    logger.info("[에버그린 주제뱅크] 큐 보충 시작...")
    queue = load_queue()

    # [FIX] 구글 트렌드 시절 남아있던 잔여 키워드(주제뱅크에 없는 항목)를 자동 정리
    stale = [t for t in queue.get("pending", []) if _topic_category(t) is None]
    if stale:
        queue["pending"] = [t for t in queue.get("pending", []) if _topic_category(t) is not None]
        save_queue(queue)
        logger.info(f"[에버그린 주제뱅크] 예전 트렌드 키워드 잔여분 {len(stale)}개 정리 완료: {stale[:5]}{' ...' if len(stale) > 5 else ''}")

    used = set(queue.get("pending", [])) | set(queue.get("completed", []))

    # 카테고리별 미사용 주제 후보 목록
    pool: List[Tuple[str, int]] = []  # (주제, 가중치)
    for category, topics in EVERGREEN_TOPIC_BANK.items():
        weight = CATEGORY_WEIGHT.get(category, 1)
        for topic in topics:
            if topic not in used:
                pool.append((topic, weight))

    if not pool:
        logger.info("[에버그린 주제뱅크] 모든 주제를 이미 사용했습니다. EVERGREEN_TOPIC_BANK에 새 주제를 추가해주세요.")
        logger.info("=" * 60)
        return

    need = max(0, target_size - len(queue["pending"]))
    if need == 0:
        logger.info(f"[에버그린 주제뱅크] 큐가 이미 충분합니다 (대기 {len(queue['pending'])}개).")
        logger.info("=" * 60)
        return

    weights = [w for _, w in pool]
    topics_only = [t for t, _ in pool]
    picked: List[str] = []
    remaining_idx = list(range(len(topics_only)))
    remaining_weights = list(weights)
    for _ in range(min(need, len(topics_only))):
        chosen = random.choices(remaining_idx, weights=remaining_weights, k=1)[0]
        picked.append(topics_only[chosen])
        pos = remaining_idx.index(chosen)
        remaining_idx.pop(pos)
        remaining_weights.pop(pos)

    queue["pending"].extend(picked)
    save_queue(queue)
    logger.info(f"[에버그린 주제뱅크] 신규 편성: {len(picked)}개 (대기 {len(queue['pending'])}개)")
    logger.info("=" * 60)

DAILY_PUBLISH_LIMIT = 2  # [FIX] 하루 자동(스케줄) 발행은 2회로 제한. 단, 수동 실행(제목 직접 입력)은 이 한도와 무관하게 항상 발행됩니다(run()의 manual_title 분기 참고).

def check_daily_limit() -> bool:
    queue = load_queue()
    today_str = now_kst().strftime("%Y-%m-%d")
    daily_stats = queue.get("daily_stats", {"date": "", "count": 0})
    if daily_stats.get("date") != today_str:
        return True
    return daily_stats.get("count", 0) < DAILY_PUBLISH_LIMIT

def increment_daily_count() -> None:
    queue = load_queue()
    today_str = now_kst().strftime("%Y-%m-%d")
    daily_stats = queue.get("daily_stats", {"date": today_str, "count": 0})
    if daily_stats.get("date") == today_str:
        daily_stats["count"] = daily_stats.get("count", 0) + 1
    else:
        daily_stats = {"date": today_str, "count": 1}
    queue["daily_stats"] = daily_stats
    save_queue(queue)



# =====================================================================
# HTML 및 렌더링 템플릿들
# =====================================================================

def build_decor_html(theme: Dict[str, Any], seed: str) -> str:
    rng = random.Random(seed)
    emojis = theme["decor"]
    count = rng.randint(9, 12)
    items = []
    for _ in range(count):
        emoji = rng.choice(emojis)
        top = rng.randint(0, 96)
        left = rng.randint(0, 92)
        size = rng.randint(26, 58)
        rotate = rng.randint(-30, 30)
        opacity = round(rng.uniform(0.07, 0.16), 2)
        items.append(
            f'<span class="decor-item" style="top:{top}%;left:{left}%;font-size:{size}px;'
            f'opacity:{opacity};transform:rotate({rotate}deg);">{emoji}</span>'
        )
    return '<div class="decor-layer" aria-hidden="true">' + "".join(items) + "</div>"

def _search_console_meta() -> str:
    if not GOOGLE_SITE_VERIFICATION: return ""
    return f'\n<meta name="google-site-verification" content="{GOOGLE_SITE_VERIFICATION}">'

def _ga_snippet() -> str:
    if not GA_MEASUREMENT_ID: return ""
    return f"""
<script async src="https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{GA_MEASUREMENT_ID}');
</script>"""

ENABLE_AUTO_TRANSLATE = os.environ.get("ENABLE_AUTO_TRANSLATE", "true").strip().lower() != "false"

def _translate_widget() -> str:
    """방문자의 브라우저 언어가 한국어가 아니면 조용히 번역을 수행합니다 (UI 완전 숨김 처리)"""
    if not ENABLE_AUTO_TRANSLATE:
        return ""
    return """
<div id="google_translate_element" style="position:absolute; top:-9999px; left:-9999px; display:none;"></div>
<script>
function googleTranslateElementInit() {
  new google.translate.TranslateElement({pageLanguage: 'ko', autoDisplay: false}, 'google_translate_element');
}
(function() {
  try {
      var userLang = navigator.language || navigator.userLanguage;
      if (userLang && !userLang.startsWith('ko')) {
        var targetLang = userLang.split('-')[0];
        if(document.cookie.indexOf('googtrans=') === -1) {
          document.cookie = "googtrans=/ko/" + targetLang + "; path=/";
          if (document.domain) {
              document.cookie = "googtrans=/ko/" + targetLang + "; domain=" + document.domain + "; path=/";
          }
          window.location.reload();
        }
      }
  } catch(e) {}
})();
</script>
<script src="//translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>"""

def _adsense_snippet() -> str:
    if not ADSENSE_CLIENT_ID: return ""
    return f'\n<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={ADSENSE_CLIENT_ID}" crossorigin="anonymous"></script>'

def build_faq_section_html(article: Dict[str, Any], accent: str = "#4a90d9") -> str:
    if not article.get("faq_items"): return ""
    cards = []
    for i, qa in enumerate(article["faq_items"], 1):
        cards.append(
            f'<details style="margin:14px 0;background:#f7f8fa;border-left:4px solid {accent};'
            'border-radius:8px;padding:2px 18px;" open>'
            '<summary style="cursor:pointer;padding:14px 0;font-family:\'Noto Sans KR\',-apple-system,sans-serif;'
            f'font-weight:800;font-size:1.08em;color:#111;outline:none;user-select:none;">Q{i}. {qa.get("question", "")}</summary>'
            '<p style="margin:0;padding:0 0 16px;font-family:\'Noto Sans KR\',-apple-system,sans-serif;'
            f'font-weight:400;color:#555;line-height:1.75;">A. {qa.get("answer", "")}</p>'
            '</details>'
        )
    return '<h2 style="margin-top:2em;">자주 묻는 질문(FAQ) <span style="font-size:0.6em;color:#999;font-weight:400;">(탭하면 펼쳐져요)</span></h2>' + "".join(cards)

def build_json_ld(article: Dict[str, Any], canonical_url: str, thumb_url: str, date: str, platform: str = "github") -> str:
    schema_type = article.get("schema_type", "Article")
    title = article["title"]
    meta_description = article.get("meta_description", "")
    article_type = "BlogPosting" if platform == "blogger" else "Article"

    if schema_type == "FAQPage" and article.get("faq_items"):
        data = {
            "@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": qa.get("question", ""), "acceptedAnswer": {"@type": "Answer", "text": qa.get("answer", "")}} for qa in article["faq_items"]],
        }
    elif schema_type == "HowTo" and article.get("howto_steps"):
        data = {
            "@context": "https://schema.org", "@type": "HowTo", "name": title, "description": meta_description,
            "step": [{"@type": "HowToStep", "name": s.get("name", ""), "text": s.get("text", "")} for s in article["howto_steps"]],
        }
    else:
        schema_type = article_type
        data = {
            "@context": "https://schema.org", "@type": article_type, "headline": title, "description": meta_description,
            "image": thumb_url, "datePublished": date, "author": {"@type": "Organization", "name": SITE_TITLE},
        }

    data.pop("@context", None)
    breadcrumb = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_TITLE, "item": (SITE_URL + "/") if SITE_URL else "../index.html"},
            {"@type": "ListItem", "position": 2, "name": article.get("category", "라이프스타일"), "item": (SITE_URL + "/") if SITE_URL else "../index.html"},
            {"@type": "ListItem", "position": 3, "name": title, "item": canonical_url},
        ],
    }
    graph_nodes = [data, breadcrumb]
    if article.get("product_list"):
        graph_nodes.append({
            "@type": "ItemList", "name": f"{title} - 소개된 상품 목록",
            "itemListElement": [{"@type": "ListItem", "position": i, "item": {"@type": "Product", "name": p.get("name", ""), "description": p.get("description", "")}} for i, p in enumerate(article["product_list"][:6], 1)],
        })
    return json.dumps({"@context": "https://schema.org", "@graph": graph_nodes}, ensure_ascii=False, indent=2)

def build_blog_index_json_ld(posts: List[Dict[str, Any]]) -> str:
    data = {
        "@context": "https://schema.org", "@type": "Blog", "name": SITE_TITLE, "url": (SITE_URL + "/") if SITE_URL else ".",
        "blogPost": [{"@type": "BlogPosting", "headline": p["title"], "url": (f"{SITE_URL}/{p['file']}" if SITE_URL else p["file"]), "datePublished": p["date"], "image": (f"{SITE_URL}/{p['thumb']}" if SITE_URL else p["thumb"])} for p in posts[:10]],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)

POST_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{meta_description}">
<link rel="canonical" href="{canonical_url}">
<link rel="icon" type="image/png" href="../favicon.png">{search_console_meta}
<link rel="manifest" href="../manifest.json">
<meta name="theme-color" content="#facc15">
<link rel="apple-touch-icon" href="../icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{site_title_short}">
<script>if ('serviceWorker' in navigator) {{ window.addEventListener('load', () => navigator.serviceWorker.register('../sw.js').catch(()=>{{}})); }}</script>
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{meta_description}">
<meta property="og:image" content="{thumb_url}">
<meta property="og:url" content="{canonical_url}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{meta_description}">
<meta name="twitter:image" content="{thumb_url}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family={font}&family=Noto+Sans+KR:wght@400;700&display=swap" rel="stylesheet">
<script type="application/ld+json">
{json_ld}
</script>{ga_snippet}{adsense_snippet}
<style>
  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{ position: relative; width: 100%; max-width: 720px; margin: 0 auto; padding: 0 clamp(16px, 4vw, 20px) 60px; font-family: 'Noto Sans KR', -apple-system, sans-serif; line-height: 1.75; color: #1a1a1a; background: #fafafa; overflow-x: hidden; }}
  img {{ max-width: 100%; height: auto; }}
  .decor-layer {{ position: absolute; inset: 0; overflow: hidden; pointer-events: none; z-index: 0; }}
  .decor-item {{ position: absolute; filter: grayscale(0%); user-select: none; }}
  .content {{ position: relative; z-index: 1; }}
  .hero {{ margin: 0 -20px 24px; position: relative; }}
  .hero img {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; }}
  .badge {{ display: inline-block; background: {accent}; color: #fff; font-size: clamp(0.75em, 2.2vw, 0.85em); font-weight: 700; padding: 5px 14px; border-radius: 999px; margin: 20px 0 10px; }}
  h1 {{ font-family: '{font_family}', 'Noto Sans KR', sans-serif; font-size: clamp(1.4em, 5vw, 1.9em); line-height: 1.35; margin: 0 0 8px; word-break: keep-all; }}
  h2 {{ font-family: '{font_family}', 'Noto Sans KR', sans-serif; font-size: clamp(1.1em, 4vw, 1.35em); margin-top: 2em; padding: 10px 14px; background: linear-gradient(90deg, {accent}22, transparent); border-left: 5px solid {accent}; border-radius: 4px; position: relative; z-index: 1; word-break: keep-all; }}
  p {{ margin: 1em 0; position: relative; z-index: 1; }}
  table {{ width: 100%; min-width: 460px; border-collapse: collapse; font-size: 0.92em; }}
  th, td {{ padding: 11px 14px; border-bottom: 1px solid #eee; text-align: left; line-height: 1.5; }}
  th {{ background: {accent}14; font-weight: 800; color: #111; white-space: nowrap; }}
  tr:last-child td {{ border-bottom: none; }}
  a.back {{ display: inline-block; margin: 20px 0; color: {accent}; text-decoration: none; font-weight: 700; position: relative; z-index: 1; }}
  .meta {{ color: #999; font-size: 0.85em; margin-bottom: 4px; }}
  .related {{ margin-top: 60px; padding-top: 24px; border-top: 2px solid #eee; }}
  .related h3 {{ font-size: 1.1em; margin-bottom: 14px; }}
  .related-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 14px; }}
  .related-card {{ text-decoration: none; color: #1a1a1a; }}
  .related-card img {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; border-radius: 10px; margin-bottom: 6px; }}
  .related-card span {{ font-size: 0.88em; font-weight: 500; }}
  .post-nav {{ display: flex; justify-content: space-between; gap: 10px; margin: 30px 0; flex-wrap: wrap; }}
  .post-nav a {{ display: flex; align-items: center; gap: 8px; text-decoration: none; color: #333; background: #fff; border: 1px solid #eee; border-radius: 999px; padding: 6px 16px 6px 6px; font-size: 0.85em; max-width: 100%; }}
  .post-nav img {{ width: 28px; height: 28px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }}
  .post-nav .nav-icon {{ width: 28px; height: 28px; border-radius: 50%; background: {accent}; color: #fff; display:flex; align-items:center; justify-content:center; font-size: 14px; flex-shrink: 0; }}
  .post-nav span {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}

  details summary {{ list-style: none; display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
  details summary::-webkit-details-marker {{ display: none; }}
  details summary::after {{ content: '▼'; font-size: 0.75em; color: {accent}; flex-shrink: 0; }}
  details[open] summary::after {{ content: '▲'; }}
  @media (max-width: 480px) {{
    .related-grid {{ grid-template-columns: 1fr 1fr; }}
    .post-nav a {{ font-size: 0.78em; flex: 1 1 100%; }}
  }}
  @media (min-width: 900px) {{
    body {{ max-width: 760px; }}
  }}
  .translate-widget {{ position: fixed; top: 10px; right: 10px; z-index: 999; font-size: 0.8em; }}
  body {{ top: 0 !important; }}
  .goog-te-banner-frame {{ display: none !important; }}
</style>
</head>
<body>
{translate_widget}
{decor_html}
<div class="content">
<a class="back" href="../index.html">← 목록으로</a>
<div class="hero"><img src="../thumbs/{thumb_filename}" alt="{title}" loading="eager" fetchpriority="high"></div>
<span class="badge">{badge}</span>
<h1>{title}</h1>
<p class="meta">{date}</p>
{photo_credit_html}
{html_body}
{post_nav}
{related_html}
{bottom_ad}
</div>
</body>
</html>
"""

ALL_THEME_FONTS = sorted({t["font"] for t in CATEGORY_THEMES.values()})
def _google_fonts_url() -> str:
    families = "&family=".join(ALL_THEME_FONTS)
    return f"https://fonts.googleapis.com/css2?family={families}&family=Noto+Sans+KR:wght@400;700;900&display=swap"

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{site_title}</title>
<meta name="description" content="{site_title} - 자동으로 업데이트되는 블로그">
<link rel="canonical" href="{site_url}/">
<link rel="icon" type="image/png" href="favicon.png">{search_console_meta}
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#facc15">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{site_title_short}">
<script>if ('serviceWorker' in navigator) {{ window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(()=>{{}})); }}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{fonts_url}" rel="stylesheet">
<script type="application/ld+json">
{blog_json_ld}
</script>{ga_snippet}{adsense_snippet}
<style>
  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{ max-width: 1000px; margin: 0 auto; padding: 0 0 60px; font-family: 'Noto Sans KR', -apple-system, sans-serif; background:#f5f5f7; color:#1a1a1a; }}
  img {{ max-width: 100%; height: auto; }}
  .masthead {{ position: relative; margin-bottom: 26px; }}
  .masthead img {{ width: 100%; aspect-ratio: 1600/420; object-fit: cover; display:block; }}
  .masthead-inner {{ padding: 0 clamp(14px, 4vw, 20px); }}
  .brand-row {{ display:flex; align-items:center; gap:12px; margin: 18px 0 4px; flex-wrap: wrap; }}
  .brand-row img.logo {{ width:44px; height:44px; border-radius:50%; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }}
  h1.site-title {{ font-family: 'Jua', sans-serif; font-size: clamp(1.2em, 4.5vw, 1.6em); margin:0; word-break: keep-all; }}
  .dash-link {{ margin-left:auto; font-size: clamp(0.7em, 2.5vw, 0.75em); color:#888; text-decoration:none; background:#eee; padding:6px 14px; border-radius:999px; }}
  .intro {{ color:#555; font-size:0.95em; margin: 4px 0 16px; line-height:1.6; word-break: keep-all; }}
  .pill-row {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom: 10px; }}
  .pill {{ font-size:0.78em; font-weight:700; color:#fff; padding:5px 13px; border-radius:999px; }}
  .content-wrap {{ padding: 0 clamp(14px, 4vw, 20px); }}
  .tier-label {{ font-size: 0.85em; font-weight:900; color:#aaa; letter-spacing:2px; margin: 34px 0 12px; text-transform:uppercase; }}
  .tier-label:first-of-type {{ margin-top: 10px; }}

  .hero {{ display:block; text-decoration:none; color:#1a1a1a; background:#fff; border-radius:20px; overflow:hidden; box-shadow: 0 6px 24px rgba(0,0,0,0.10); }}
  .hero img {{ width:100%; aspect-ratio: 16/9; object-fit:cover; display:block; }}
  .hero-body {{ padding: clamp(16px, 4vw, 22px) clamp(18px, 5vw, 26px) 28px; }}
  .hero-badge {{ display:inline-block; font-size:0.8em; font-weight:700; color:#fff; padding:5px 14px; border-radius:999px; margin-bottom:12px; }}
  .hero-title {{ font-size: clamp(1.25em, 4.5vw, 1.7em); font-weight:800; line-height:1.35; word-break: keep-all; }}

  .mid-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:18px; }}
  .mid-card {{ text-decoration:none; color:#1a1a1a; background:#fff; border-radius:16px; overflow:hidden; box-shadow: 0 3px 14px rgba(0,0,0,0.08); transition: transform .15s ease; }}
  .mid-card:hover {{ transform: translateY(-3px); }}
  .mid-card img {{ width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }}
  .mid-body {{ padding: 14px 16px 18px; }}
  .mid-title {{ font-weight:700; font-size:clamp(0.92em, 3vw, 1.08em); line-height:1.4; word-break: keep-all; }}

  .bottom-grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap:14px; }}
  .bottom-card {{ text-decoration:none; color:#1a1a1a; background:#fff; border-radius:10px; overflow:hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
  .bottom-card img {{ width:100%; aspect-ratio:16/10; object-fit:cover; display:block; }}
  .bottom-body {{ padding: 8px 10px 12px; }}
  .bottom-title {{ font-weight:600; font-size:0.85em; line-height:1.35; word-break: keep-all; }}

  .badge-sm {{ display:inline-block; font-size:0.65em; font-weight:700; color:#fff; padding:2px 8px; border-radius:999px; margin-bottom:5px; }}
  .date {{ color: #999; font-size: 0.78em; margin-top: 5px; }}
  .site-footer {{ margin-top: 50px; padding: 24px 20px; border-top: 1px solid #e2e2e2; text-align:center; color:#999; font-size:0.82em; }}
  .site-footer a {{ color:#777; text-decoration:none; margin: 0 8px; }}
  .site-footer a:hover {{ color:#b45309; }}

  @media (max-width: 480px) {{
    .masthead img {{ aspect-ratio: 1600/620; }}
    .hero img {{ aspect-ratio: 16/9; }}
    .mid-grid {{ grid-template-columns: 1fr; gap: 14px; }}
    .bottom-grid {{ grid-template-columns: repeat(2, 1fr); }}
  }}
  @media (min-width: 1000px) {{
    .bottom-grid {{ grid-template-columns: repeat(5, 1fr); }}
  }}
  .translate-widget {{ position: fixed; top: 10px; right: 10px; z-index: 999; font-size: 0.8em; }}
  body {{ top: 0 !important; }}
  .goog-te-banner-frame {{ display: none !important; }}
</style>
</head>
<body>
{translate_widget}
<div class="masthead">
  <img src="banner.webp" alt="{site_title}" loading="eager" fetchpriority="high">
</div>
<div class="masthead-inner">
  <div class="brand-row">
    <img class="logo" src="logo.webp" alt="{site_title} 로고">
    <h1 class="site-title">{site_title}</h1>
    <a class="dash-link" href="dashboard.html">📊 성과관리</a>
  </div>
  <p class="intro">{site_tagline}</p>
  <div class="pill-row">{category_pills}</div>
</div>

<div class="content-wrap">
{hero_html}
{mid_html}
{bottom_html}
</div>
{footer_html}
</body>
</html>
"""

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>성과 관리 - {site_title}</title>
<style>
  body {{ max-width: 760px; margin: 40px auto; padding: 0 20px; font-family: -apple-system, sans-serif; color:#222; }}
  h1 {{ font-size: 1.5em; }}
  h2 {{ font-size: 1.1em; margin-top: 2em; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
  th, td {{ text-align: left; padding: 8px 4px; border-bottom: 1px solid #eee; }}
  a {{ color: #4a90d9; }}
  .card {{ background:#f7f7f9; border-radius:8px; padding:16px; margin: 10px 0; }}
  a.back {{ display: inline-block; margin-bottom: 20px; color: #4a90d9; text-decoration: none; }}
</style>
</head>
<body>
<a class="back" href="index.html">← 블로그로</a>
<h1>📊 성과 관리 대시보드</h1>
<div class="card">
  <b>실시간 트래픽 확인 (GA4)</b><br>
  플레이스토어 "Google Analytics" 앱 설치 후 이 사이트의 방문자/인기글을 확인하세요.<br>
  <a href="https://analytics.google.com" target="_blank">analytics.google.com 바로가기</a>
</div>
<div class="card">
  <b>수익(쿠팡 마크업 수수료) 확인</b><br>
  쿠팡파트너스 앱 또는 사이트에서 클릭수/수익을 확인하세요.<br>
  <a href="https://partners.coupang.com" target="_blank">partners.coupang.com 바로가기</a>
</div>
<div class="card">
  <b>광고 수익(애드센스) 확인</b><br>
  플레이스토어 "Google AdSense" 앱 설치 후 페이지뷰/광고 수익(전면광고 포함)을 확인하세요.<br>
  <a href="https://www.google.com/adsense" target="_blank">adsense.google.com 바로가기</a>
</div>
<div class="card">
  <b>검색 노출 확인 (Google Search Console)</b><br>
  사이트가 구글 검색에 얼마나 노출/클릭되는지 확인하세요. 최초 1회 소유권 인증이 필요합니다.<br>
  <a href="https://search.google.com/search-console" target="_blank">search.google.com/search-console 바로가기</a>
</div>
<h2>발행된 글 목록 ({post_count}개)</h2>
<table>
<tr><th>날짜</th><th>제목</th><th>바로가기</th></tr>
{rows}
</table>
</body>
</html>
"""

SITEMAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{site_url}/</loc></url>
{url_entries}
</urlset>
"""
ROBOTS_TXT = "User-agent: *\nAllow: /\n\nSitemap: {site_url}/sitemap.xml\n"
GEMINI_GRADIENT_COLORS = [(66, 133, 244), (156, 39, 176), (234, 67, 121)]
THUMB_SIZE = (1280, 720)

# =====================================================================
# 기타 유틸리티
# =====================================================================

def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip()
    return re.sub(r"[\s]+", "-", text) or "post"

# --- [NEW] AI의 "글자 수 세기" 오류 자동 교정 ---
# LLM은 종종 텍스트의 글자 수를 잘못 셉니다(예: "그래 이혼하자"는 실제 6글자인데 "다섯 글자"라고
# 서술). AI 판단에 맡기지 않고, Python이 키워드의 실제 길이를 정확히 세어 본문에서 언급된
# "OO 글자" 표현을 강제로 바로잡습니다.
KOREAN_COUNT_WORDS = {
    1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯", 6: "여섯", 7: "일곱", 8: "여덟",
    9: "아홉", 10: "열", 11: "열한", 12: "열두", 13: "열세", 14: "열네", 15: "열다섯",
    16: "열여섯", 17: "열일곱", 18: "열여덟", 19: "열아홉", 20: "스무",
}
_COUNT_WORD_PATTERN = re.compile(
    "(" + "|".join(re.escape(w) for w in sorted(KOREAN_COUNT_WORDS.values(), key=len, reverse=True)) + r")\s?(글자|자)\b"
)

def fix_character_count_claims(article: Dict[str, Any]) -> Dict[str, Any]:
    keyword = article.get("keyword", "")
    correct_len = len(re.sub(r"\s+", "", keyword))
    if correct_len not in KOREAN_COUNT_WORDS:
        return article  # 지원 범위(1~20글자) 밖이면 손대지 않음
    correct_word = KOREAN_COUNT_WORDS[correct_len]

    def _replace(m: "re.Match") -> str:
        return f"{correct_word} {m.group(2)}"

    for field in ("title", "html_body", "meta_description"):
        if article.get(field):
            article[field] = _COUNT_WORD_PATTERN.sub(_replace, article[field])
    return article

def generate_article(title: str) -> Dict[str, Any]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 환경변수가 비어있습니다. 저장소 Secrets 설정을 확인하세요.")

    url = GEMINI_URL.format(api_key=GEMINI_API_KEY)
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": f"오늘 날짜: {now_kst().strftime('%Y년 %m월 %d일')}\n\n주제: '{title}'\n\n이 주제에 대해 검색으로 찾아온 독자가 실제로 궁금해할 조건·절차·비교·주의사항을 중심으로, 정확하고 실용적인 가이드형 블로그 글을 작성해주세요. 확실하지 않은 정보는 단정하지 말고, 공식 기관 확인이 필요한 내용은 그렇게 안내해주세요. 시점을 언급할 때는 반드시 위에 적힌 '오늘 날짜'를 기준으로 하고, 이보다 오래된 연도를 임의로 쓰지 마세요."}]}],
        # [FIX] JSON 파싱 실패를 줄이기 위해 순수 JSON 출력을 강제하고 출력 토큰 한도를 명시적으로 늘림
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
    }

    last_error = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code in (429, 503):
                wait = 15 * attempt
                logger.warning(f"일시적 오류({resp.status_code}), {wait}초 대기 후 재시도 ({attempt}/3)")
                time.sleep(wait)
                last_error = f"{resp.status_code} 오류 반복됨"
                continue
            resp.raise_for_status()
            data = resp.json()

            candidates = data.get("candidates") or []
            if not candidates:
                block_reason = data.get("promptFeedback", {}).get("blockReason", "알 수 없음")
                last_error = f"candidates가 비어있음 (blockReason: {block_reason})"
                logger.warning(f"[Gemini] 응답에 candidates가 없습니다 ({attempt}/3): {last_error}")
                continue

            finish_reason = candidates[0].get("finishReason", "")
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                last_error = f"content.parts가 비어있음 (finishReason: {finish_reason})"
                logger.warning(f"[Gemini] 빈 응답 ({attempt}/3): {last_error}")
                continue

            text = parts[0].get("text", "")
            cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

            try:
                decoder = json.JSONDecoder()
                article, _ = decoder.raw_decode(cleaned)
            except json.JSONDecodeError as e:
                last_error = f"JSON 파싱 실패: {e} (finishReason: {finish_reason}, 응답 길이: {len(text)}자)"
                logger.warning(
                    f"[Gemini] {last_error} ({attempt}/3)\n"
                    f"  응답 앞부분: {text[:200]!r}\n  응답 뒷부분: {text[-200:]!r}"
                )
                continue

            if not article.get("title") or not article.get("html_body"):
                last_error = "응답 JSON에 title 또는 html_body가 없습니다."
                logger.warning(f"[Gemini] {last_error} ({attempt}/3)")
                continue

            article["keyword"] = title

            desc = article.get("meta_description", "").strip()
            if len(desc) > 160:
                desc = desc[:157].rstrip() + "..."
            article["meta_description"] = desc

            return article
        except (KeyError, IndexError) as e:
            last_error = f"Gemini 응답 형식이 예상과 다릅니다: {e}"
            logger.warning(f"[Gemini] {last_error} ({attempt}/3)")
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            time.sleep(10)

    raise RuntimeError(f"3번 시도했지만 계속 실패했습니다: {last_error}")

def _load_font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                return ImageFont.truetype(path, size)
            except Exception as e:
                # [FIX] 다운로드가 중간에 끊겨 0바이트/손상된 폰트 파일이 생겨도
                # 여기서 죽지 않고 다음 후보 폰트로 넘어가도록 방어
                logger.warning(f"폰트 파일 '{path}' 로드 실패({e}), 다음 후보로 대체합니다.")
    logger.warning("한글 폰트를 찾지 못해 기본 폰트로 대체합니다 (한글이 깨져 보일 수 있음).")
    return ImageFont.load_default()

def _make_gradient_background(size: Tuple[int, int], colors: List[Tuple[int, int, int]]):
    w, h = size
    base = Image.new("RGB", size, colors[0])
    top = Image.new("RGB", size, colors[-1])
    mask = Image.new("L", size)
    mask.putdata([int(((x / w + y / h) / 2) * 255) for y in range(h) for x in range(w)])
    blended = Image.composite(top, base, mask)
    mid = Image.new("RGB", size, colors[1])
    mid_mask = Image.new("L", size)
    mid_mask.putdata([int(80 * (1 - abs((x / w + y / h) / 2 - 0.5) * 2)) for y in range(h) for x in range(w)])
    return Image.composite(mid, blended, mid_mask)

def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

_pexels_unconfigured_logged = False

def _fetch_stock_photo(query: str, fallback_query: str, size: Tuple[int, int], seed: int) -> Tuple[Optional[Image.Image], Optional[Dict[str, str]]]:
    """Pexels에서 사진을 검색해 (이미지, 출처정보)를 반환합니다. 1순위 검색어(query, 보통 기사
    주제에 맞는 AI 추출 영어 키워드)로 먼저 찾고, 결과가 없으면 2순위(fallback_query, 카테고리
    일반 키워드)로 재검색합니다. API 키 미설정/요청 전부 실패 시 (None, None)을 반환하며,
    호출부에서 그라데이션으로 대체합니다."""
    global _pexels_unconfigured_logged
    if not PEXELS_API_KEY:
        if not _pexels_unconfigured_logged:
            logger.info("[무료 이미지] PEXELS_API_KEY 미설정으로 건너뜁니다. (그라데이션 배경으로 대체됩니다)")
            _pexels_unconfigured_logged = True
        return None, None

    for attempt_query in [q for q in (query, fallback_query) if q]:
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": PEXELS_API_KEY},
                params={"query": attempt_query, "orientation": "landscape", "per_page": 15, "size": "large"},
                timeout=15,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if not photos:
                continue  # 이 검색어로는 결과 없음 → 다음 후보 검색어로 재시도
            photo = photos[seed % len(photos)]
            img_url = photo["src"].get("large2x") or photo["src"].get("large") or photo["src"]["original"]
            img_resp = requests.get(img_url, timeout=20)
            img_resp.raise_for_status()
            img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")

            # 비율 유지 크롭 후 리사이즈 (center-crop)
            target_ratio = size[0] / size[1]
            w, h = img.size
            cur_ratio = w / h
            if cur_ratio > target_ratio:
                new_w = int(h * target_ratio)
                left = (w - new_w) // 2
                img = img.crop((left, 0, left + new_w, h))
            else:
                new_h = int(w / target_ratio)
                top = (h - new_h) // 2
                img = img.crop((0, top, w, top + new_h))
            img = img.resize(size)

            credit = {
                "name": photo.get("photographer", "Unknown"),
                "photographer_url": photo.get("photographer_url", "https://www.pexels.com"),
                "photo_url": photo.get("url", "https://www.pexels.com"),
                "source": "Pexels",
            }
            return img, credit
        except Exception as e:
            logger.warning(f"[무료 이미지] Pexels 검색/다운로드 실패('{attempt_query}'): {e}")
            continue

    logger.warning("[무료 이미지] 모든 검색어로 실패해 그라데이션으로 대체합니다.")
    return None, None

def _wrap_by_pixel_width(draw, text: str, font, max_width: int) -> List[str]:
    words = text.split(" ")
    lines: List[str] = []
    current = ""
    def width_of(s: str) -> int: return draw.textbbox((0, 0), s, font=font)[2] - draw.textbbox((0, 0), s, font=font)[0]

    for word in words:
        candidate = f"{current} {word}".strip()
        if width_of(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if width_of(word) <= max_width:
            current = word
            continue
        chunk = ""
        for ch in word:
            if width_of(chunk + ch) <= max_width: chunk += ch
            else:
                if chunk: lines.append(chunk)
                chunk = ch
        current = chunk

    if current: lines.append(current)
    return lines

def generate_thumbnail(title: str, output_path: str, theme: Dict[str, Any], category: str = "라이프스타일", image_keywords: str = "") -> Optional[Dict[str, str]]:
    seed = int(hashlib.md5(title.encode("utf-8")).hexdigest(), 16) % 100000
    fallback_query = STOCK_SEARCH_TERMS.get(category, STOCK_SEARCH_TERMS["라이프스타일"])
    photo, credit = _fetch_stock_photo(image_keywords, fallback_query, THUMB_SIZE, seed)

    if photo is not None:
        img = photo.convert("RGBA")
    else:
        # 무료 이미지 확보 실패 시에만 기존 그라데이션 배경으로 대체 (파이프라인 중단 방지용 폴백)
        img = _make_gradient_background(THUMB_SIZE, theme["gradient"]).convert("RGBA")

    draw = ImageDraw.Draw(img)
    accent_rgb = _hex_to_rgb(theme["accent"])

    # 카테고리 배지 (브랜드 일관성 유지용, 사진 위 작은 라벨만 표시하고 큰 제목 텍스트는 그리지 않음)
    label_font = _load_font(30)
    label_text = theme["label"]
    lb = draw.textbbox((0, 0), label_text, font=label_font)
    pad_x, pad_y = 18, 8
    badge_w = (lb[2] - lb[0]) + pad_x * 2
    badge_h = (lb[3] - lb[1]) + pad_y * 2
    badge_x, badge_y = 24, 24
    draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=badge_h // 2, fill=accent_rgb + (230,))
    draw.text((badge_x + pad_x, badge_y + pad_y - lb[1]), label_text, font=label_font, fill=(255, 255, 255, 255))

    bar_h = 18
    draw.rectangle([(0, THUMB_SIZE[1] - bar_h), (THUMB_SIZE[0], THUMB_SIZE[1])], fill=accent_rgb + (255,))

    # 출처 표기 (무료 이미지를 실제로 가져온 경우에만) — 저작권 크레딧을 사진 위에 작게 각인
    if credit:
        credit_font = _load_font(20)
        credit_text = f'Photo by {credit["name"]} on {credit["source"]}'
        cb = draw.textbbox((0, 0), credit_text, font=credit_font)
        cw, ch = cb[2] - cb[0], cb[3] - cb[1]
        cx = THUMB_SIZE[0] - cw - 16
        cy = THUMB_SIZE[1] - bar_h - ch - 14
        draw.rectangle([cx - 8, cy - 4, cx + cw + 8, cy + ch + 8], fill=(0, 0, 0, 110))
        draw.text((cx, cy - cb[1]), credit_text, font=credit_font, fill=(255, 255, 255, 235))

    img.convert("RGB").save(output_path, format="WEBP", quality=85, method=6)
    return credit

BRAND_GRADIENT = [(15, 23, 42), (30, 41, 59), (51, 65, 85)]
BRAND_ACCENT = (250, 204, 21)
LOGO_SIZE = (512, 512)
BANNER_SIZE = (1600, 420)

def generate_site_logo(output_path: str) -> None:
    img = _make_gradient_background(LOGO_SIZE, BRAND_GRADIENT).convert("RGBA")
    draw = ImageDraw.Draw(img)
    w, h = LOGO_SIZE
    margin = 36
    draw.ellipse([margin, margin, w - margin, h - margin], outline=BRAND_ACCENT + (255,), width=10)
    initial = (SITE_TITLE.strip()[:1] or "B")
    font = _load_font(220)
    bbox = draw.textbbox((0, 0), initial, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) / 2 - bbox[0], (h - th) / 2 - bbox[1]), initial, font=font, fill=(255, 255, 255, 255))
    img.convert("RGB").save(output_path, format="WEBP", quality=90)

def generate_site_banner(output_path: str) -> None:
    img = _make_gradient_background(BANNER_SIZE, BRAND_GRADIENT).convert("RGBA")
    draw = ImageDraw.Draw(img)
    w, h = BANNER_SIZE
    draw.rectangle([(0, 0), (w, 8)], fill=BRAND_ACCENT + (255,))
    title_font = _load_font(88)
    tagline_font = _load_font(32)
    tb = draw.textbbox((0, 0), SITE_TITLE, font=title_font)
    tw = tb[2] - tb[0]
    ty = h / 2 - 60
    draw.text(((w - tw) / 2, ty), SITE_TITLE, font=title_font, fill=(255, 255, 255, 255))
    lb = draw.textbbox((0, 0), SITE_TAGLINE, font=tagline_font)
    lw = lb[2] - lb[0]
    draw.text(((w - lw) / 2, ty + 110), SITE_TAGLINE, font=tagline_font, fill=BRAND_ACCENT + (255,))
    img.convert("RGB").save(output_path, format="WEBP", quality=88)

def ensure_brand_assets() -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    logo_path = os.path.join(DOCS_DIR, "logo.webp")
    generate_site_logo(logo_path)
    generate_site_banner(os.path.join(DOCS_DIR, "banner.webp"))
    favicon_path = os.path.join(DOCS_DIR, "favicon.png")
    with Image.open(logo_path) as im:
        im.convert("RGB").resize((64, 64)).save(favicon_path, format="PNG")

# =====================================================================
# [NEW] PWA(프로그레시브 웹앱) 지원
# - 홈 화면에 앱 아이콘처럼 설치할 수 있도록 manifest.json, 아이콘(PNG), 서비스워커를 생성합니다.
# - 앱스토어/플레이스토어 등록이나 결제 기능은 포함하지 않으며, 순수 "설치형 웹앱" 수준입니다.
# =====================================================================
def generate_pwa_icons() -> None:
    logo_path = os.path.join(DOCS_DIR, "logo.webp")
    if not os.path.exists(logo_path):
        return
    with Image.open(logo_path) as im:
        im = im.convert("RGB")
        for size in (192, 512):
            im.resize((size, size)).save(os.path.join(DOCS_DIR, f"icon-{size}.png"), format="PNG")
        # 마스커블 아이콘(안드로이드가 원형/둥근모서리로 잘라도 안전하도록 여백을 둔 버전)
        maskable = Image.new("RGB", (512, 512), BRAND_GRADIENT[0])
        inner = im.resize((360, 360))
        maskable.paste(inner, (76, 76))
        maskable.save(os.path.join(DOCS_DIR, "icon-maskable-512.png"), format="PNG")

def generate_pwa_manifest() -> None:
    accent_hex = "#{:02x}{:02x}{:02x}".format(*BRAND_ACCENT)
    bg_hex = "#{:02x}{:02x}{:02x}".format(*BRAND_GRADIENT[0])
    # [FIX] "/"(도메인 루트) 절대경로는 GitHub Pages 프로젝트 사이트(예: 아이디.github.io/저장소명/)처럼
    # 하위 경로에 배포되면 실제로 존재하지 않는 루트를 가리켜 404가 나고 PWA 설치 배너가 뜨지 않는
    # 원인이 됨. manifest의 start_url/scope/icons는 manifest.json 자신의 위치 기준 상대경로로 지정해
    # 루트 배포든 하위경로 배포든(SITE_URL 설정과 무관하게) 항상 올바르게 동작하도록 함.
    manifest = {
        "id": ".",
        "name": SITE_TITLE,
        "short_name": SITE_TITLE[:12],
        "description": SITE_TAGLINE,
        "start_url": ".",
        "scope": ".",
        "display": "standalone",
        "background_color": bg_hex,
        "theme_color": accent_hex,
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    with open(os.path.join(DOCS_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

def generate_service_worker() -> None:
    # 네트워크 우선(신선한 글을 놓치지 않도록) + 실패 시 캐시 폴백 정도의 최소 구현.
    # 블로그는 콘텐츠가 계속 갱신되므로 공격적인 캐싱은 피합니다.
    sw_js = """const CACHE_NAME = 'blog-pwa-v1';
self.addEventListener('install', (event) => {
  self.skipWaiting();
});
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
    ))
  );
  self.clients.claim();
});
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});
"""
    with open(os.path.join(DOCS_DIR, "sw.js"), "w", encoding="utf-8") as f:
        f.write(sw_js)

def ensure_pwa_assets() -> None:
    generate_pwa_icons()
    generate_pwa_manifest()
    generate_service_worker()

def _coupang_deeplink(search_url: str) -> Optional[str]:
    if not (COUPANG_ACCESS_KEY and COUPANG_SECRET_KEY): return None
    domain = "https://api-gateway.coupang.com"
    path = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
    try:
        query = urllib.parse.urlencode({"coupangUrls": search_url})
        path_with_query = f"{path}?{query}"
        datetime_str = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
        message = datetime_str + "GET" + path_with_query
        signature = hmac.new(COUPANG_SECRET_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
        auth_header = f"CEA algorithm=HmacSHA256, access-key={COUPANG_ACCESS_KEY}, signed-date={datetime_str}, signature={signature}"
        resp = requests.get(domain + path_with_query, headers={"Authorization": auth_header, "Content-Type": "application/json"}, timeout=8)
        resp.raise_for_status()
        return resp.json()["data"][0]["shortenUrl"]
    except Exception as e:
        logger.warning(f"[쿠팡 딥링크] 발급 실패, 일반 링크로 대체: {e}")
        return None

def add_ymyl_disclaimer(article: Dict[str, Any]) -> Dict[str, Any]:
    theme = get_theme(article.get("category", "라이프스타일"))
    if not theme.get("ymyl"): return article
    disclaimer = (
        '<div style="background:#fff8e1;border:1px solid #ffe082;border-radius:10px;'
        'padding:14px 18px;margin:24px 0;font-size:0.92em;color:#5d4037;">'
        '⚠️ <b>안내:</b> 이 글은 일반적인 정보 제공 목적으로 작성되었으며, 특정 상품·기관을 보증하지 않습니다. '
        '금리, 자격 요건, 지원금액, 신청 기간 등은 수시로 바뀔 수 있으니 반드시 공식 채널에서 최신 정보를 확인하세요.</div>'
    )
    article["html_body"] += disclaimer
    return article

def _relevance_score(article: Dict[str, Any], candidate: Dict[str, Any]) -> float:
    score = 0.0
    if candidate.get("category") == article.get("category", "라이프스타일"): score += 3.0
    current_words = set(re.findall(r"[\w가-힣]+", (article.get("title", "") + " " + article.get("keyword", ""))))
    candidate_words = set(re.findall(r"[\w가-힣]+", candidate.get("title", "")))
    score += len(current_words & candidate_words) * 1.5
    return score

def add_internal_link(article: Dict[str, Any]) -> Dict[str, Any]:
    if not os.path.exists(POSTS_JSON): return article
    with open(POSTS_JSON, "r", encoding="utf-8") as f: posts = json.load(f)
    if not posts: return article
    scored = [(p, _relevance_score(article, p)) for p in posts]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_pool = [p for p, s in scored[:5] if s > 0] or [p for p, s in scored[:5]]
    if not top_pool: return article
    weights = [max(s, 0.5) for p, s in scored[:len(top_pool)]]
    pick = random.choices(top_pool, weights=weights, k=1)[0]
    article["html_body"] += f'<p style="margin-top:2em;padding-top:1em;border-top:1px dashed #ddd;">🔗 이 글도 함께 보면 좋아요: <a href="../{pick["file"]}">{pick["title"]}</a></p>'
    return article

def _manual_ad_unit() -> str:
    if not (ADSENSE_CLIENT_ID and ADSENSE_SLOT_ID): return ""
    return (
        '<div style="margin:28px 0;text-align:center;">'
        f'<ins class="adsbygoogle" style="display:block" data-ad-client="{ADSENSE_CLIENT_ID}" '
        f'data-ad-slot="{ADSENSE_SLOT_ID}" data-ad-format="auto" data-full-width-responsive="true"></ins>'
        '<script>(adsbygoogle = window.adsbygoogle || []).push({});</script></div>'
    )

def insert_manual_ads(article: Dict[str, Any]) -> Dict[str, Any]:
    ad_html = _manual_ad_unit()
    if not ad_html: return article
    idx = article["html_body"].find("<h2")
    if idx != -1: article["html_body"] = article["html_body"][:idx] + ad_html + article["html_body"][idx:]
    else: article["html_body"] += ad_html
    return article

def _fetch_content_photo(image_keywords: str, category: str, seed: int, size=(1000, 560)):
    # [FIX] 카테고리 대분류 프롬프트로만 AI 일러스트를 생성하던 방식(pollinations.ai)을 제거하고,
    # 기사 주제에 맞는 AI 추출 검색어(image_keywords)로 실제 사진을 찾아 연관성을 높였습니다.
    fallback_query = STOCK_SEARCH_TERMS.get(category, STOCK_SEARCH_TERMS["라이프스타일"])
    photo, _credit = _fetch_stock_photo(image_keywords, fallback_query, size, seed)
    return photo

def enhance_tables(html_body: str, accent: str) -> str:
    counter = {"n": 0}
    def _style_cells(raw_table: str, min_width: int, is_modal: bool = False) -> str:
        # 1.5x 모달용 폰트 및 패딩 스케일 업 적용
        font_size = "1.5em" if is_modal else "0.92em"
        pad_th = "18px 21px" if is_modal else "12px 14px"
        pad_td = "18px 21px" if is_modal else "12px 14px"
        
        styled = re.sub(
            r"<table\b[^>]*>",
            f'<table style="width:100%;min-width:{min_width}px;border-collapse:collapse;overflow:hidden;font-size:{font_size};"',
            raw_table, count=1,
        )
        styled = re.sub(
            r"<th\b[^>]*>",
            f'<th style="padding:{pad_th};text-align:left;background:{accent}1f;font-weight:800;'
            f'color:#111;border-bottom:2px solid {accent}80;white-space:nowrap;word-break:keep-all;min-width:56px;">',
            styled,
        )
        styled = re.sub(
            r"<td\b[^>]*>",
            # [FIX] word-break:keep-all 없이는 좁은 열에서 한글 단어가 한 글자씩 쪼개져 세로로
            # 늘어지는 가독성 문제가 있었음. 단어 단위로만 줄바꿈되고, 최소 너비를 둬서 셀이
            # 지나치게 눌리지 않도록 수정.
            f'<td style="padding:{pad_td};text-align:left;border-bottom:1px solid #ececec;line-height:1.65;'
            f'vertical-align:top;word-break:keep-all;min-width:56px;">',
            styled,
        )
        return styled

    def wrap_table(match):
        counter["n"] += 1
        uid = f"tblzoom{counter['n']}_{random.randint(1000, 9999)}"
        table_html = match.group(0)
        styled_table = _style_cells(table_html, 460, False)
        # 모달 내부 표는 1.5배(1.5em 폰트) 커진 상태로 렌더링
        modal_table = _style_cells(table_html, 630, True)

        return (
            f'<div style="overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;margin:1.2em 0 0.4em;'
            f'border-radius:8px;border:1px solid #eee;">{styled_table}</div>'
            f'<div style="text-align:right;margin:0 0 1.2em;">'
            f'<button type="button" onclick="document.getElementById(\'{uid}\').style.display=\'flex\';" '
            f'style="border:none;background:none;color:{accent};font-size:0.85em;font-weight:700;'
            f'cursor:pointer;padding:4px 2px;">🔍 표 크게 보기</button></div>'
            f'<div id="{uid}" '
            f'style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.82);'
            f'z-index:1000;align-items:center;justify-content:center;padding:16px;" '
            f'onclick="if(event.target===this){{this.style.display=\'none\';}}">'
            f'<div style="background:#fff;border-radius:12px;padding:24px;max-width:95vw;max-height:88vh;overflow:auto;">'
            f'<button type="button" onclick="document.getElementById(\'{uid}\').style.display=\'none\';" '
            f'style="display:block;margin:0 0 16px auto;width:36px;height:36px;border-radius:50%;'
            f'border:none;background:#f0f0f0;font-size:1.2em;cursor:pointer;">✕</button>'
            f'{modal_table}'
            f'</div></div>'
        )

    return re.sub(r"<table.*?</table>", wrap_table, html_body, flags=re.DOTALL)

def insert_content_image(article: Dict[str, Any], slug: str) -> Dict[str, Any]:
    category = article.get("category", "라이프스타일")
    seed = int(hashlib.md5((article["title"] + "-inline").encode("utf-8")).hexdigest(), 16) % 100000
    photo = _fetch_content_photo(article.get("image_keywords", ""), category, seed)
    if photo is None: return article
    filename = f"{slug}-inline.webp"
    path = os.path.join(DOCS_DIR, "thumbs", filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    photo.save(path, format="WEBP", quality=82, method=6)

    theme = get_theme(category)
    img_html = (
        '<figure style="margin:20px 0;">'
        f'<img src="../thumbs/{filename}" alt="{article["title"]} 관련 이미지" loading="lazy" width="1000" height="560" style="width:100%;border-radius:10px;display:block;">'
        f'<figcaption style="text-align:center;font-size:0.82em;color:#999;margin-top:6px;">{theme["badge"]} 관련 이미지</figcaption>'
        '</figure>'
    )
    idx = article["html_body"].find("</h2>")
    if idx != -1: article["html_body"] = article["html_body"][:idx + 5] + img_html + article["html_body"][idx + 5:]
    else: article["html_body"] = img_html + article["html_body"]
    return article

def _fetch_product_icon(product_name: str, seed: int, size=(160, 160)):
    prompt = f"minimalist pencil sketch icon of {product_name}, single centered object, clean line art, simple outline, white background, no text, no watermark"
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?width={size[0]}&height={size[1]}&seed={seed}&nologo=true"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        if img.size != size: img = img.resize(size)
        return img
    except Exception as e:
        logger.warning(f"[상품 아이콘] 생성 실패, 아이콘 없이 표시: {e}")
        return None

def build_product_list_html(article: Dict[str, Any], slug: str, accent: str) -> str:
    products = article.get("product_list") or []
    if not products: return ""
    os.makedirs(os.path.join(DOCS_DIR, "thumbs"), exist_ok=True)
    cards = []
    for i, item in enumerate(products[:6], 1):
        name = item.get("name", "")
        desc = item.get("description", "")
        seed = int(hashlib.md5(f"{slug}-product-{i}".encode("utf-8")).hexdigest(), 16) % 100000
        icon = _fetch_product_icon(name, seed)
        if icon is not None:
            icon_filename = f"{slug}-product{i}.webp"
            icon.save(os.path.join(DOCS_DIR, "thumbs", icon_filename), format="WEBP", quality=80)
            icon_html = f'<img src="../thumbs/{icon_filename}" alt="{name}" loading="lazy" style="width:56px;height:56px;border-radius:10px;object-fit:cover;flex-shrink:0;">'
        else:
            icon_html = f'<div style="width:56px;height:56px;border-radius:10px;background:{accent}22;flex-shrink:0;"></div>'

        cards.append(
            '<div style="display:flex;gap:14px;align-items:center;margin:10px 0;padding:12px 14px;'
            f'background:#f7f8fa;border-radius:10px;">{icon_html}'
            f'<div><p style="margin:0 0 3px;font-weight:700;color:#111;">{name}</p>'
            f'<p style="margin:0;color:#555;font-size:0.92em;line-height:1.5;">{desc}</p></div></div>'
        )
    return '<h2 style="margin-top:2em;">한눈에 보는 상품 목록</h2>' + "".join(cards)

def add_coupang_markup(article: Dict[str, Any]) -> Dict[str, Any]:
    product_keyword = (article.get("product_keyword") or "").strip()
    if not product_keyword: return article
    search_url = f"https://www.coupang.com/np/search?q={urllib.parse.quote(product_keyword)}"
    if COUPANG_PARTNER_TAG: search_url += f"&lptag={COUPANG_PARTNER_TAG}"
    link = _coupang_deeplink(search_url) or search_url
    extra_html = (
        f'<h2>관련 추천 상품</h2>'
        f'<p><a href="{link}" target="_blank" rel="nofollow sponsored">{product_keyword} 관련 인기 상품 보러가기</a></p>'
        '<p style="font-size:0.85em;color:#888;">이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.</p>'
    )
    article["html_body"] += extra_html
    return article

def _font_family_name(font_param: str) -> str:
    return font_param.split(":")[0].replace("+", " ")

def _build_post_nav_html() -> str:
    prev_post = None
    if os.path.exists(POSTS_JSON):
        with open(POSTS_JSON, "r", encoding="utf-8") as f:
            posts = json.load(f)
        if posts: prev_post = posts[0]
    prev_html = (
        f'<a href="../{prev_post["file"]}"><img src="../{prev_post["thumb"]}" alt="이전 게시물">'
        f'<span>← 이전 게시물: {prev_post["title"]}</span></a>'
        if prev_post else '<a href="../index.html"><span class="nav-icon">🏠</span><span>목록으로</span></a>'
    )
    latest_html = '<a href="../index.html"><span class="nav-icon">📰</span><span>최신 게시물 보기</span></a>'
    return f'<div class="post-nav">{prev_html}{latest_html}</div>'

def _build_related_html(exclude_slug: str) -> str:
    if not os.path.exists(POSTS_JSON): return ""
    with open(POSTS_JSON, "r", encoding="utf-8") as f: posts = json.load(f)
    posts = [p for p in posts if p.get("file") != exclude_slug][:3]
    if not posts: return ""
    cards = "\n".join(
        f'<a class="related-card" href="../{p["file"]}"><img src="../{p["thumb"]}" alt="{p["title"]}" loading="lazy">'
        f'<span>{p["title"]}</span></a>' for p in posts
    )
    return f'<div class="related"><h3>📌 함께 보면 좋은 글</h3><div class="related-grid">{cards}</div></div>'

def save_post(article: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str, str, str]:
    os.makedirs(POSTS_DIR, exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, "thumbs"), exist_ok=True)
    category = article.get("category", "라이프스타일")
    theme = get_theme(category)
    slug = slugify(article["keyword"])
    today = now_kst().strftime("%Y-%m-%d")
    thumb_filename = f"{slug}-{today}.webp"
    post_filename = f"{slug}-{today}.html"
    photo_credit = generate_thumbnail(article["title"], os.path.join(DOCS_DIR, "thumbs", thumb_filename), theme, category, article.get("image_keywords", ""))
    photo_credit_html = ""
    if photo_credit:
        photo_credit_html = (
            f'<p style="margin:6px 0 0;font-size:0.78em;color:#aaa;">'
            f'사진: <a href="{photo_credit["photo_url"]}" target="_blank" rel="nofollow noopener">'
            f'{photo_credit["name"]}</a> / {photo_credit["source"]} (무료 이미지, 출처 표기)</p>'
        )
    
    cleaned_body = re.sub(
        r"<h[23]>[^<]*자주\s*묻는\s*질문[^<]*</h[23]>.{0,400}?</table>",
        "", article["html_body"], flags=re.DOTALL | re.IGNORECASE
    )
    article["html_body"] = cleaned_body
    article["html_body"] = enhance_tables(article["html_body"], theme["accent"])
    article = insert_content_image(article, slug)
    article["html_body"] += build_faq_section_html(article, theme["accent"])
    article["html_body"] += build_product_list_html(article, slug, theme["accent"])
    
    post_url = f"{SITE_URL}/posts/{post_filename}" if SITE_URL else f"posts/{post_filename}"
    thumb_url = f"{SITE_URL}/thumbs/{thumb_filename}" if SITE_URL else f"../thumbs/{thumb_filename}"
    title = article["title"]
    json_ld = build_json_ld(article, post_url, thumb_url, today)
    
    html = POST_TEMPLATE.format(
        title=title,
        meta_description=article.get("meta_description", ""),
        date=today,
        html_body=article["html_body"],
        thumb_filename=thumb_filename,
        canonical_url=post_url,
        thumb_url=thumb_url,
        json_ld=json_ld,
        ga_snippet=_ga_snippet(),
        adsense_snippet=_adsense_snippet(),
        font=theme["font"],
        font_family=_font_family_name(theme["font"]),
        accent=theme["accent"],
        badge=theme["badge"],
        related_html=_build_related_html(exclude_slug=f"posts/{post_filename}"),
        post_nav=_build_post_nav_html(),
        decor_html=build_decor_html(theme, seed=slug),
        bottom_ad=_manual_ad_unit(),
        search_console_meta=_search_console_meta(),
        translate_widget=_translate_widget(),
        photo_credit_html=photo_credit_html,
        site_title_short=SITE_TITLE[:12],
    )
    with open(os.path.join(POSTS_DIR, post_filename), "w", encoding="utf-8") as f:
        f.write(html)
        
    post_meta = {
        "title": title, "file": f"posts/{post_filename}", "thumb": f"thumbs/{thumb_filename}",
        "date": today, "category": category, "accent": theme["accent"], "badge": theme["badge"],
    }
    return post_meta, json_ld, thumb_url, os.path.join(DOCS_DIR, "thumbs", thumb_filename), post_url

def update_index(new_post: Dict[str, Any]) -> List[Dict[str, Any]]:
    os.makedirs(DOCS_DIR, exist_ok=True)
    posts = []
    if os.path.exists(POSTS_JSON):
        with open(POSTS_JSON, "r", encoding="utf-8") as f: posts = json.load(f)
    posts.insert(0, new_post)
    with open(POSTS_JSON, "w", encoding="utf-8") as f: json.dump(posts, f, ensure_ascii=False, indent=2)

    hero_posts, mid_posts, bottom_posts = posts[:1], posts[1:3], posts[3:]

    hero_html = ""
    if hero_posts:
        p = hero_posts[0]
        hero_html = (
            '<div class="tier-label">🔥 최신 이야기</div>'
            f'<a class="hero" href="{p["file"]}"><img src="{p["thumb"]}" alt="{p["title"]}" loading="eager" fetchpriority="high">'
            f'<div class="hero-body"><span class="hero-badge" style="background:{p.get("accent", "#4a90d9")}">{p.get("badge", "✨ 라이프스타일")}</span>'
            f'<div class="hero-title">{p["title"]}</div><div class="date">{p["date"]}</div></div></a>'
        )
    mid_html = ""
    if mid_posts:
        cards = "\n".join(
            f'<a class="mid-card" href="{p["file"]}"><img src="{p["thumb"]}" alt="{p["title"]}" loading="lazy">'
            f'<div class="mid-body"><span class="badge-sm" style="background:{p.get("accent", "#4a90d9")}">{p.get("badge", "✨ 라이프스타일")}</span>'
            f'<div class="mid-title">{p["title"]}</div><div class="date">{p["date"]}</div></div></a>' for p in mid_posts
        )
        mid_html = f'<div class="tier-label">📖 다음 이야기</div><div class="mid-grid">{cards}</div>'
    bottom_html = ""
    if bottom_posts:
        cards = "\n".join(
            f'<a class="bottom-card" href="{p["file"]}"><img src="{p["thumb"]}" alt="{p["title"]}" loading="lazy">'
            f'<div class="bottom-body"><span class="badge-sm" style="background:{p.get("accent", "#4a90d9")}">{p.get("badge", "✨ 라이프스타일")}</span>'
            f'<div class="bottom-title">{p["title"]}</div></div></a>' for p in bottom_posts
        )
        bottom_html = f'<div class="tier-label">🗂️ 지난 글 모아보기</div><div class="bottom-grid">{cards}</div>'

    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        category_pills = "".join(f'<span class="pill" style="background:{t["accent"]}">{t["badge"]}</span>' for t in CATEGORY_THEMES.values())
        f.write(INDEX_TEMPLATE.format(
            site_title=SITE_TITLE, site_tagline=SITE_TAGLINE, site_url=SITE_URL or ".", ga_snippet=_ga_snippet(),
            adsense_snippet=_adsense_snippet(), fonts_url=_google_fonts_url(),
            hero_html=hero_html, mid_html=mid_html, bottom_html=bottom_html, blog_json_ld=build_blog_index_json_ld(posts),
            category_pills=category_pills, search_console_meta=_search_console_meta(),
            footer_html='<div class="site-footer"><a href="about.html">블로그 소개</a>·<a href="privacy.html">개인정보처리방침</a>·<a href="contact.html">문의하기</a>'
                        f'<div style="margin-top:8px;">© {now_kst().year} {SITE_TITLE}</div></div>',
            translate_widget=_translate_widget(),
            site_title_short=SITE_TITLE[:12],
        ))
    return posts

def generate_static_pages() -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    common_kwargs = dict(site_title=SITE_TITLE, search_console_meta=_search_console_meta(), ga_snippet=_ga_snippet(), adsense_snippet=_adsense_snippet())
    pages = {
        "about.html": ("블로그 소개", f"<p>{SITE_TITLE}에 오신 것을 환영합니다.</p><p>{SITE_TAGLINE}</p><p>이 블로그는 다양한 주제의 정보를 정리해서 소개하며, 콘텐츠 제작 과정 일부에 AI 도구를 활용하고 있습니다. 게시된 정보는 참고용이며, 중요한 결정을 내리실 때는 반드시 공식 출처를 함께 확인해주세요.</p>"),
        "privacy.html": ("개인정보처리방침", "<p>본 블로그는 구글 애널리틱스(GA4) 및 구글 애드센스를 통해 방문자 통계와 광고를 제공할 수 있습니다. 이 과정에서 쿠키(Cookie)가 사용될 수 있으며, 쿠키를 통해 수집되는 정보에는 개인을 직접 식별할 수 있는 정보는 포함되지 않습니다.</p><h2>쿠키 및 광고</h2><p>구글을 포함한 제3자 광고 공급업체는 쿠키를 사용하여 사용자의 이전 방문 기록을 기반으로 광고를 게재합니다. 이용자는 <a href=\"https://adssettings.google.com\" target=\"_blank\">구글 광고 설정</a>에서 맞춤 광고를 비활성화할 수 있습니다.</p><h2>문의</h2><p>개인정보 관련 문의사항은 문의하기 페이지를 통해 연락 주시기 바랍니다.</p>"),
        "contact.html": ("문의하기", "<p>블로그 콘텐츠 관련 문의, 협업 제안, 오류 신고 등은 아래 이메일로 연락 주세요.</p><p><b>이메일:</b> 이 페이지의 문구를 직접 열어 본인의 연락처로 수정해주세요.</p>"),
    }
    for filename, (page_title, page_body) in pages.items():
        path = os.path.join(DOCS_DIR, filename)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(STATIC_PAGE_TEMPLATE.format(page_title=page_title, page_body=page_body, **common_kwargs))

def update_dashboard(posts: List[Dict[str, Any]]) -> None:
    rows = "\n".join(f'<tr><td>{p["date"]}</td><td>{p["title"]}</td><td><a href="{p["file"]}">보기</a></td></tr>' for p in posts)
    with open(os.path.join(DOCS_DIR, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(DASHBOARD_TEMPLATE.format(site_title=SITE_TITLE, post_count=len(posts), rows=rows))

def update_seo_files(posts: List[Dict[str, Any]]) -> None:
    if not SITE_URL: return
    url_entries = "\n".join(f"<url><loc>{SITE_URL}/{p['file']}</loc></url>" for p in posts)
    with open(os.path.join(DOCS_DIR, "sitemap.xml"), "w", encoding="utf-8") as f: f.write(SITEMAP_TEMPLATE.format(site_url=SITE_URL, url_entries=url_entries))
    with open(os.path.join(DOCS_DIR, "robots.txt"), "w", encoding="utf-8") as f: f.write(ROBOTS_TXT.format(site_url=SITE_URL))

def _blogger_configured() -> bool: return bool(BLOGGER_BLOG_ID and GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN)
def _get_blogger_access_token() -> str:
    resp = requests.post("https://oauth2.googleapis.com/token", data={"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "refresh_token": GOOGLE_REFRESH_TOKEN, "grant_type": "refresh_token"}, timeout=15)
    if not resp.ok:
        # [FIX] invalid_grant(리프레시 토큰 만료/폐기) 등 원인을 그대로 노출
        raise RuntimeError(f"블로거 토큰 갱신 실패 (HTTP {resp.status_code}): {resp.text[:500]}")
    return resp.json()["access_token"]

def strip_interactive_widgets(html_body: str) -> str:
    """[FIX] 자체 사이트 전용 "표 크게 보기" 버튼/모달에 onclick 같은 인라인 JS가 들어있는데,
    Blogger/워드프레스 모두 보안상 이런 인라인 스크립트를 걸러내면서 태그 구조가 깨져 표가
    텍스트로 그대로 노출되는 원인이 되었습니다. 외부 플랫폼에는 이 인터랙티브 위젯을 제거하고
    정적인(스크롤 가능한) 표만 남깁니다."""
    # "🔍 표 크게 보기" 버튼 div
    html_body = re.sub(r'<div style="text-align:right;margin:0 0 1\.2em;">.*?</div>', '', html_body, flags=re.DOTALL)
    # 확대 모달 (바깥/안쪽 div 두 겹)
    html_body = re.sub(r'<div id="tblzoom[^"]*"[^>]*>.*?</div>\s*</div>', '', html_body, flags=re.DOTALL)
    # 혹시 남아있는 onclick 속성 전체 제거 (안전망)
    html_body = re.sub(r'\s*onclick="[^"]*"', '', html_body)
    return html_body

def convert_tables_to_lists_for_wordpress(html_body: str) -> str:
    """[FIX-5차·최종] style 속성을 다 제거해도 <table> 태그 자체가 여전히 텍스트로 노출되는 것을
    스크린샷으로 재확인 — 즉 워드프레스닷컴은 속성 유무와 무관하게 <table> 태그 자체를 콘텐츠
    저장 과정에서 정상 처리하지 못합니다(자체 kses 화이트리스트에서 table 계열 태그가 제외되어
    있는 것으로 추정). <table>을 아예 쓰지 않고 행 단위 목록(<ul><li>)으로 변환해 문제 자체를
    피해갑니다."""
    def _cell_text(cell_html: str) -> str:
        return re.sub(r'<[^>]+>', '', cell_html).strip()

    def _convert(m: "re.Match") -> str:
        table_html = m.group(0)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
        if not rows:
            return ''
        header_cells = [_cell_text(c) for c in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', rows[0], re.DOTALL)]
        body_rows = rows[1:] if re.search(r'<th\b', rows[0]) else rows
        items = []
        for row in body_rows:
            cells = [_cell_text(c) for c in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.DOTALL)]
            if not any(cells):
                continue
            # [FIX] 한 줄에 " · "로 몰아넣으면 가독성이 떨어진다는 피드백 반영.
            # 첫 열은 이 항목의 제목처럼 굵게, 나머지는 "라벨: 값" 형태로 줄바꿈해서 나열.
            first_cell, rest_cells = cells[0], cells[1:]
            lines = [f'<b>{first_cell}</b>']
            for i, cell in enumerate(rest_cells, start=1):
                if not cell:
                    continue
                label = header_cells[i] if i < len(header_cells) else ''
                lines.append(f'<b>{label}</b> {cell}' if label else cell)
            items.append('<li style="margin-bottom:10px;">' + '<br>'.join(lines) + '</li>')
        return '<ul>' + ''.join(items) + '</ul>' if items else ''

    return re.sub(r'<table\b.*?</table>', _convert, html_body, flags=re.DOTALL)

def _make_wordpress_images_responsive(html_body: str) -> str:
    """[FIX] style 속성을 제거하면서 고정 픽셀 width="1280" 같은 값만 남아, 좁은 모바일 화면에서도
    그대로 1280px로 렌더링되어 이미지가 화면 밖으로 넘치던 문제를 해결합니다. width는 100%로,
    height는 제거해 종횡비를 유지한 채 컨테이너 폭에 맞게 자동으로 줄어들게 합니다."""
    def _fix(m: "re.Match") -> str:
        tag = m.group(0)
        tag = re.sub(r'\swidth="\d+"', ' width="100%"', tag)
        tag = re.sub(r'\sheight="\d+"', '', tag)
        return tag
    return re.sub(r'<img\b[^>]*>', _fix, html_body)

def build_wordpress_gutenberg_content(html_body: str) -> str:
    """[FIX-5차·최종 원인 확정] <table> 태그 자체가 속성 유무와 무관하게 워드프레스닷컴에서
    텍스트로 노출되는 것으로 최종 확인됨. table을 목록으로 완전히 대체하고, 나머지 style 속성도
    kses 안전성을 위해 제거합니다."""
    html_body = strip_interactive_widgets(html_body)
    html_body = convert_tables_to_lists_for_wordpress(html_body)  # [FIX] <table> 자체를 배제
    html_body = re.sub(r'\s+style="[^"]*"', '', html_body)  # 나머지 style 속성도 안전을 위해 제거
    html_body = _make_wordpress_images_responsive(html_body)  # [FIX] 이미지 크기 오버 방지
    return html_body

def _make_blogger_safe_html(html_body: str) -> str:
    html_body = strip_interactive_widgets(html_body)  # [FIX] 표 확대 모달 등 인라인 JS 제거
    # [FIX] base64는 Blogger의 목록/요약(snippet) 자동 생성 로직에서 글자수 제한에 걸려
    # 이미지가 아예 안 뜨는 부작용이 있었음. 이제 run()에서 외부 발행 전 사전 push를 보장하므로
    # 실제 GitHub Pages 절대경로 URL을 그대로 사용해도 안전함.
    if SITE_URL:
        html_body = html_body.replace('href="../posts/', f'href="{SITE_URL}/posts/').replace('href="../thumbs/', f'href="{SITE_URL}/thumbs/').replace('src="../thumbs/', f'src="{SITE_URL}/thumbs/')
    else:
        html_body = re.sub(r'<img src="\.\./thumbs/[^"]*"[^>]*>', "", html_body)
        html_body = re.sub(r'<a href="\.\./(posts|thumbs)/[^"]*"[^>]*>(.*?)</a>', r"\2", html_body)
    return html_body

def publish_to_blogger(article: Dict[str, Any], canonical_url: str, thumb_url: str, local_thumb_path: str) -> Optional[str]:
    if not _blogger_configured(): return None
    try:
        access_token = _get_blogger_access_token()
        theme = get_theme(article.get("category", "라이프스타일"))
        today = now_kst().strftime("%Y-%m-%d")
        blogger_json_ld = build_json_ld(article, canonical_url, thumb_url, today, platform="blogger")
        # [FIX] base64는 요약 스니펫 글자수 제한 안에서 이미지가 아예 안 뜨는 원인이었음.
        # 사전 push가 보장되므로 실제 GitHub Pages URL(thumb_url)을 그대로 사용.
        content_html = (
            f'{_translate_widget()}'
            f'<img src="{thumb_url}" style="max-width:100%;height:auto;border-radius:8px;" alt="{article["title"]}">'
            f'<span style="display:inline-block;background:{theme["accent"]};color:#fff;font-size:0.85em;font-weight:bold;padding:4px 12px;border-radius:999px;margin:14px 0 4px;">{theme["badge"]}</span>'
            f'{_make_blogger_safe_html(article["html_body"])}<script type="application/ld+json">{blogger_json_ld}</script>'
        )
        url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOGGER_BLOG_ID}/posts/"
        resp = requests.post(url, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}, json={"title": article["title"], "content": content_html}, timeout=30)
        if not resp.ok:
            # [FIX] "403 Client Error: Forbidden"만으로는 원인(토큰 만료/권한 부족/블로그ID 불일치)을
            # 알 수 없었음. 구글이 실제로 보낸 에러 본문을 그대로 노출해 원인 특정이 가능하게 함.
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        blogger_url = resp.json().get("url")
        logger.info(f"[블로거] 발행 완료: {blogger_url or '(URL 확인 불가)'}")
        return blogger_url
    except Exception as e:
        logger.error(f"[블로거] 발행 실패: {e}")
        return None

# =====================================================================
# [NEW] 워드프레스 동시 자동 발행 (워드프레스닷컴/Jetpack용 OAuth2 + 자체호스팅용 Basic Auth 자동 분기)
# - [FIX] *.wordpress.com 호스팅 사이트(예: kresonate.wordpress.com)는 자체 호스팅 워드프레스와 전혀
#   다른 API(public-api.wordpress.com)를 쓰고 Basic Auth(Application Password)를 지원하지 않습니다.
#   OAuth2만 가능합니다. 그래서 WORDPRESS_CLIENT_ID/SECRET이 설정되어 있으면 워드프레스닷컴 OAuth2
#   "password grant" 방식을 쓰고, 없으면 기존 자체호스팅용 Basic Auth 방식으로 동작합니다.
# - [워드프레스닷컴 준비물]
#   1) https://developer.wordpress.com/apps/new/ 에서 앱 등록 → Client ID / Client Secret 발급
#      (Redirect URI는 password grant에는 쓰이지 않으므로 아무 값이나 입력 가능, 예: https://localhost)
#   2) 워드프레스닷컴 계정 보안 설정(2단계 인증 활성화 후 my.wordpress.com/me/security)에서
#      Application Password 발급 → WORDPRESS_APP_PASSWORD 로 사용
#   3) WORDPRESS_URL에는 사이트 주소(예: kresonate.wordpress.com)를 입력
# - 미설정 시 조용히 건너뛰며(로그로 사유 표시), 실패해도 다른 발행 채널에는 영향 없습니다.
# =====================================================================
def _wordpress_configured() -> bool:
    return bool(WORDPRESS_URL and WORDPRESS_USERNAME and WORDPRESS_APP_PASSWORD)

def _wordpress_is_com_mode() -> bool:
    # Client ID/Secret이 있으면 워드프레스닷컴(OAuth2) 모드로 판단
    return bool(WORDPRESS_CLIENT_ID and WORDPRESS_CLIENT_SECRET)

def _get_wordpress_com_access_token() -> str:
    resp = requests.post(
        "https://public-api.wordpress.com/oauth2/token",
        data={
            "client_id": WORDPRESS_CLIENT_ID,
            "client_secret": WORDPRESS_CLIENT_SECRET,
            "grant_type": "password",
            "username": WORDPRESS_USERNAME,
            "password": WORDPRESS_APP_PASSWORD,
        },
        timeout=15,
    )
    if not resp.ok:
        # [FIX] resp.raise_for_status()만 쓰면 "400 Client Error"만 남고 정작 왜 실패했는지
        # (invalid_client/invalid_grant/invalid_request 등) 알 수 없었음 → 응답 본문을 그대로 노출
        raise RuntimeError(f"워드프레스닷컴 토큰 발급 실패 (HTTP {resp.status_code}): {resp.text[:500]}")
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"워드프레스닷컴 토큰 발급 실패: {data}")
    return data["access_token"]

def _upload_media_to_wordpress_com(site: str, access_token: str, local_path: str) -> Optional[str]:
    """워드프레스닷컴 미디어 라이브러리에 로컬 이미지를 업로드하고, 실제 호스팅되는 공개 URL을 반환합니다.
    [FIX] base64 data URI는 워드프레스닷컴 콘텐츠 정제(sanitizer)가 보안상 걸러내어 이미지가
    통째로 깨지는 원인이었습니다. 대신 정식 미디어 업로드 API로 실제 URL을 발급받아 사용합니다."""
    try:
        with open(local_path, "rb") as f:
            files = {"media[]": (os.path.basename(local_path), f, "image/webp")}
            resp = requests.post(
                f"https://public-api.wordpress.com/rest/v1.1/sites/{site}/media/new",
                headers={"Authorization": f"Bearer {access_token}"},
                files=files,
                timeout=30,
            )
        if not resp.ok:
            logger.warning(f"[워드프레스] 미디어 업로드 실패 (HTTP {resp.status_code}): {resp.text[:300]}")
            return None
        media_list = resp.json().get("media", [])
        return media_list[0]["URL"] if media_list else None
    except Exception as e:
        logger.warning(f"[워드프레스] 미디어 업로드 실패 '{local_path}': {e}")
        return None

def _replace_thumbs_with_wordpress_media(html_body: str, site: str, access_token: str) -> str:
    """본문 속 '../thumbs/파일명' 상대경로 이미지를 워드프레스 미디어 라이브러리에 업로드한 뒤
    실제 URL로 치환합니다. 같은 파일이 여러 번 나와도 한 번만 업로드하도록 캐싱합니다."""
    uploaded_cache: Dict[str, Optional[str]] = {}

    def _replace(m: "re.Match") -> str:
        filename = m.group(1)
        if filename not in uploaded_cache:
            local_path = os.path.join(DOCS_DIR, "thumbs", filename)
            uploaded_cache[filename] = _upload_media_to_wordpress_com(site, access_token, local_path)
        hosted_url = uploaded_cache[filename]
        return f'src="{hosted_url}"' if hosted_url else m.group(0)

    return re.sub(r'src="\.\./thumbs/([^"]+)"', _replace, html_body)

def _publish_to_wordpress_com(article: Dict[str, Any], source_url: str, local_thumb_path: str) -> None:
    access_token = _get_wordpress_com_access_token()
    theme = get_theme(article.get("category", "라이프스타일"))
    site = WORDPRESS_URL.replace("https://", "").replace("http://", "").rstrip("/")

    thumb_hosted_url = _upload_media_to_wordpress_com(site, access_token, local_thumb_path)
    safe_body = _replace_thumbs_with_wordpress_media(article["html_body"], site, access_token)

    hero_html = (
        (f'<img src="{thumb_hosted_url}" alt="{article["title"]}" width="1280" height="720" /><br>' if thumb_hosted_url else "")
        + f'<span style="display:inline-block;background:{theme["accent"]};color:#fff;font-size:0.85em;'
        f'font-weight:bold;padding:4px 12px;border-radius:999px;margin:10px 0 4px;">{theme["badge"]}</span>'
    )
    # [FIX] 표만 워드프레스 네이티브 core/table 블록으로 변환하고 나머지는 HTML 블록으로 감쌈
    # (본문 전체를 하나의 Custom HTML 블록으로 통째로 감싸는 방식이 표 깨짐/앱 미리보기 불가의 원인이었음)
    content_html = build_wordpress_gutenberg_content(hero_html + safe_body)
    payload = {
        "title": article["title"],
        "content": content_html,
        "status": "publish",
        "excerpt": article.get("meta_description", ""),
    }
    resp = requests.post(
        f"https://public-api.wordpress.com/rest/v1.1/sites/{site}/posts/new",
        headers={"Authorization": f"Bearer {access_token}"},
        data=payload,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"워드프레스닷컴 글 발행 실패 (HTTP {resp.status_code}, site={site}): {resp.text[:500]}")
    logger.info(f"[워드프레스] 발행 완료(워드프레스닷컴): {resp.json().get('URL', '(URL 확인 불가)')}")

def _upload_media_to_wordpress_self_hosted(auth_header: str, local_path: str) -> Optional[Dict[str, Any]]:
    """자체호스팅 워드프레스 미디어 라이브러리에 업로드하고 {id, url}을 반환합니다."""
    try:
        with open(local_path, "rb") as f:
            img_bytes = f.read()
        resp = requests.post(
            f"{WORDPRESS_URL}/wp-json/wp/v2/media",
            headers={
                "Authorization": auth_header,
                "Content-Disposition": f'attachment; filename="{os.path.basename(local_path)}"',
                "Content-Type": "image/webp",
            },
            data=img_bytes,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"id": data.get("id"), "url": data.get("source_url")}
    except Exception as e:
        logger.warning(f"[워드프레스] 미디어 업로드 실패 '{local_path}': {e}")
        return None

def _replace_thumbs_with_wordpress_self_hosted_media(html_body: str, auth_header: str) -> str:
    uploaded_cache: Dict[str, Optional[str]] = {}

    def _replace(m: "re.Match") -> str:
        filename = m.group(1)
        if filename not in uploaded_cache:
            local_path = os.path.join(DOCS_DIR, "thumbs", filename)
            media = _upload_media_to_wordpress_self_hosted(auth_header, local_path)
            uploaded_cache[filename] = media["url"] if media else None
        hosted_url = uploaded_cache[filename]
        return f'src="{hosted_url}"' if hosted_url else m.group(0)

    return re.sub(r'src="\.\./thumbs/([^"]+)"', _replace, html_body)

def _publish_to_wordpress_self_hosted(article: Dict[str, Any], canonical_url: str, local_thumb_path: str) -> None:
    auth_token = base64.b64encode(f"{WORDPRESS_USERNAME}:{WORDPRESS_APP_PASSWORD}".encode("utf-8")).decode("ascii")
    auth_header = f"Basic {auth_token}"
    theme = get_theme(article.get("category", "라이프스타일"))

    # 대표이미지(썸네일) 업로드 (실패해도 본문 발행은 계속 진행)
    featured_media_id = None
    thumb_media = _upload_media_to_wordpress_self_hosted(auth_header, local_thumb_path)
    if thumb_media:
        featured_media_id = thumb_media.get("id")

    # [FIX] base64 data URI는 워드프레스 콘텐츠 정제 필터가 걸러낼 수 있어(워드프레스닷컴에서 실제로 발생),
    # 자체호스팅에서도 동일 위험을 피하기 위해 실제 미디어 업로드 방식으로 통일
    safe_body = _replace_thumbs_with_wordpress_self_hosted_media(article["html_body"], auth_header)
    hero_html = (
        f'<span style="display:inline-block;background:{theme["accent"]};color:#fff;font-size:0.85em;'
        f'font-weight:bold;padding:4px 12px;border-radius:999px;margin:0 0 14px;">{theme["badge"]}</span>'
    )
    # [FIX] 표만 네이티브 core/table 블록으로 변환, 나머지는 HTML 블록으로 감쌈
    content_html = build_wordpress_gutenberg_content(hero_html + safe_body)
    payload = {
        "title": article["title"],
        "content": content_html,
        "status": "publish",
        "excerpt": article.get("meta_description", ""),
    }
    if featured_media_id:
        payload["featured_media"] = featured_media_id

    resp = requests.post(
        f"{WORDPRESS_URL}/wp-json/wp/v2/posts",
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    logger.info(f"[워드프레스] 발행 완료(자체호스팅): {resp.json().get('link', '(URL 확인 불가)')}")

def publish_to_wordpress(article: Dict[str, Any], source_url: str, thumb_url: str, local_thumb_path: str) -> None:
    if not _wordpress_configured():
        missing = [name for name, val in [
            ("WORDPRESS_URL", WORDPRESS_URL),
            ("WORDPRESS_USERNAME", WORDPRESS_USERNAME),
            ("WORDPRESS_APP_PASSWORD", WORDPRESS_APP_PASSWORD),
        ] if not val]
        logger.info(f"[워드프레스] 미설정으로 건너뜁니다. (비어있는 값: {', '.join(missing)})")
        return
    try:
        if _wordpress_is_com_mode():
            _publish_to_wordpress_com(article, source_url, local_thumb_path)
        else:
            logger.warning(
                "[워드프레스] WORDPRESS_CLIENT_ID/SECRET이 없어 자체호스팅용 Basic Auth 방식을 시도합니다. "
                "워드프레스닷컴(*.wordpress.com) 사이트라면 이 방식은 항상 실패합니다 — "
                "https://developer.wordpress.com/apps/new/ 에서 앱을 등록하세요."
            )
            _publish_to_wordpress_self_hosted(article, source_url, local_thumb_path)
    except Exception as e:
        logger.error(f"[워드프레스] 발행 실패: {e}")

def ensure_nojekyll() -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    if not os.path.exists(os.path.join(DOCS_DIR, ".nojekyll")):
        open(os.path.join(DOCS_DIR, ".nojekyll"), "w").close()

# =====================================================================
# [NEW] 외부 발행(Blogger/워드프레스) 전에 GitHub Pages로 먼저 push
# - [FIX] 기존에는 git 커밋/푸시가 워크플로의 더 나중 단계(별도 셸 스텝)에서 실행되어,
#   Blogger/워드프레스가 이미지 URL을 참조하는 시점엔 그 파일이 아직 GitHub Pages에
#   존재하지 않는 타이밍 버그가 있었습니다(base64로 임시 땜질했던 이유이기도 함).
# - 이제 파이썬 스크립트 안에서 외부 발행 직전에 먼저 커밋+푸시를 완료시켜, 이후
#   Blogger/워드프레스가 참조하는 GitHub Pages 이미지 URL이 항상 실제로 존재하도록 보장합니다.
# - 실패해도 예외를 던지지 않고 False만 반환합니다 (git push 실패가 전체 파이프라인을
#   중단시키지 않도록 하기 위함; 워크플로의 마지막 커밋 스텝이 안전망으로 남아있음).
# =====================================================================
def commit_and_push_changes() -> bool:
    try:
        subprocess.run(["git", "config", "user.name", "auto-blog-bot"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "auto-blog-bot@users.noreply.github.com"], check=True, capture_output=True)
        subprocess.run(["git", "add", "docs", "keywords_queue.json"], check=True, capture_output=True)
        diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff_check.returncode == 0:
            logger.info("[git] 변경사항 없음, 사전 push 생략")
            return True
        commit_msg = f"자동 파이프라인 실행(사전 push): {now_kst().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        logger.info("[git] GitHub Pages 사전 push 완료 (외부 발행 시 이미지 URL이 실제로 존재함을 보장)")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        logger.warning(f"[git] 사전 push 실패, 외부 발행 시 이미지가 일시적으로 깨질 수 있습니다: {stderr[:300]}")
        return False

def run() -> None:
    is_refresh_only = len(sys.argv) > 1 and sys.argv[1].strip().lower() == "refresh"

    # [개편] 트렌드 감지 대신 에버그린 주제뱅크에서 큐를 보충
    refill_evergreen_queue()
    if is_refresh_only:
        return

    # 수동 제목 입력 여부 확인
    manual_title = ""
    if len(sys.argv) > 1 and sys.argv[1].strip() and sys.argv[1].strip().lower() not in ["publish", "refresh"]:
        manual_title = sys.argv[1].strip()

    # [FIX] 기존에는 "제목을 직접 입력했을 때만" 하루 발행 한도를 무시했습니다. 그런데 GitHub Actions에서
    # 제목 입력 없이 workflow_dispatch(수동 실행 버튼)로 돌린 경우도 "수동 발행"이므로, 자동 스케줄(cron)과
    # 구분해 하루 한도와 무관하게 항상 발행되도록 합니다. GITHUB_EVENT_NAME은 GitHub Actions가 모든 스텝에
    # 자동으로 제공하는 환경변수라 워크플로 파일 수정 없이도 바로 동작합니다.
    is_manual_dispatch = os.environ.get("GITHUB_EVENT_NAME", "").strip() == "workflow_dispatch"
    bypass_daily_limit = bool(manual_title) or is_manual_dispatch

    title = ""
    if manual_title:
        title = manual_title
    else:
        # [개편] 트렌드 감지 게이트가 사라졌으므로 하루 발행 한도로 콘텐츠 팜화 방지 (단, 수동 실행은 예외)
        if not bypass_daily_limit and not check_daily_limit():
            logger.info(f"오늘의 발행 한도({DAILY_PUBLISH_LIMIT}회)를 모두 소진하여 포스팅을 생략합니다.")
            return

        queue = load_queue()
        if not queue.get("pending"):
            logger.info("대기 중인 에버그린 주제가 없습니다. EVERGREEN_TOPIC_BANK를 확인해주세요.")
            return
            
        title = pick_next_topic(queue)
        if not title:
            logger.info("대기 중인 에버그린 주제가 없습니다. EVERGREEN_TOPIC_BANK를 확인해주세요.")
            return
        queue.setdefault("completed", []).append(title)
        save_queue(queue)

    logger.info(f"[처리 시작] 제목: {title}")

    ensure_nojekyll()
    ensure_brand_assets()
    ensure_pwa_assets()  # [NEW] PWA 매니페스트/아이콘/서비스워커
    generate_static_pages()

    article = generate_article(title)
    article = fix_character_count_claims(article)  # [NEW] AI의 글자 수 오기재를 Python이 강제 교정
    logger.info(f"글 생성 완료: {article['title']}")

    article = add_internal_link(article)
    article = insert_manual_ads(article)
    article = add_coupang_markup(article)
    article = add_ymyl_disclaimer(article)

    post_meta, json_ld, thumb_url, local_thumb_path, post_url = save_post(article)
    posts = update_index(post_meta)

    update_dashboard(posts)
    update_seo_files(posts)

    commit_and_push_changes()  # [NEW] 외부 발행 전 GitHub Pages에 이미지가 실제로 존재하도록 먼저 push

    blogger_url = publish_to_blogger(article, post_url, thumb_url, local_thumb_path)
    # [FIX] 워드프레스/블로거 본문 하단 "원문" 링크는 요청에 따라 완전히 제거함.
    # source_url 인자는 더 이상 본문에 노출되지 않지만, 추후 필요시를 대비해 시그니처는 유지.
    publish_to_wordpress(article, blogger_url or post_url, thumb_url, local_thumb_path)

    if not bypass_daily_limit:
        increment_daily_count()

    logger.info(f"저장 완료: docs/{post_meta['file']}, docs/{post_meta['thumb']}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error(f"스크립트 실행 중 치명적인 오류 발생: {e}")
        sys.exit(1)
