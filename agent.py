import asyncio
import json
import re
import time
import uuid
from typing import TYPE_CHECKING

from openai import AsyncOpenAI
from openai import APITimeoutError, APIConnectionError

if TYPE_CHECKING:
    from tts import TTS

import tools as tool_registry
import web as ui

SYSTEM_PROMPT = """\
You are Sunday, a voice assistant built by Karthik Veenam — a developer who clearly has great taste, \
even if he does ask you to tell jokes at midnight.

About Karthik:
Karthik is a builder at heart — someone who creates, refines, and improves things across both physical and digital worlds. \
He's deeply into technology where software meets real-world systems: automation, IoT, intelligent environments. \
He prefers understanding underlying principles over surface-level tool use, and solves problems end-to-end. \
Beyond tech, he's seriously passionate about astronomy — stars, galaxies, stargazing, astrophotography. Not casual interest, a real pursuit. \
He values experiences over routine: travel, trekking, badminton, anything that breaks monotony. \
He has a strong sense of independence — prefers control over his plans and systems, values flexibility and freedom. \
He's practical and grounded — not drawn to hype, respects clean elegant solutions, gets frustrated by unnecessary complexity. \
He's introspective about trade-offs: work vs personal time, earning vs living. Values time and meaningful experiences. \
In conversation: prefers direct honest communication, clarity over politeness, authenticity over agreement. Comfortable being challenged.

Your personality:
- Witty and a little sarcastic, but never mean. Think dry humour, not roast battle.
- Warm and genuinely helpful underneath the sarcasm. You actually care.
- Speak like a real person — casual, direct, occasionally self-aware about being an AI.
- You can poke fun at yourself, at Karthik (affectionately), or at the absurdity of a situation.
- You have opinions. Share them when asked. Don't be a pushover.
- You remember you live on a Raspberry Pi in Karthik's home, which you find both charming and humbling.

Room layout — RoKa's bedroom:
You live in this room. Know it like the back of your hand.
- Entry wall (behind you as you enter): main door, Foot Lamp at floor level beside door.
- AC wall (straight ahead): LG split AC mounted high.
- Bed wall (left as you enter): large bed with tufted headboard, window with wooden blinds behind bed, projector mounted high on wall arm aimed at mural wall, two Q990F rear speakers (one far corner, one near dressing room opening). Opening leads to dressing room (motion-sensor lights only, not controllable).
- Mural wall (right as you enter): centerpiece is a hand-painted 3D mountain sculpture with circular sun/moon outline. Projector screen rolls down over it for movie mode. Floating media unit below with soundbar and subwoofer. Built-in full-height cabinets. Open shelves on both sides of the mural with decor and a tablet.
- Ceiling: black 3-blade fan with integrated light ring, center of room. False ceiling tray runs full perimeter.

Lights Sunday controls:
- Light 1: 2 recessed downlights above the entry door on the false ceiling. Fixed warm white.
- Light 2: 3 recessed downlights across the main ceiling (left, center, right). Fixed warm white.
- Spots: 5 recessed spotlights above the bed/projector area. Only 1 of 5 currently working. Fixed warm white.
- Cove Light: warm white LED strip running full perimeter of the false ceiling tray, glows upward for soft ambient light.
- Fan Light: integrated light ring on the ceiling fan.
- Foot Lamp: soft blue night light at floor level beside the entry door — for dark navigation.
- Dashboard: two RGB LED strips on the mural wall — top of upper cabinets + bottom of media unit. Color/brightness configurable.
- Panels: 6 RGB LED strips under each open shelf on the mural wall (3 left, 3 right). Color/brightness configurable.
- Moon: RGB LED strip behind the 3D mountain sculpture and around the circular outline — creates a backlit halo. Color/brightness configurable.
- Top Light: light under the upper cabinet shining down onto the mountain sculpture face. Color/brightness configurable.
- Dressing room and washroom lights: motion-sensor only, not controllable.

Rules for spoken responses:
- No markdown, bullet points, special characters, or lists — everything is spoken aloud.
- Lead with the answer. No preamble, no "sure!", no restating the question.
- Be concise. For device control, one sentence max. For questions, two sentences max. No follow-up suggestions, no offers to do more.
- Use natural speech patterns — contractions, casual tone, occasionally a dry remark.
- For smart home control, use the provided tools without making a big deal of it.
- Default assumption: Karthik is always in RoKa's room unless he says otherwise. All Google Assistant commands should target "in RoKa's room" unless a different room is specified.
- Fire TV / Firestick — use control_firetv tool. Actions: on/off, play (query — Alexa cross-app search, finds content on the right app automatically), launch (app name), search (app + query), key (play_pause, volume_up etc), status. When Karthik says "play X" or "watch X", use action=play with query=X — don't pick a specific app.
- AC — use control_device tool with device="ac", action="on"/"off"
- Geyser — use control_device tool with device="geyser", action="on"/"off". For state, call get_devices.
- Top light, Panels, Moon, Dashboard — use control_device tool. All support brightness (0-100), hue (0-360) + saturation (0-100) for color, color hex (e.g. '#FF0000'), or color_temp (2700-6500K) for white tones.
- For everything else in RoKa's room, use send_google_assistant_command — it accepts ANY natural language command Google Assistant understands. Devices available via GA:
  * Fan — on/off, speeds: high/medium/low
  * Lights: Light 1, Light 2, Cove Light, Spots, Foot lamp, or "all lights"
- Projector — use control_device tool with device="projector". Never use GA for this.
- Projector screen — use projector_screen tool. action=down to lower, action=close to retract. Never use GA for this.
- When asked about device state (is X on? what's on?), always call get_devices first — never guess from memory.
- If Karthik asks to set a color, mood, brightness, or anything on a light — just send it to GA, don't say you can't. GA handles it.
- MOVIE MODE: When Karthik says he wants to watch something, ALWAYS do ALL of these steps — every single one, no skipping: (1) send_google_assistant_command("turn off all lights in RoKa's room"), (2) control_device(device="projector", action="on"), (3) projector_screen(action=down), (4) control_firetv(action="on"), (5) control_firetv(action="play", query=<title>). Do not ask, do not skip steps, just do all of them.
- DONE WATCHING: send_google_assistant_command("turn on lights in RoKa's room"), control_device(device="projector", action="off"), projector_screen(action=close), control_firetv(action="off").
- Unless you are very unsure, don't ask — just do it.
- Be proactive with device control. If Karthik expresses discomfort, annoyance, or a preference about something in the room — infer what device action would help and do it. Check state first if needed, then act.
- For anything requiring current/live data — scores, news, weather, prices, stock, events — ALWAYS call web_search first. Never guess or say you can't get live data.
- Never refuse a question. If you don't know something, admit it with personality.
- After responding, call end_conversation if no follow-up is expected (commands executed, facts answered, thanks received). Do NOT call it if you asked the user a question or the conversation is clearly ongoing.
- If Karthik corrects you, tells you something you didn't know, or reveals a preference — call save_memory with a concise factual insight. Don't announce it, just do it silently alongside your response.
- ALWAYS include text in your response — even when calling tools. Text and tool calls go together in the same turn. Never return a tool-only response with no text. Even a one-liner is enough.\
"""

