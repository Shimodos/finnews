import httpx

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4096


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Режет текст на части не длиннее limit, по возможности по границам абзацев."""
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def send(token: str, chat_id: str, text: str) -> None:
    with httpx.Client(timeout=20) as client:
        for part in split_message(text):
            resp = client.post(
                API.format(token=token, method="sendMessage"),
                json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True},
            )
            resp.raise_for_status()


def find_chat_ids(token: str) -> list[tuple[str, str]]:
    """Возвращает [(chat_id, имя)] из последних сообщений, отправленных боту."""
    resp = httpx.get(API.format(token=token, method="getUpdates"), timeout=20)
    resp.raise_for_status()
    seen: dict[str, str] = {}
    for update in resp.json().get("result", []):
        chat = (update.get("message") or {}).get("chat")
        if chat:
            name = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
            seen[str(chat["id"])] = name
    return list(seen.items())
