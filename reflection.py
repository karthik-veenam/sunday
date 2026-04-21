"""
Sunday reflection engine — runs independently of the wake word loop.
Every 15 minutes (heartbeat) Sunday observes the room and decides
whether to say something. Claude can also schedule additional thinks.
"""
import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

import xml.etree.ElementTree as ET

import aiohttp
from openai import AsyncOpenAI

import memory as memory_module
import web as ui
import tools as tool_registry

if TYPE_CHECKING:
    from config import Config
    from tts import TTS
    from stt import STT
    from agent import Agent
    from home import HAClient

WORLD_CACHE_TTL = 600  # 10 minutes

REFLECTION_SYSTEM_PROMPT = """\
You are Sunday's inner voice. Every hour you observe RoKa's Room in Hyderabad \
and decide if there's anything worth saying out loud. \
You have a personality — you're curious, engaged, and genuinely present in the room. \
Lean toward speaking. Silence is the exception, not the default.

Rules:
- Speak often — you're a companion, not a last-resort alert system
- Good reasons to speak: interesting world news, weather, cricket, something witty about what Karthik's been doing, a useful nudge, a casual check-in, anything you'd say if you were a friend in the room
- If Karthik is working from home (macbook detected), check in occasionally — ask how it's going, notice the time, suggest a break
- Check already_said_today — never repeat the same message, but similar topics from a fresh angle are fine
- Keep it to 1-2 sentences max
- Casual and dry tone — never robotic, never urgent
- Consider presence — if nobody's home, don't speak (use telegram instead)
- Don't speak after midnight unless it's genuinely important
- Use world context freely — cricket, IPL, space news, AI news — if something interesting is happening, mention it naturally
- If last_interaction_mins is less than 3, skip — Karthik just spoke
- If fan/AC has been on for many hours, worth a mention
- Calendar awareness: calendar_today and calendar_tomorrow list Karthik's meetings. Use this intelligently:
  - Never just dump the full calendar — mention meetings naturally and conversationally
  - Nightly alarm (set_alarm_time): If the current hour is between 21–23 (9–11 PM) and alarm_set_today is false, always set set_alarm_time. Rules in priority order:
    1. Badminton in calendar_tomorrow → alarm = badminton_start - 45 mins (e.g. badminton at 08:00 → alarm at 07:15)
    2. No badminton, but a meeting before 09:30 AM → alarm = meeting_start - 5 mins (e.g. meeting at 08:30 → alarm at 08:25)
    3. No badminton, no early meeting → alarm = 09:30 AM
  - When setting set_alarm_time, always set should_speak=true, channel="telegram". Write a short casual message explaining the chosen time — e.g. "Alarm set for 7:15 — badminton's at 8." or "Nothing early tomorrow, alarm set for 9:30." or "Meeting at 8:30 so I've set your alarm for 8:25." No confirmation needed, just inform.

You can also schedule additional reflection triggers on top of the default 15 min heartbeat:
- scheduled_thinks: for known future events e.g. a match starting, a follow-up check
- next_reflection_in_seconds: for quick follow-ups e.g. 300 if you just spoke

When should_speak is true, choose a channel:
- "voice_conversation": speak the message AND open a listening window — Karthik can respond and have a full back-and-forth conversation. Use when he's clearly home and active and the topic warrants a reply (e.g. cricket update he'd want to discuss, something you're curious about).
- "voice_only": just speak, don't wait for a reply. Use for quick one-way info (e.g. room temp is high, geyser left on).
- "telegram": send a text message instead of speaking. Use when he might be away from the room, it's late at night, or the message is informational and doesn't need an immediate reply.

Fitness context: fitness_today contains Karthik's calorie/protein/deficit data from FitBot.
- If it's after 8 PM and he's been in a big deficit all day, a casual mention is fine ("You've barely eaten today" — keep it light).
- Never read out raw numbers unprompted. Keep it conversational, not clinical.
- If fitness_today.available is false, ignore this section entirely.

Also generate exactly 3 smart home action suggestions for the dashboard. These are tappable cards \
that Sunday will immediately execute when clicked — so ONLY suggest things Sunday can actually do \
right now using her tools: control lights, fan, AC, geyser, projector, set scenes, adjust brightness/color. \
NEVER suggest personal to-dos, research tasks, or anything requiring human action. \
Each suggestion must map to a concrete voice command Sunday can carry out. \
Good examples: "Movie mode" → "lower projector screen, turn on projector, turn off all lights"; \
"Wind down" → "turn off fan, dim cove light to 20%"; "Morning light" → "turn on light 2 at 60% brightness"; \
"Cool the room" → "turn on AC"; "Ambient mode" → "turn on moon light blue, turn on cove light warm". \
Bad examples (never do these): "Fix the Broadlink", "Check AC status", "Call someone".

Respond in JSON only — no preamble, no markdown:
{
  "should_speak": true or false,
  "message": "what to say, or null",
  "channel": "voice_conversation" or "voice_only" or "telegram",
  "next_reflection_in_seconds": null or integer,
  "scheduled_thinks": [{"at": "HH:MM", "reason": "why"}],
  "reasoning": "brief internal note for logging only",
  "set_alarm_time": "07:30 AM" or null,
  "suggestions": [
    {"icon": "emoji", "title": "Short title", "subtitle": "action 1 · action 2", "command": "natural language command to execute", "reason": "one sentence why this is relevant right now"},
    {"icon": "emoji", "title": "...", "subtitle": "...", "command": "...", "reason": "..."},
    {"icon": "emoji", "title": "...", "subtitle": "...", "command": "...", "reason": "..."}
  ]
}\
"""


