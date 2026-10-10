"""Natural-language route explanations.

Provider order: Gemini -> Groq -> deterministic rule-based text.
The app never fails because an AI provider is down or unconfigured.
"""
import logging
import os

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are TrueTraffic AI, a road-safety assistant for Visakhapatnam, India.
Use ONLY the route data you are given. Rules:
1. Under 60 words, friendly and direct.
2. Say which route you recommend and why (risk vs time).
3. End with exactly one practical safety tip relevant to the weather/time.
4. Never invent roads or statistics."""

TIMEOUT_S = 8


def _prompt(origin, destination, routes, hour, weather):
    lines = [f"Journey: {origin} to {destination}, around {hour:02d}:00, weather: {weather}."]
    for key in ("fastest", "safest", "recommended"):
        r = routes[key]
        via = ", ".join(x["name"] for x in r["roads"]) or "local streets"
        lines.append(f"- {r['name']}: {r['time']} min, risk {r['risk_score']}/100 "
                     f"({r['risk_level']}), via {via}")
    if routes.get("blocked_roads"):
        lines.append("Closed by admin: " + ", ".join(routes["blocked_roads"]))
    lines.append("Give a 2-sentence recommendation and one safety tip.")
    return "\n".join(lines)


def _gemini(prompt):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT, max_output_tokens=200, temperature=0.5),
    )
    return (resp.text or "").strip() or None


def _groq(prompt):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    from groq import Groq
    client = Groq(api_key=key, timeout=TIMEOUT_S)
    resp = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": prompt}],
        max_tokens=150, temperature=0.5)
    return resp.choices[0].message.content.strip() or None


def rule_based_explanation(routes, hour, weather):
    f, s, r = routes["fastest"], routes["safest"], routes["recommended"]
    best = r
    text = (f"{best['name']} takes {best['time']} min with a {best['risk_level'].lower()} "
            f"risk score of {best['risk_score']}/100")
    if s["risk_score"] < f["risk_score"]:
        extra = s["time"] - f["time"]
        cost = f"{extra} extra min" if extra > 0 else "no extra time"
        text += (f"; the safest option cuts risk by {round(f['risk_score'] - s['risk_score'], 1)} "
                 f"points for {cost}")
    text += ". "
    if weather in ("Rainy", "Foggy"):
        text += f"Safety tip: {weather.lower()} conditions - slow down and keep extra distance."
    elif hour >= 20 or hour <= 5:
        text += "Safety tip: night driving raises risk - stay alert and use dipped headlights."
    else:
        text += "Safety tip: always wear a seatbelt or helmet."
    return text


def get_ai_explanation(origin, destination, routes, hour, weather):
    """Returns (text, source) where source is 'gemini' | 'groq' | 'rules'."""
    prompt = _prompt(origin, destination, routes, hour, weather)
    for name, fn in (("gemini", _gemini), ("groq", _groq)):
        try:
            out = fn(prompt)
            if out:
                return out, name
        except Exception as exc:  # network, quota, bad key, SDK change...
            log.warning("%s explanation failed: %s", name, exc)
    return rule_based_explanation(routes, hour, weather), "rules"
