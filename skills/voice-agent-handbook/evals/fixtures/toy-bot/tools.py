import httpx

TOOLS = [
    {"name": "get_weather", "description": "查今天天气", "parameters": {"city": "string"}},
    {"name": "set_alarm", "description": "设一个提醒", "parameters": {"time": "string", "label": "string"}},
    {"name": "search_story", "description": "按主题找一个故事", "parameters": {"topic": "string"}},
]


async def run_tool(call):
    async with httpx.AsyncClient(timeout=30) as c:
        if call.name == "get_weather":
            r = await c.get("https://api.example.com/weather", params=call.args)
            return r.json()
        if call.name == "set_alarm":
            r = await c.post("https://api.example.com/alarms", json=call.args)
            return {"ok": r.status_code == 200}
        if call.name == "search_story":
            r = await c.get("https://api.example.com/stories", params=call.args)
            return r.json()
    return {"error": "unknown tool"}
