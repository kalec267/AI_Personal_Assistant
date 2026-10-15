"""Turn displayed Markdown into short, speakable Korean text segments."""
import re


def spoken_text(text):
    text = re.sub(r"```[\s\S]*?```", "코드 내용은 화면에서 확인해 주세요.", str(text))
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "링크", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) > 2000:
        text = text[:1950].rsplit(" ", 1)[0] + "… 나머지 내용은 화면에서 확인해 주세요."
    return text


def speech_chunks(text, limit=240):
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?。！？])\s+|\n+", spoken_text(text)):
        if not sentence:
            continue
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = ""
        while len(sentence) > limit:
            split = sentence.rfind(" ", 0, limit + 1)
            split = split if split > limit // 2 else limit
            chunks.append(sentence[:split].strip())
            sentence = sentence[split:].strip()
        current = (current + " " + sentence).strip()
    if current:
        chunks.append(current)
    return chunks