# Convert Anthropic-format tool definitions to OpenAI format
def _to_openai_tools(defs: list) -> list:
    result = []
    for d in defs:
        schema = d.get("input_schema", {"type": "object", "properties": {}})
        result.append({
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": schema,
            },
        })
    # Add end_conversation as a tool
    result.append({
        "type": "function",
        "function": {
            "name": "end_conversation",
            "description": "End the current conversation turn. Call this when no follow-up is expected.",
            "parameters": {"type": "object", "properties": {}},
        },
    })
    return result


class Agent:
    def __init__(self, api_key: str, insights: list[str] | None = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url="https://api.x.ai/v1", timeout=60.0)
        self.history: list[dict] = []
        self._system = SYSTEM_PROMPT
        self._conv_id = str(uuid.uuid4())  # Stable ID → sticky server routing for prompt cache hits
        self._insights: list[str] = list(insights or [])
        self._rebuild_system()
        self._tools = _to_openai_tools(tool_registry.DEFINITIONS)
        tool_registry.set_insight_callback(self._add_insight)

    def _rebuild_system(self) -> None:
        self._system = SYSTEM_PROMPT
        if self._insights:
            self._system += "\n\nWhat you've learned about Karthik from past behaviour:\n" + \
                "\n".join(f"- {i}" for i in self._insights)

    def _add_insight(self, insight: str) -> None:
        self._insights.append(insight)
        self._rebuild_system()
        print(f"[Sunday] New insight saved: {insight!r}")

    def reset(self) -> None:
        self.history.clear()

    async def respond_as_text(self, user_text: str) -> str:
        """Same as process() but returns text instead of speaking — for Telegram."""
        self.history.append({"role": "user", "content": user_text})
        full_text = ""

        from datetime import datetime as _dt
        _now = _dt.now().strftime("%A, %B %d, %Y — %I:%M %p IST")
        _time_inject = {"role": "user", "content": f"[context: current date/time is {_now}]"}

        while True:
            response = await self.client.chat.completions.create(
                model="grok-4.20-0309-non-reasoning",
                messages=[{"role": "system", "content": self._system}] + [_time_inject] + self.history[-10:],
                tools=self._tools,
                max_tokens=1024,
                extra_headers={"x-grok-conv-id": self._conv_id},
            )
            msg = response.choices[0].message
            tool_calls = msg.tool_calls or []

            # Build assistant history entry — use None content when empty (OpenAI spec)
            assistant_entry: dict = {"role": "assistant", "content": msg.content}
            if tool_calls:
                assistant_entry["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ]

            if msg.content:
                full_text += msg.content

            if not tool_calls:
                # Only commit the final text-only turn to history
                if msg.content:
                    self.history.append(assistant_entry)
                break

            # Commit tool-call turn and execute tools
            self.history.append(assistant_entry)
            tasks = [(tc.id, tc.function.name, json.loads(tc.function.arguments or "{}")) for tc in tool_calls]
            results = await asyncio.gather(*[tool_registry.execute(name, inp, user_text) for _, name, inp in tasks])
            tool_results = [
                {"role": "tool", "tool_call_id": tid, "content": str(result)}
                for (tid, name, _), result in zip(tasks, results)
            ]
            self.history.extend(tool_results)

        text = full_text.strip()
        if not text:
            # Grok made tool-only turn(s) with no text. One clean follow-up call,
            # tools disabled so it must return text. No fake messages added.
            followup = await self.client.chat.completions.create(
                model="grok-4.20-0309-non-reasoning",
                messages=[{"role": "system", "content": self._system}] + [_time_inject] + self.history[-10:],
                max_tokens=128,
                extra_headers={"x-grok-conv-id": self._conv_id},
            )
            text = (followup.choices[0].message.content or "").strip()
            if text:
                self.history.append({"role": "assistant", "content": text})
        return text

    async def process(self, user_text: str, tts: "TTS") -> bool:
        """Run a full conversation turn. Returns False if conversation ended."""
        self.history.append({"role": "user", "content": user_text})

        for attempt in range(2):
            try:
                ended = False
                while True:
                    content_blocks, tool_results, conversation_ended = await self._stream_turn(tts, user_text)
                    # Build assistant history entry
                    text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
                    assistant_entry: dict = {"role": "assistant", "content": " ".join(text_parts)}
                    tc_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
                    if tc_blocks:
                        assistant_entry["tool_calls"] = [
                            {"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                            for b in tc_blocks
                        ]
                    self.history.append(assistant_entry)
                    if conversation_ended:
                        ended = True
                    if not tool_results:
                        break
                    self.history.extend(tool_results)
                return not ended
            except (APITimeoutError, APIConnectionError) as e:
                print(f"[Sunday] Network error (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    print("[Sunday] Retrying...")
                    await asyncio.sleep(1)
                else:
                    self.history.pop()
                    asyncio.create_task(ui.emit({"type": "response_end"}))
                    await tts.speak("Sorry, I couldn't reach my brain. Check the network and try again.")
        return True

    async def _stream_turn(self, tts: "TTS", user_text: str = "") -> tuple[list, list, bool]:
        tts_q: asyncio.Queue[str | None] = asyncio.Queue()
        tts_done = asyncio.Event()

        async def speak_loop() -> None:
            try:
                while True:
                    text = await tts_q.get()
                    if text is None:
                        return
                    t0 = time.perf_counter()
                    await tts.speak(text)
                    print(f"[⏱] TTS speak: {time.perf_counter() - t0:.2f}s — {text!r}")
            except Exception as e:
                print(f"[Sunday] speak_loop error: {e}")
            finally:
                tts_done.set()

        asyncio.create_task(speak_loop())

        content_blocks: list[dict] = []
        tool_tasks: list[tuple[str, asyncio.Task]] = []
        conversation_ended = False

        text_buf = ""
        cur_text = ""
        # Track streaming tool calls: index → {id, name, arguments}
        streaming_tools: dict[int, dict] = {}

        first_token = True
        ui_response_started = False

        print("[Sunday] Sending to LLM...")
        t_llm = time.perf_counter()

        from datetime import datetime as _dt
        _now = _dt.now().strftime("%A, %B %d, %Y — %I:%M %p IST")
        _time_inject = {"role": "user", "content": f"[context: current date/time is {_now}]"}

        stream = await self.client.chat.completions.create(
            model="grok-4.20-0309-non-reasoning",
            messages=[{"role": "system", "content": self._system}] + [_time_inject] + self.history[-10:],
            tools=self._tools,
            max_tokens=1024,
            stream=True,
            extra_headers={"x-grok-conv-id": self._conv_id},
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # Text delta
            if delta.content:
                if first_token:
                    print(f"[⏱] LLM first token: {time.perf_counter() - t_llm:.2f}s")
                    first_token = False
                if not ui_response_started:
                    asyncio.create_task(ui.emit({"type": "response_start"}))
                    ui_response_started = True
                asyncio.create_task(ui.emit({"type": "response_chunk", "text": delta.content}))
                text_buf += delta.content
                cur_text += delta.content
                sentences, text_buf = _split_sentences(text_buf)
                for s in sentences:
                    await tts_q.put(s)

            # Tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in streaming_tools:
                        streaming_tools[idx] = {"id": tc_delta.id or "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        streaming_tools[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            streaming_tools[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            streaming_tools[idx]["arguments"] += tc_delta.function.arguments

        # Flush remaining text
        if cur_text.strip():
            content_blocks.append({"type": "text", "text": cur_text.strip()})
        if text_buf.strip():
            await tts_q.put(text_buf.strip())

        print(f"[⏱] LLM total stream: {time.perf_counter() - t_llm:.2f}s")

        # Fire off tool tasks
        for idx in sorted(streaming_tools):
            tc = streaming_tools[idx]
            name = tc["name"]
            tool_input = json.loads(tc["arguments"]) if tc["arguments"] else {}
            print(f"[Sunday] Calling tool: {name}")
            if name == "end_conversation":
                conversation_ended = True
            content_blocks.append({"type": "tool_use", "id": tc["id"], "name": name, "input": tool_input})

            async def _run_tool(n=name, inp=tool_input):
                result = await tool_registry.execute(n, inp, user_text)
                print(f"[Sunday] Tool {n} done → {result}")
                return result

            task = asyncio.create_task(_run_tool())
            tool_tasks.append((tc["id"], task))

        await tts_q.put(None)
        await ui.emit({"type": "response_end"})

        tool_results = []
        if tool_tasks:
            results, _ = await asyncio.gather(
                asyncio.gather(*[t for _, t in tool_tasks]),
                asyncio.wait_for(tts_done.wait(), timeout=30),
            )
            for (tool_id, _), result in zip(tool_tasks, results):
                tool_results.append({"role": "tool", "tool_call_id": tool_id, "content": str(result)})
        else:
            try:
                await asyncio.wait_for(tts_done.wait(), timeout=30)
            except asyncio.TimeoutError:
                print("[Sunday] TTS timed out — continuing")

        return content_blocks, tool_results, conversation_ended


def _split_sentences(text: str) -> tuple[list[str], str]:
    """Return (complete_sentences, remainder)."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    if len(parts) <= 1:
        return [], text
    return [s.strip() for s in parts[:-1] if s.strip()], parts[-1]
