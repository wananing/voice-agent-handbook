"""儿童故事玩具的语音服务端（示例代码，用于评审练习）。

设备是按键说话的 ESP32，上行 Opus 16k，下行 Opus 24k。
服务端：WebSocket 收音频 -> 云端流式 ASR -> LLM（带工具）-> 云端 TTS -> 下发。
"""
import asyncio
import json
import time

import websockets

from asr_client import StreamingASR
from llm_client import LLM
from tts_client import TTS
from tools import TOOLS, run_tool

SYSTEM_PROMPT = open("prompt.txt").read()


class Session:
    def __init__(self, ws):
        self.ws = ws
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.asr = StreamingASR(lang="zh")
        self.llm = LLM(model="gpt-4.1-mini", tools=TOOLS)
        self.tts = TTS(voice="child-friendly")
        self.speaking_task = None
        self.audio_buf = []

    async def handle(self):
        async for msg in self.ws:
            if isinstance(msg, bytes):
                self.audio_buf.append(msg)
                self.asr.feed(msg)
                continue
            data = json.loads(msg)
            t = data.get("type")
            if t == "listen_start":
                self.audio_buf = []
                self.asr.reset()
            elif t == "listen_stop":
                asyncio.create_task(self.respond())
            elif t == "abort":
                if self.speaking_task:
                    self.speaking_task.cancel()

    async def respond(self):
        # 等 ASR 出最终结果
        text = await self.asr.final(timeout=5.0)
        if not text:
            return
        await self.ws.send(json.dumps({"type": "stt", "text": text}))
        self.history.append({"role": "user", "content": text})

        self.speaking_task = asyncio.current_task()
        reply = ""
        async for chunk in self.llm.stream(self.history):
            if chunk.tool_call:
                result = await run_tool(chunk.tool_call)
                self.history.append({"role": "tool", "tool_call_id": chunk.tool_call.id, "content": json.dumps(result)})
                async for chunk2 in self.llm.stream(self.history):
                    reply += chunk2.text or ""
                break
            reply += chunk.text or ""

        # 整段文本一次合成，合成完再下发
        audio = await self.tts.synthesize(reply)
        await self.ws.send(json.dumps({"type": "tts_start"}))
        for frame in audio.frames(ms=60):
            await self.ws.send(frame)
        await self.ws.send(json.dumps({"type": "tts_stop"}))
        self.history.append({"role": "assistant", "content": reply})
        if len(self.history) > 40:
            self.history = self.history[:1] + self.history[-30:]


async def main(ws):
    s = Session(ws)
    try:
        await s.handle()
    except websockets.ConnectionClosed:
        pass


if __name__ == "__main__":
    start = websockets.serve(main, "0.0.0.0", 8765)
    asyncio.get_event_loop().run_until_complete(start)
    asyncio.get_event_loop().run_forever()
