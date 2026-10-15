"""Ollama tool-calling client; the model proposes actions, never executes them."""
import datetime as dt
import json
import re
import urllib.error
import urllib.request

from .core import UserError


def tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


def tools(catalog):
    return [
        tool("launch_program", "등록된 프로그램 실행", {"app_id": {"type": "string", "enum": [app["id"] for app in catalog]}}, ["app_id"]),
        tool("set_volume", "기본 오디오 장치 볼륨·음소거 제어", {
            "mode": {"type": "string", "enum": ["set", "up", "down", "mute", "unmute"]},
            "value": {"type": "integer", "minimum": 0, "maximum": 100}}, ["mode"]),
        tool("open_website", "사용자가 요청한 HTTPS 웹 사이트 열기", {"url": {"type": "string"}}, ["url"]),
        tool("media_control", "미디어 재생·일시정지, 다음 곡, 이전 곡", {"command": {"type": "string", "enum": ["play_pause", "next", "previous"]}}, ["command"]),
        tool("create_event", "제목·시작·종료가 명확한 일정 초안. PC 현지 ISO 시간, 시간대 없음", {
            "title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}, ["title", "start", "end"]),
        tool("list_events", "일정 조회. 날짜를 생략하면 전체", {"day": {"type": "string", "description": "YYYY-MM-DD"}}, []),
    ]


class Ollama:
    def __init__(self, credentials):
        self.credentials = credentials

    def request(self, mode, model, text, catalog):
        if mode not in {"local", "cloud"}:
            raise UserError("Ollama 로컬 또는 클라우드를 선택하세요.")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}", model):
            raise UserError("올바른 Ollama 모델 이름을 입력해 주세요.")
        headers = {"Content-Type": "application/json"}
        endpoint = "http://127.0.0.1:11434/api/chat"
        if mode == "cloud":
            endpoint = "https://ollama.com/api/chat"
            key = self.credentials.get()
            if not key:
                raise UserError("AI 설정에서 재발급한 Ollama API 키를 등록해 주세요.")
            headers["Authorization"] = "Bearer " + key
        prompt = (
            "당신은 한국어 개인 비서 루나입니다. 명확한 PC 조작 요청만 제공된 도구로 제안하세요. "
            "아직 실행되지 않았으므로 완료했다고 말하지 마세요. 질문·부정·취소에는 조작을 제안하지 마세요. "
            "이전에 한 작업을 가정하지 마세요. 한 요청에 최대 5개 동작입니다. 등록 앱 ID만 사용하세요. "
            "셸 명령, 임의 파일 조작, 코드 실행은 지원하지 않습니다. 정보가 부족하면 질문하세요. "
            "일정 생성은 다른 동작과 섞지 마세요. 날짜·시각·제목이 누락되거나 오전/오후가 불명확하면 물어보세요. "
            "일정 종료를 말하지 않으면 1시간입니다. URL은 사용자가 지정한 HTTPS 사이트만 제안하세요. "
            "소리만 줄이라는 요청에 음소거 해제를 추가하지 마세요. "
            f"현재 PC 시각: {dt.datetime.now().astimezone().isoformat()}. "
            "등록 프로그램: " + json.dumps(catalog, ensure_ascii=False)
        )
        payload = {"model": model, "stream": False, "messages": [
            {"role": "system", "content": prompt}, {"role": "user", "content": text}],
            "tools": tools(catalog), "options": {"temperature": 0, "num_predict": 1800}}
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read(1024 * 1024))
            if not isinstance(result, dict):
                raise UserError("Ollama 응답 형식이 올바르지 않습니다.")
            message = result.get("message")
            if not isinstance(message, dict) or result.get("error"):
                raise UserError("Ollama 응답 형식이 올바르지 않아요. 도구 호출을 지원하는 모델을 선택하세요.")
            calls = message.get("tool_calls") or []
            content = message.get("content") or ""
            if not isinstance(calls, list) or len(calls) > 5 or not isinstance(content, str):
                raise UserError("Ollama가 올바르지 않은 동작을 제안했어요. 아무 동작도 실행하지 않았습니다.")
            if not calls and not content.strip():
                raise UserError("Ollama 답변이 비어 있어요. 다른 모델을 선택하거나 다시 시도해 주세요.")
            return {"content": content[:6000], "calls": calls}
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                if mode == "cloud":
                    raise UserError(f"Ollama 클라우드 인증 실패 (HTTP {error.code}). "
                                    "‘AI · 프로그램 설정’의 ‘Ollama API 키’에 Ollama에서 발급한 키를 다시 등록하고 연결 테스트를 눌러 주세요.") from None
                raise UserError(f"Ollama 로컬 서버 접근 거부 (HTTP {error.code}). 로컬 Ollama 실행 상태와 접근 정책을 확인해 주세요.") from None
            if error.code == 429:
                raise UserError("Ollama 사용 한도에 도달했어요. 잠시 후 다시 시도하거나 로컬 모드를 사용하세요.") from None
            if error.code == 404:
                raise UserError("Ollama 모델을 찾지 못했어요. 모델 이름과 다운로드 상태를 확인하세요.") from None
            raise UserError(f"Ollama 요청 실패 (HTTP {error.code}). 도구 호출 지원 모델인지 확인하세요.") from None
        except UserError:
            raise
        except (OSError, ValueError, TypeError):
            raise UserError("Ollama에 연결하지 못했어요. 로컬 앱 실행·모델 다운로드 또는 인터넷 연결을 확인하세요.") from None
