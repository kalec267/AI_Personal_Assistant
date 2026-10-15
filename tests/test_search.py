import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from assistant.actions import validate_calls, perform
from assistant.core import UserError
from assistant.search import search_spec, parse_search, search_keywords, open_search
from assistant.service import AssistantService
from assistant.settings import save_settings


class SearchTests(unittest.TestCase):
    def test_korean_queries_are_encoded_on_expected_engine_and_filter(self):
        query = '생성형 AI & "동영상" #2026'
        for provider in ("naver", "google"):
            for kind in ("web", "news", "video"):
                with self.subTest(provider=provider, kind=kind):
                    spec = search_spec(query, provider, kind)
                    url = urlsplit(spec["url"])
                    self.assertEqual(url.scheme, "https")
                    self.assertIn(provider, url.hostname)
                    parameters = parse_qs(url.query)
                    self.assertEqual(parameters["query" if provider == "naver" else "q"], [query])
                    if provider == "naver":
                        self.assertEqual(parameters["where"], [{"web": "nexearch", "news": "news", "video": "video"}[kind]])
                    elif kind != "web":
                        self.assertEqual(parameters["tbm"], ["nws" if kind == "news" else "vid"])

    def test_natural_search_preserves_keywords_and_avoids_normal_commands(self):
        for text, provider, kind, query in [("구글에서 인공지능 뉴스 검색해줘", "google", "news", "인공지능"),
                                           ("네이버에서 파이썬 강의 동영상 찾아줘", "naver", "video", "파이썬 강의"),
                                           ("구글 고양이 사진 검색", "google", "web", "고양이 사진")]:
            with self.subTest(text=text):
                result = parse_search(text)
                self.assertIsNotNone(result)
                self.assertEqual((result["provider"], result["kind"], result["query"]), (provider, kind, query))
        for text in ("오늘 일정 보여줘", "메모장 실행", "구글에서 뉴스 검색하지 마", "그걸 뉴스 검색해줘", "검색이 뭔지 알려줘"):
            with self.subTest(text=text):
                self.assertIsNone(parse_search(text))

    def test_ai_off_search_works_without_api_and_browser_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as folder, patch("urllib.request.urlopen") as request:
            result = AssistantService(folder).execute("네이버에서 경제 뉴스 검색해줘")
            self.assertEqual(result["search"]["kind"], "news")
            request.assert_not_called()
        with patch("assistant.search.webbrowser.open", return_value=True) as browser:
            self.assertIn("열기를 요청", open_search("경제", "naver", "news"))
            browser.assert_called_once_with(search_spec("경제", "naver", "news")["url"], new=2)
        with patch("assistant.search.webbrowser.open", return_value=False), self.assertRaisesRegex(UserError, "기본 브라우저"):
            open_search("경제")

    def test_spoken_search_extracts_keywords_and_content_kind(self):
        cases = [
            ("구글에서 삼성에 대한 기사 찾아줘", "google", "news", "삼성 기사"),
            ("구글에서 삼성에 대한 기사를 찾아줘", "google", "news", "삼성 기사"),
            ("네이버에서 삼성에 관한 최신 기사를 좀 찾아주세요.", "naver", "news", "삼성 최신 기사"),
            ("구글에서 삼성에 관련된 기사 보여줘", "google", "news", "삼성 기사"),
            ("구글에서 삼성에 대해서 검색해줘", "google", "web", "삼성"),
            ("네이버에서 전기차에 관한 뉴스를 찾아줘", "naver", "news", "전기차"),
            ("네이버에서 파이썬 관련된 동영상을 보여주세요", "naver", "video", "파이썬"),
            ("삼성에 대한 기사 찾아줘", "google", "news", "삼성 기사"),
            ("구글에서 대한항공에 대한 2026년 10월 기사 찾아줘", "google", "news", "대한항공 2026년 10월 기사"),
            ("구글에서 가을 사진 검색해줘", "google", "web", "가을 사진"),
        ]
        for text, provider, kind, query in cases:
            with self.subTest(text=text):
                result = parse_search(text)
                self.assertIsNotNone(result)
                self.assertEqual((result["provider"], result["kind"], result["query"]), (provider, kind, query))
                self.assertEqual(parse_qs(urlsplit(result["url"]).query)["query" if provider == "naver" else "q"], [query])

    def test_exact_phrases_names_and_search_operators_are_preserved(self):
        cases = [
            ('구글에서 "삼성에 대한 기사" 검색해줘', '"삼성에 대한 기사"'),
            ('구글에서 "삼성에 대한 기사"를 검색해줘', '"삼성에 대한 기사"'),
            ('구글에서 “에 대한” 검색해줘', '“에 대한”'),
            ('구글에서 대한항공 가을 아를 검색해줘', '대한항공 가을 아를'),
            ('구글에서 site:news.naver.com 삼성 -주식 after:2026-10-01 검색해줘', 'site:news.naver.com 삼성 -주식 after:2026-10-01'),
            ('구글에서 "삼성  전자"에 대한 기사 찾아줘', '"삼성  전자" 기사'),
        ]
        for text, query in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_search(text)["query"], query)
        # The dedicated search dialog accepts literal keywords, not commands.
        self.assertEqual(search_spec("삼성에 대한 기사")["query"], "삼성에 대한 기사")

    def test_context_references_and_negated_requests_are_not_literal_searches(self):
        for text in ("그걸 뉴스로 찾아줘", "구글에서 그거에 대한 기사 찾아줘", "그 내용에 관한 동영상 보여줘",
                     "구글에서 삼성에 대한 기사 검색하지 마", "삼성에 대한 기사 찾아주지 마"):
            with self.subTest(text=text):
                self.assertIsNone(parse_search(text))

    def test_keyword_cleanup_is_repeatable_and_keeps_meaningful_words(self):
        for query, expected in (("  삼성에  대한   최신 기사  ", "삼성 최신 기사"),
                                ("AI에 관련된 강의를", "AI 강의"),
                                ("사과 관련된 기사", "사과 기사"),
                                ("결과 관련된 기사", "결과 기사"),
                                ("대한항공", "대한항공"), ('"삼성에 대한 기사"', '"삼성에 대한 기사"')):
            with self.subTest(query=query):
                self.assertEqual(search_keywords(query), expected)
                self.assertEqual(search_keywords(expected), expected)

    def test_service_search_uses_optimized_query_without_calling_ai(self):
        with tempfile.TemporaryDirectory() as folder:
            save_settings(folder, "", False, ai_mode="local", ollama_model="qwen3:4b")
            service = AssistantService(folder)
            with patch.object(service.ollama, "request") as ai:
                result = service.execute("구글에서 삼성에 대한 기사 찾아줘")
                ai.assert_not_called()
            self.assertEqual(result["search"]["query"], "삼성 기사")
            self.assertEqual(result["search"]["kind"], "news")
            self.assertIn("삼성 기사", result["message"])

    def test_ai_search_summary_and_browser_use_the_same_optimized_query(self):
        with tempfile.TemporaryDirectory() as folder:
            service = AssistantService(folder)
            call = {"function": {"name": "search_web", "arguments": {"query": "삼성에 대한 기사", "provider": "google", "kind": "news"}}}
            action = validate_calls([call], service.programs)[0]
            self.assertEqual(action["args"]["query"], "삼성 기사")
            self.assertIn("삼성 기사", action["summary"])
            self.assertEqual(call["function"]["arguments"]["query"], "삼성에 대한 기사")
            with patch("assistant.search.webbrowser.open", return_value=True) as browser:
                perform(action, service.programs)
            self.assertEqual(parse_qs(urlsplit(browser.call_args.args[0]).query)["q"], ["삼성 기사"])

    def test_search_tool_is_validated_and_cannot_change_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            service = AssistantService(folder)
            call = {"function": {"name": "search_web", "arguments": {"query": "경제", "provider": "naver", "kind": "news"}}}
            action = validate_calls([call], service.programs)[0]
            with patch("assistant.search.webbrowser.open", return_value=True) as browser:
                perform(action, service.programs)
                self.assertEqual(urlsplit(browser.call_args.args[0]).hostname, "search.naver.com")
            for key, value in (("provider", "evil.example"), ("kind", "execute"), ("query", "\ncommand")):
                bad = {"function": {"name": "search_web", "arguments": dict(call["function"]["arguments"], **{key: value})}}
                with self.subTest(key=key), self.assertRaises(UserError):
                    validate_calls([bad], service.programs)

    def test_search_keyword_is_available_for_follow_up_without_fabricating_news(self):
        with tempfile.TemporaryDirectory() as folder:
            save_settings(folder, "", False, ai_mode="local", ollama_model="qwen3:4b")
            service = AssistantService(folder)
            service.execute("구글에서 인공지능 뉴스 검색해줘")
            with patch.object(service.ollama, "request", return_value={"content": "검색어는 인공지능이었어요", "calls": []}) as request:
                service.execute("검색어가 뭐였지?")
                history = request.call_args.kwargs["history"]
                self.assertIn("인공지능", history[-1]["content"])
                self.assertIn("내용은 조회하지 않았습니다", history[-1]["content"])


if __name__ == "__main__":
    unittest.main()
