"""OpenAI Chat Completions transport with Luna's shared prompts and analysis.

Only the selected provider is contacted. Returned tool calls go through the
same application validation and confirmation flow as Ollama proposals.
"""
import json
import re
import urllib.error
import urllib.request

from .core import UserError
from .ollama import Ollama


class OpenAI(Ollama):
    name = "OpenAI"

    def _validate(self, mode, model):
        if mode != "openai":
            raise UserError("ChatGPT · OpenAI API 연결을 선택하세요.")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}", model):
            raise UserError("올바른 OpenAI 모델 이름을 입력해 주세요.")

    def _complete(self, mode, payload, *, analysis, images):
        key = self.credentials.get()
        if not key:
            raise UserError("설정 → AI · 대화에서 ChatGPT를 선택하고 OpenAI API 키를 등록해 주세요.")
        messages = []
        for item in payload["messages"]:
            message = {"role": item["role"], "content": item["content"]}
            if item.get("images"):
                message["content"] = [{"type": "text", "text": item["content"]}] + [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}}
                    for data in item["images"]]
            messages.append(message)
        body = {"model": payload["model"], "messages": messages, "stream": False,
                "max_completion_tokens": 1800}
        if not analysis:
            body["tools"] = payload["tools"]
            body["tool_choice"] = "auto"
        request = urllib.request.Request("https://api.openai.com/v1/chat/completions",
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise UserError("OpenAI 응답이 너무 큽니다. 요청 범위를 줄여 주세요.")
            result = json.loads(raw)
            choices = result.get("choices") if isinstance(result, dict) and not result.get("error") else None
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise UserError("OpenAI 응답 형식이 올바르지 않습니다.")
            choice = choices[0]
            if choice.get("finish_reason") not in {"stop", "tool_calls"}:
                raise UserError("OpenAI 답변이 완성되지 않았어요. 아무 동작도 실행하지 않았습니다. 요청을 줄이거나 모델을 확인하세요.")
            message = choice.get("message")
            parsed = self._parse_message(message, analysis=analysis)
            normalized = []
            for call in parsed["calls"]:
                function = call.get("function") if isinstance(call, dict) and call.get("type") == "function" else None
                if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                    raise UserError("OpenAI 도구 호출 형식이 올바르지 않습니다. 아무 동작도 실행하지 않았습니다.")
                arguments = json.loads(function["arguments"])
                if not isinstance(arguments, dict):
                    raise UserError("OpenAI 도구 인자는 JSON 객체여야 합니다. 아무 동작도 실행하지 않았습니다.")
                normalized.append({"function": {"name": function["name"], "arguments": arguments}})
            return {"content": parsed["content"], "calls": normalized}
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise UserError(f"OpenAI 인증·접근 실패 (HTTP {error.code}). 설정의 OpenAI API 키와 프로젝트·모델 접근 권한을 확인하세요.") from None
            if error.code == 429:
                raise UserError("OpenAI 사용 한도 또는 API 잔액을 확인하세요. 잠시 후 다시 시도하세요. ChatGPT 구독과 API 요금은 별도입니다.") from None
            if error.code == 404:
                raise UserError("OpenAI 모델을 찾거나 사용할 수 없어요. 모델 이름과 계정의 접근 권한을 확인하세요.") from None
            if error.code == 400:
                raise UserError("OpenAI 요청을 처리하지 못했어요. Chat Completions의 도구 호출을 지원하는 모델을 선택하고, 이미지 첨부 시 이미지 입력 지원도 확인하세요.") from None
            raise UserError(f"OpenAI 요청 실패 (HTTP {error.code}). 잠시 후 다시 시도해 주세요.") from None
        except UserError:
            raise
        except (OSError, ValueError, TypeError):
            raise UserError("OpenAI 연결 또는 응답을 확인하지 못했어요. 인터넷 연결과 모델 설정을 확인하세요.") from None
