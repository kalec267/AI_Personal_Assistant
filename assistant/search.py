"""Free search result pages; queries are encoded, never scraped or executed."""
import re
from urllib.parse import urlencode
import webbrowser

from .core import UserError

PROVIDERS = {"google": "구글", "naver": "네이버"}
KINDS = {"web": "전체", "news": "뉴스", "video": "동영상"}

# Quoted phrases are intentional exact searches, including their particles.
QUOTED = re.compile(r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|“[^”]*”|‘[^’]*’)''')
RELATION = re.compile(r"에\s*(?:대한|대해(?:서)?|관한|관해(?:서)?|관련(?:된|한|해서|하여))(?=\s|$)")
OBJECT = re.compile(r"(뉴스|기사|동영상|영상|비디오|사진|이미지|정보|자료|소식|강의|방법|후기|리뷰)(?:을|를)(?=\s*$)")


def search_keywords(query):
    """Remove clear request grammar without stemming names or quoted phrases."""
    parts = QUOTED.split(query)
    for index in range(0, len(parts), 2):
        part = RELATION.sub(" ", parts[index])
        part = re.sub(r"(?<!\S)관련(?:된|한|해서|하여)(?=\s|$)", " ", part)
        if index == len(parts) - 1:
            part = OBJECT.sub(r"\1", part)
            # A particle after a closing quote belongs to the request itself.
            if index:
                part = re.sub(r"^(?:을|를)(?=\s|$)", "", part)
        parts[index] = re.sub(r"\s+", " ", part)
    return "".join(parts).strip()


def search_spec(query, provider="google", kind="web"):
    if not isinstance(provider, str) or provider not in PROVIDERS or not isinstance(kind, str) or kind not in KINDS:
        raise UserError("네이버·구글과 전체·뉴스·동영상 중 선택하세요.")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200 or any(ord(char) < 32 for char in query):
        raise UserError("검색어는 1~200자로 입력하세요.")
    query = query.strip()
    if provider == "naver":
        url = "https://search.naver.com/search.naver?" + urlencode({"where": {"web": "nexearch", "news": "news", "video": "video"}[kind], "query": query})
    else:
        parameters = {"q": query}
        if kind != "web":
            parameters["tbm"] = "nws" if kind == "news" else "vid"
        url = "https://www.google.com/search?" + urlencode(parameters)
    return {"query": query, "provider": provider, "kind": kind, "url": url,
            "label": f"{PROVIDERS[provider]} {KINDS[kind]} 검색: {query}"}


def parse_search(text, default_provider="google", default_kind="web"):
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(?:(네이버|구글)(?:에서|로)?\s*)?(.+?)\s*(?:좀\s*)?"
                         r"(검색해\s*줘|검색해\s*주세요|검색|찾아\s*줘|찾아\s*주세요|보여\s*줘|보여\s*주세요)[.!?]?\s*", text)
    if not match:
        return None
    provider, query, verb = match.groups()
    query = search_keywords(query)
    category = re.search(r"(?:^|\s)(뉴스|기사|동영상|영상|비디오)(?:로)?$", query)
    kind = category.group(1) if category else None
    if not provider and not kind and not verb.startswith("검색"):
        return None
    topic = query[:category.start()].strip() if category else query
    if topic in {"그거", "그걸", "이거", "이걸", "그 내용", "이 내용", "방금 내용"}:
        return None  # Let Ollama resolve references using the recent conversation.
    if category:
        # Keep '기사' as the requested keyword: '삼성에 대한 기사' → '삼성 기사'.
        query = (topic + " 기사").strip() if kind == "기사" else topic or kind
    if not query:
        return None
    provider = {"구글": "google", "네이버": "naver"}.get(provider, default_provider)
    kind = "news" if kind in {"뉴스", "기사"} else "video" if kind else default_kind
    return search_spec(query, provider, kind)


def open_search(query, provider="google", kind="web"):
    spec = search_spec(query, provider, kind)
    if not webbrowser.open(spec["url"], new=2):
        raise UserError("검색 결과를 열지 못했어요. Windows 기본 브라우저를 확인하세요.")
    return spec["label"] + " · 기본 브라우저에 결과 페이지 열기를 요청했어요."
