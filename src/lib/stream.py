import re
import sys

CANNED = re.compile(r"need more steps to process|sorry, i (?:can'?t|cannot)", re.I)


def field(message, name, default=None):
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = (block if isinstance(block, str) else str(field(block, "text", "") or "")
                 for block in content)
        return "\n".join(part for part in parts if part)
    return str(content or "")


def say(tag, arrow, text):
    line = f"{tag} {arrow} {text}".replace("\n", " ")
    print(line[:400], file=sys.stderr)


async def stream(agent, message, tag="·", limit=60):
    answer = ""
    async for update in agent.astream({"messages": [{"role": "user", "content": message}]},
                                      config={"recursion_limit": limit},
                                      stream_mode="updates"):
        for payload in update.values():
            for event in (payload or {}).get("messages", []):
                content = text_of(field(event, "content"))
                if field(event, "type") == "tool":
                    say(tag, "←", content)
                elif calls := field(event, "tool_calls"):
                    for call in calls:
                        say(tag, "→", f"{call['name']} {call['args']}")
                elif content:
                    answer = content
    if not answer.strip() or CANNED.search(answer):
        return ""
    return answer.strip()