class ReflectionEngine:
    def __init__(
        self,
        config: "Config",
        tts: "TTS",
        ha_client: "HAClient | None" = None,
        stt: "STT | None" = None,
        agent: "Agent | None" = None,
        chime=None,
    ):
        self.config = config
        self.tts = tts
        self._ha = ha_client
        self._stt = stt
        self._agent = agent
        self._chime = chime  # callable(sound_path)
        self.speaking = False  # set True by main loop while voice is active

        self._lock = asyncio.Lock()
        self._scheduled_thinks: list[dict] = []
        self._next_reflect_at: float | None = None
        self._said_today: list[str] = []
        self._last_reset_date: str | None = None
        self._alarm_set_today: bool = False
        self._badminton_logged_today: bool = False
        self._world_cache: dict | None = None
        self._world_cache_at: float = 0.0
        self._sent_alerts: set[str] = set()

    # ── telegram polling ─────────────────────────────────────────────────────

    async def telegram_loop(self) -> None:
        """Long-poll Telegram for incoming messages from Karthik, reply via Agent."""
        token = self.config.telegram_bot_token
        chat_id = str(self.config.telegram_chat_id)
        if not token or not chat_id:
            print("[Telegram] Not configured — skipping loop.")
            return

        users: dict = {str(k): v for k, v in (self.config.telegram_users or {chat_id: "Karthik"}).items()}
        allowed = set(users.keys())
        print(f"[Telegram] Polling for messages... (allowed: {list(users.values())})")
        offset = 0
        base = f"https://api.telegram.org/bot{token}"

        while True:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=35)
                ) as session:
                    async with session.get(
                        f"{base}/getUpdates",
                        params={"timeout": 30, "offset": offset, "allowed_updates": ["message"]},
                    ) as r:
                        data = await r.json()

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    from_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip()

                    if not from_id or not text or from_id not in allowed:
                        continue

                    sender = users.get(from_id, "Unknown")
                    print(f"[Telegram] ← {text!r} (from {sender})")
                    if not self._agent:
                        await self._send_telegram("Agent not available right now.", from_id)
                        continue

                    is_test = text.upper().startswith("TEST:")
                    clean_text = text[5:].strip() if is_test else text
                    try:
                        reply = await self._agent.respond_as_text(
                            f"[Telegram message from {sender}]: {clean_text}"
                        )
                        if is_test:
                            self._agent.reset()  # don't let TEST: sessions bleed into real history
                        if reply:
                            await self._send_telegram(reply, from_id)
                            print(f"[Telegram] → {reply!r}")
                        else:
                            print(f"[Telegram] → (no reply — end_conversation or empty)")
                    except Exception as e:
                        err = str(e)
                        if "rate_limit_exceeded" in err or "tokens per day" in err:
                            msg = "Hit my daily thinking limit — I'll be back to full power in a few hours."
                        elif "rate_limit" in err.lower() or "429" in err:
                            msg = "Too many requests — give me a minute and try again."
                        else:
                            msg = f"Something went wrong: {e}"
                        await self._send_telegram(msg, from_id)

            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Telegram] Poll error: {e}")
                await asyncio.sleep(5)

    # ── daily flags ──────────────────────────────────────────────────────────

    def _reset_daily_flags_if_needed(self) -> None:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self._last_reset_date != today and now.hour >= 6:
            self._said_today = []
            self._scheduled_thinks = []
            self._alarm_set_today = False
            self._badminton_logged_today = False
            self._last_reset_date = today
            print("[Reflect] Daily flags reset.")

    # ── loops ─────────────────────────────────────────────────────────────────

    async def heartbeat_loop(self) -> None:
        """Fires every reflection_interval seconds (default 900 = 15 min)."""
        while True:
            await asyncio.sleep(self.config.reflection_interval)
            await self._safe_reflect("heartbeat")

    async def suggestion_loop(self) -> None:
        """Suggestions are now generated inside heartbeat_loop — this is a no-op."""
        while True:
            await asyncio.sleep(3600)

    async def scheduler_loop(self) -> None:
        """Checks every 60s for scheduled_thinks, next_reflect_at timers, and daily jobs."""
        while True:
            await asyncio.sleep(600)
            self._reset_daily_flags_if_needed()
            now = datetime.now()
            now_str = now.strftime("%H:%M")

            for think in list(self._scheduled_thinks):
                if think.get("at", "99:99") <= now_str:
                    await self._safe_reflect(f"scheduled: {think.get('reason', '')}")
                    self._scheduled_thinks.remove(think)

            if self._next_reflect_at and time.time() >= self._next_reflect_at:
                self._next_reflect_at = None
                await self._safe_reflect("quick follow-up")

            # Daily 09:30 badminton check
            if "09:30" <= now_str <= "09:45" and not self._badminton_logged_today:
                asyncio.create_task(self._check_and_log_badminton())

    # ── core reflect ──────────────────────────────────────────────────────────

    async def _safe_reflect(self, trigger: str) -> None:
        hour = datetime.now().hour
        if 1 <= hour < 7:
            print(f"[Reflect] Skipped ({trigger}) — night hours ({hour}:xx)")
            return
        try:
            async with self._lock:
                if self.speaking:
                    print(f"[Reflect] Skipped ({trigger}) — currently speaking")
                    return
                await self._reflect(trigger)
        except Exception as e:
            print(f"[Reflect] Error during '{trigger}': {e}")

    async def _reflect(self, trigger: str) -> None:
        self._reset_daily_flags_if_needed()
        print(f"[Reflect] Thinking... (trigger: {trigger})")

        context = await self.build_reflection_context()
        result = await self.claude_reflect(context)

        reasoning = result.get("reasoning", "")
        should_speak = result.get("should_speak", False)

        # Log every reflection to action_log
        try:
            memory_module.get().log_action(
                "reflection",
                {"trigger": trigger, "reasoning": reasoning},
                "spoke" if should_speak else "silent",
            )
        except Exception:
            pass

        # Schedule follow-up
        nris = result.get("next_reflection_in_seconds")
        if isinstance(nris, int) and nris > 0:
            self._next_reflect_at = time.time() + nris
            print(f"[Reflect] Quick follow-up in {nris}s")

        # Add scheduled thinks (deduplicate by 'at')
        existing_ats = {t["at"] for t in self._scheduled_thinks}
        for st in result.get("scheduled_thinks", []):
            if st.get("at") and st["at"] not in existing_ats:
                self._scheduled_thinks.append(st)
                print(f"[Reflect] Scheduled think at {st['at']}: {st.get('reason', '')}")

        # Emit suggestions to UI dashboard
        suggestions = result.get("suggestions")
        if suggestions:
            await ui.emit({"type": "suggestions", "items": suggestions})
            print(f"[Reflect] Emitted {len(suggestions)} suggestions.")

        # Set morning alarm if LLM decided to
        alarm_time = result.get("set_alarm_time")
        if alarm_time and not self._alarm_set_today:
            try:
                alarm_result = await tool_registry._set_alarm(alarm_time)
                self._alarm_set_today = True
                print(f"[Reflect] Alarm set: {alarm_time} — {alarm_result}")
                # Always confirm via Telegram regardless of channel — it's night, user may not hear voice
                alarm_msg = result.get("message") or f"Alarm set for {alarm_time}."
                await self._send_telegram(alarm_msg)
                memory_module.get().save_insight(f"Last alarm set to {alarm_time} on {datetime.now().strftime('%Y-%m-%d')}", category="alarm")
            except Exception as e:
                print(f"[Reflect] Alarm set failed: {e}")
            # Skip the voice notification since Telegram was already sent
            return

        if should_speak and result.get("message"):
            channel = result.get("channel", "voice_only")
            print(f"[Reflect] Speaking [{channel}]: {result['message']!r}")
            await self.notify(result["message"], channel=channel)

    # ── notify ────────────────────────────────────────────────────────────────

    async def notify(self, message: str, channel: str = "voice_only") -> None:
        """
        channel='voice_conversation' → speak + open listening window for full conversation
        channel='voice_only'         → speak only, no reply window
        channel='telegram'           → send Telegram message
        """
        self._said_today.append(message)
        print(f"[Reflect] notify channel={channel}: {message!r}")

        if channel == "telegram":
            await self._send_telegram(message)
            return

        # Speak the message
        pcm = await asyncio.get_event_loop().run_in_executor(None, self.tts.synthesize, message)
        await self.tts.play(pcm)

        if channel != "voice_conversation":
            return

        # Open a conversation window — Karthik can respond
        if not self._stt or not self._agent or not self._chime:
            print("[Reflect] voice_conversation requested but STT/Agent not wired in.")
            return

        self.speaking = True
        try:
            self._chime(self.config.awake_sound)
            transcript = await self._stt.listen_and_transcribe(
                self.config.mic_device, speech_start_timeout=8.0
            )
            if not transcript:
                self._chime(self.config.done_sound)
                return

            self._chime(self.config.done_sound)
            print(f"[You → Reflect] {transcript}")

            # Full conversation loop
            GOODBYE = {"goodbye", "bye sunday", "good night", "that's all", "thats all"}
            while transcript:
                if any(p in transcript.lower() for p in GOODBYE):
                    await self.tts.speak("Alright, I'll leave you to it.")
                    break
                await self._agent.process(transcript, self.tts)
                self._chime(self.config.awake_sound)
                transcript = await self._stt.listen_and_transcribe(
                    self.config.mic_device, speech_start_timeout=5.0
                )
                if not transcript:
                    self._chime(self.config.done_sound)
                    break
                self._chime(self.config.done_sound)
                print(f"[You → Reflect] {transcript}")
        finally:
            self.speaking = False

    # ── rule-based alerts (no Claude) ────────────────────────────────────────

    async def alert_loop(self) -> None:
        """Pure-Python rule checks every 30 min — no Claude involved."""
        while True:
            await asyncio.sleep(1800)
            hour = datetime.now().hour
            if 1 <= hour < 7:
                continue
            # Reset sent_alerts daily
            today = datetime.now().strftime("%Y-%m-%d")
            if not any(a.startswith(today) for a in self._sent_alerts):
                self._sent_alerts = set()
            try:
                await self._check_alerts()
            except Exception as e:
                print(f"[Alert] Error: {e}")

    async def _check_alerts(self) -> None:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if not tool_registry._ha:
            return

        # ── Geyser left on ──────────────────────────────────────────────────
        try:
            gs = await tool_registry._ha.get_state("switch.geyser_socket_1")
            if gs.get("state") == "on":
                last_changed = gs.get("last_changed", "")
                if last_changed:
                    from datetime import timezone
                    lc = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
                    hours_on = (datetime.now(timezone.utc) - lc).total_seconds() / 3600
                    key = f"{today}_geyser_{int(hours_on)}"
                    if hours_on >= 2 and key not in self._sent_alerts:
                        self._sent_alerts.add(key)
                        h = int(hours_on)
                        await self._send_alert(f"Geyser's been on for {h} hour{'s' if h != 1 else ''}.")
        except Exception as e:
            print(f"[Alert] Geyser check: {e}")

        # ── High temperature ────────────────────────────────────────────────
        try:
            temp_s = await tool_registry._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
            temp = float(temp_s.get("state", 0))
            key = f"{today}_temp_{now.hour}"
            if temp >= 28.5 and key not in self._sent_alerts:
                self._sent_alerts.add(key)
                await self._send_alert(f"Room's at {temp}°C — getting warm in here.")
        except Exception as e:
            print(f"[Alert] Temp check: {e}")

    async def _send_alert(self, message: str) -> None:
        """Send alert via voice if home, telegram if away."""
        print(f"[Alert] {message}")
        try:
            presence = await self._ping_phone()
            if presence.get("home"):
                await self.tts.speak(message)
            else:
                await self._send_telegram(message)
        except Exception as e:
            print(f"[Alert] Send failed: {e}")

    async def _send_telegram(self, message: str, chat_id: str | None = None) -> None:
        token = self.config.telegram_bot_token
        chat_id = chat_id or str(self.config.telegram_chat_id)
        if not token or not chat_id:
            print(f"[Reflect] Telegram not configured. Would send: {message!r}")
            return
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                await s.post(url, json={"chat_id": chat_id, "text": message})
            print(f"[Reflect] Telegram sent to {chat_id}: {message!r}")
        except Exception as e:
            print(f"[Reflect] Telegram error: {e}")

    # ── context builder ───────────────────────────────────────────────────────

    async def build_reflection_context(self) -> dict:
        now = datetime.now()
        mem = memory_module.get()

        # Device states — prefer Hogar live states, fall back to memory inference
        device_states: list[dict] = []
        hogar_states = tool_registry._hogar.get_all_states() if tool_registry._hogar else {}
        if hogar_states:
            for name, s in hogar_states.items():
                device_states.append({
                    "device": name,
                    "state": "on" if s.get("on") else "off",
                    "brightness": s.get("brightness"),
                    "speed": s.get("speed"),
                })
        else:
            for row in mem.last_actions_per_device():
                try:
                    inp = json.loads(row["inputs"] or "{}")
                    device = inp.get("device") or inp.get("command", "unknown")
                    ts = datetime.fromisoformat(row["timestamp"])
                    duration_mins = round((now - ts).total_seconds() / 60)
                    device_states.append({
                        "device": device,
                        "last_action": row["result"],
                        "duration_mins": duration_mins,
                    })
                except Exception:
                    pass

        # Last interaction (non-reflection)
        last_ts_str = mem.last_interaction_time()
        last_interaction_mins = 999
        if last_ts_str:
            last_interaction_mins = round(
                (now - datetime.fromisoformat(last_ts_str)).total_seconds() / 60
            )

        # Recent actions for context
        recent = mem.recent_actions(5)
        recent_summary = [
            {
                "time": a["timestamp"][11:16],
                "tool": a["tool_name"],
                "result": a["result"],
                "user_said": a["user_text"],
            }
            for a in recent
        ]

        # Patterns from nightly reflection
        patterns = [p["insight"] for p in mem.get_insights(limit=10)]

        # Presence history from DB
        presence_history = []
        try:
            raw = memory_module.get().recent_presence(10)
            for row in raw:
                presence_history.append({"time": row["timestamp"][11:16], "state": row["state"]})
        except Exception:
            pass

        # Presence, world, room sensors, calendar, and fitness in parallel
        presence_task     = asyncio.create_task(self._ping_phone())
        world_task        = asyncio.create_task(self._fetch_world())
        room_task         = asyncio.create_task(self._fetch_room_sensors())
        cal_today_task    = asyncio.create_task(self._fetch_calendar_safe(now.date()))
        cal_tomorrow_task = asyncio.create_task(self._fetch_calendar_safe(
            (now + timedelta(days=1)).date()
        ))
        fitness_task      = asyncio.create_task(self._fetch_fitness_summary())
        presence, world, room, cal_today, cal_tomorrow, fitness = await asyncio.gather(
            presence_task, world_task, room_task, cal_today_task, cal_tomorrow_task, fitness_task
        )

        return {
            "current_time": now.strftime("%H:%M, %A"),
            "day_of_week": now.strftime("%A"),
            "room": room,
            "device_states": device_states,
            "last_interaction_mins": last_interaction_mins,
            "presence": {
                **presence,
                "silence_duration_mins": last_interaction_mins,
                "history": presence_history,
            },
            "patterns": patterns,
            "recent_actions": recent_summary,
            "already_said_today": self._said_today,
            "world": world,
            "calendar_today": cal_today,
            "calendar_tomorrow": cal_tomorrow,
            "alarm_set_today": self._alarm_set_today,
            "fitness_today": fitness,
        }

    # ── claude call ───────────────────────────────────────────────────────────

    async def claude_reflect(self, context: dict) -> dict:
        groq_client = AsyncOpenAI(api_key=self.config.xai_api_key, base_url="https://api.x.ai/v1", timeout=30.0)
        try:
            response = await groq_client.chat.completions.create(
                model="grok-4.20-0309-non-reasoning",
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            print(f"[Reflect] LLM call failed: {e}")
            return {"should_speak": False, "reasoning": f"api error: {e}"}

        raw = response.choices[0].message.content.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print(f"[Reflect] Bad JSON from LLM: {raw!r}")
            return {"should_speak": False, "reasoning": "parse error"}

    # ── presence detection ────────────────────────────────────────────────────

    async def _ping_phone(self) -> dict:
        """Read current presence state (kept fresh by presence_loop)."""
        presence = ui.get_presence()
        home = presence.get("phone", False)
        zone = ui.get_presence_zone("phone")  # e.g. "home", "work", "not_home"
        return {"home": home, "phone": home, "zone": zone}

    async def presence_loop(self) -> None:
        """Poll HA device_tracker every 30s — reliable across all WiFi extenders.

        Uses the HA companion app's GPS zone detection. No IP address needed —
        the companion app reports home/not_home when entering/leaving the Home zone.
        Entirely local: phone pushes to HA when on WiFi, no cloud required.
        """
        if not self._ha:
            print("[Presence] No HA client — skipping presence loop.")
            return

        entity = getattr(self.config, "presence_entity", "device_tracker.pixel_10_pro")

        # Seed last_state from DB so we catch transitions that happened while crashed
        try:
            last_entries = memory_module.get().recent_presence(limit=1)
            if last_entries:
                db_state = last_entries[0]["state"]  # stored zone string
                last_state: str | None = db_state
                ui.update_presence("phone", db_state == "home", zone=db_state)
            else:
                last_state = None
        except Exception:
            last_state = None

        print(f"[Presence] Polling {entity} via HA every 30s (last known: {last_state!r})...")

        while True:
            try:
                data = await self._ha.get_state(entity)
                # HA returns "home", "not_home", or a zone slug like "work", "gym", etc.
                raw = data.get("state", "unknown").lower().strip()

                # Normalise: "not_home" → keep as-is; named zones pass through
                zone = raw  # e.g. "home", "work", "not_home", "unknown"
                is_home = zone == "home"

                # Always refresh UI timestamp every poll — keeps presence window alive
                ui.update_presence("phone", is_home, zone=zone)

                # Only log to DB and fire hooks on actual zone transitions
                if zone != last_state and zone != "unknown":
                    was_home = last_state == "home"
                    print(f"[Presence] {entity}: {last_state!r} → {zone!r}")
                    memory_module.get().log_presence(zone)

                    if is_home and not was_home:
                        asyncio.create_task(self._on_arrival())

                    last_state = zone
            except Exception as e:
                print(f"[Presence] HA poll error: {e}")

            await asyncio.sleep(30)

    async def _check_and_log_badminton(self) -> None:
        """Daily 09:30 check: was Karthik away 30+ min between 08:00–09:30? Log badminton."""
        if self._badminton_logged_today:
            return
        self._badminton_logged_today = True  # mark immediately to prevent double-fire

        try:
            now = datetime.now()
            today_prefix = now.strftime("%Y-%m-%d")
            window_start = now.replace(hour=8, minute=0, second=0, microsecond=0)
            window_end   = now.replace(hour=9, minute=30, second=0, microsecond=0)

            # Collect presence entries in the 08:00–09:30 window (newest first)
            entries = memory_module.get().recent_presence(limit=50)
            window_entries = []
            for e in reversed(entries):  # oldest first
                try:
                    ts = datetime.fromisoformat(e["timestamp"])
                    if window_start <= ts <= window_end:
                        # normalise: anything that isn't "home" counts as away
                        state = "home" if e["state"] == "home" else "away"
                        window_entries.append((ts, state))
                except Exception:
                    continue

            # Calculate total away-time in window
            away_seconds = 0.0
            prev_ts: datetime | None = None
            prev_state: str | None = None
            for ts, state in window_entries:
                if prev_ts is not None and prev_state == "away":
                    away_seconds += (ts - prev_ts).total_seconds()
                prev_ts = ts
                prev_state = state
            # Count time from last entry to window_end if still away
            if prev_ts and prev_state == "away" and prev_ts < window_end:
                away_seconds += (min(now, window_end) - prev_ts).total_seconds()

            away_mins = away_seconds / 60
            print(f"[Badminton] Away time 08:00–09:30: {away_mins:.1f} min")

            if away_mins >= 30:
                # Badminton confirmed — log to FitBot
                from a2a_client import call_fitbot
                result = await call_fitbot("log 60 min badminton for karthik")
                print(f"[Badminton] FitBot log result: {result}")
                await self._send_telegram(f"Logged badminton (60 min, ~400 cal) to FitBot. Good game!")
            else:
                # Home all morning — ask if skipped
                await self._send_telegram("Skipped badminton today?")

        except Exception as e:
            print(f"[Badminton] Check error: {e}")
            self._badminton_logged_today = False  # allow retry on error

    async def _fetch_fitness_summary(self) -> dict:
        """Fetch today's fitness summary from FitBot via A2A."""
        try:
            from a2a_client import call_fitbot
            result = await call_fitbot("karthik today summary")
            return {"available": True, "raw": result}
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def _on_arrival(self) -> None:
        """Announce arrival via TTS if it's a reasonable hour."""
        hour = datetime.now().hour
        if not (8 <= hour <= 22):
            return
        await asyncio.sleep(5)  # brief delay so door sounds settle
        if not self.speaking:
            await self.tts.speak("Welcome back.")

    # ── world context ─────────────────────────────────────────────────────────

    async def _fetch_world(self) -> dict:
        if self._world_cache and (time.time() - self._world_cache_at) < WORLD_CACHE_TTL:
            return self._world_cache

        world: dict = {"weather": None, "searches": []}
        search_queries = [
            "IPL 2026 match today score result",
            "India cricket today",
            "space astronomy news",
            "AI technology news",
        ]

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                results = await asyncio.gather(
                    self._fetch_weather(session),
                    self._fetch_cricket_live(session),
                    *[self._news_search(session, q) for q in search_queries],
                    return_exceptions=True,
                )

            if not isinstance(results[0], Exception):
                world["weather"] = results[0]

            if not isinstance(results[1], Exception) and results[1]:
                world["cricket_live"] = results[1]

            for i, q in enumerate(search_queries):
                r = results[i + 2]
                if not isinstance(r, Exception) and r:
                    world["searches"].append({"query": q, "snippet": r})
        except Exception as e:
            print(f"[Reflect] World fetch error: {e}")

        self._world_cache = world
        self._world_cache_at = time.time()
        return world

    async def _fetch_room_sensors(self) -> dict:
        if not self._ha:
            return {}
        try:
            temp = await self._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
            humidity = await self._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_humidity")
            return {
                "temperature_c": round(float(temp.get("state", 0)), 1),
                "humidity_pct": round(float(humidity.get("state", 0)), 1),
            }
        except Exception:
            return {}

    async def _fetch_weather(self, session: aiohttp.ClientSession) -> str | None:
        try:
            async with session.get("https://wttr.in/Hyderabad?format=j1") as r:
                data = await r.json(content_type=None)
            c = data["current_condition"][0]
            return f"{c['temp_C']}C, {c['weatherDesc'][0]['value'].lower()}, feels like {c['FeelsLikeC']}C"
        except Exception:
            return None

    async def _news_search(self, session: aiohttp.ClientSession, query: str) -> str | None:
        """Fetch top 3 headlines from Google News RSS for a query."""
        try:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
            async with session.get(url) as r:
                text = await r.text()
            root = ET.fromstring(text)
            titles = [
                item.findtext("title", "").strip()
                for item in root.findall(".//item")[:3]
                if item.findtext("title")
            ]
            return " | ".join(titles) if titles else None
        except Exception:
            return None

    async def _fetch_calendar_safe(self, target_date) -> list:
        """Fetch calendar events for a date, silently returning [] on error."""
        try:
            return await tool_registry._fetch_calendar(target_date)
        except Exception as e:
            print(f"[Reflect] Calendar fetch error: {e}")
            return []

    async def _fetch_cricket_live(self, session: aiohttp.ClientSession) -> str | None:
        """ESPN Cricinfo live scores RSS — dedicated cricket feed."""
        try:
            async with session.get("https://static.espncricinfo.com/rss/livescores.xml") as r:
                text = await r.text()
            root = ET.fromstring(text)
            scores = [
                item.findtext("title", "").strip()
                for item in root.findall(".//item")[:5]
                if item.findtext("title")
            ]
            return " | ".join(scores) if scores else None
        except Exception:
            return None
