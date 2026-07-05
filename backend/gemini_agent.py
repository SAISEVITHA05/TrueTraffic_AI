import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are TrueTraffic AI, a road safety 
assistant for Visakhapatnam, India. You know these roads:
- Beach Road: High speed accidents, risky 7pm-11pm
- Hanumanthawaka Junction: Highest accident zone in Vizag
- Gajuwaka Junction: Heavy trucks, industrial traffic
- NAD Junction: Signal violations, peak hour chaos
- Steel Plant Road: Heavy vehicles, poor visibility
- MVP Colony Roads: Two-wheeler accidents
- Kommadi Road: Poor lighting, highway accidents
- NH16 Bypass: Safest highway route
- Dwaraka Nagar Road: Pedestrian heavy area
- Siripuram Junction: Moderate risk, city centre

Rules:
1. Keep response under 60 words
2. Be friendly and direct
3. Always end with one safety tip
4. Never mention road names outside Vizag"""


def get_ai_explanation(origin, destination,
                        routes, hour, weather):
    try:
        fastest = routes["fastest"]
        safest = routes["safest"]
        recommended = routes["recommended"]

        response = client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"""
Journey: {origin} to {destination}
Time: {hour}:00 | Weather: {weather}

Routes:
- Fastest: {fastest['time']} mins, Risk: {fastest['risk_score']}/100
- Safest: {safest['time']} mins, Risk: {safest['risk_score']}/100
- Recommended: {recommended['time']} mins, Risk: {recommended['risk_score']}/100

Give a 2 sentence recommendation and one safety tip.
"""}
            ],
            max_tokens=150,
            temperature=0.7
        )
        return response.choices[0].message.content

    except Exception as e:
        print(f"Groq error: {e}")
        return ("Take the recommended route for best "
                "balance of speed and safety. "
                "Safety tip: Always wear seatbelt.")


def get_road_chat_response(user_message):
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            max_tokens=100,
            temperature=0.7
        )
        return response.choices[0].message.content

    except Exception as e:
        print(f"Groq error: {e}")
        return ("Please check road conditions "
                "carefully and drive safe!")