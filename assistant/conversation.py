"""Optional conversational AI. API credentials stay in the app process."""
import json
import os
import urllib.error
import urllib.request

from .core import UserError


def enabled():
    return bool(os.environ.get("LUNA_OPENAI_API_KEY"))


def answer(text):
    key = os.environ.get("LUNA_OPENAI_API_KEY")
    if not key:
        return ("저는 루나예요. ‘내일 오후 3시 팀 회의 일정 추가’, ‘오늘 일정 보여줘’, "
                "‘메모장 실행’, ‘볼륨 30퍼센트’라고 말해 보세요. "
                "자유로운 AI 대화는 LUNA_OPENAI_API_KEY를 설정하면 사용할 수 있어요.")
    payload = {
        "model": os.environ.get("LUNA_OPENAI_MODEL", "gpt-4.1-mini"),
        "instructions": "당신은 개인 비서 루나입니다. 한국어로 간결하고 친절하게 답하세요. "
        "당신은 대화만 할 수 있으며 컴퓨터 조작, 일정 저장, 조회를 수행하거나 확인할 수 없습니다. "
        "이를 수행했다고 주장하지 마세요. 조작 요청에는 앱의 지원 명령이나 일정 폼 사용을 안내하세요.",
        "input": text,
        "max_output_tokens": 600,
    }
    request = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        parts = [item["text"] for output in result.get("output", []) for item in output.get("content", [])
                 if item.get("type") == "output_text"]
        if not parts:
            raise UserError("AI 답변이 비어 있습니다. 잠시 후 다시 시도해 주세요.")
        return "\n".join(parts)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise UserError("AI API 인증·접근 권한을 확인해 주세요.") from None
        if error.code == 429:
            raise UserError("AI API 사용량 또는 요청 한도에 도달했습니다.") from None
        raise UserError(f"AI 서비스 요청에 실패했습니다 (HTTP {error.code}).") from None
    except (OSError, ValueError):
        raise UserError("AI 서비스에 연결할 수 없습니다. 네트워크 설정을 확인해 주세요.") from None
