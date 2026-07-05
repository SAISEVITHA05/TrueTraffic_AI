def get_ai_explanation(origin, destination,
                        routes, hour, weather):
    fastest = routes["fastest"]
    safest = routes["safest"]
    recommended = routes["recommended"]

    time_saved = safest['time'] - recommended['time']
    risk_diff = fastest['risk_score'] - recommended['risk_score']

    # Smart response based on time and risk
    if hour >= 20 or hour <= 5:
        time_context = "night time — extra caution needed"
    elif 7 <= hour <= 10:
        time_context = "morning peak hours"
    elif 17 <= hour <= 20:
        time_context = "evening peak hours"
    else:
        time_context = "normal traffic hours"

    if weather == "Rainy":
        weather_tip = "Roads are wet — reduce speed by 20kmph."
    elif weather == "Foggy":
        weather_tip = "Use fog lights and maintain distance."
    else:
        weather_tip = "Follow speed limits and stay alert."

    response = (
        f"For your journey from {origin} to {destination} "
        f"during {time_context}, we recommend the "
        f"⭐ Recommended Route — it saves {time_saved} mins "
        f"compared to the safest route while being "
        f"{risk_diff:.0f}% safer than the fastest route. "
        f"Safety tip: {weather_tip}"
    )
    return response


def get_road_chat_response(user_message):
    return (
        "Please use the route planner above to check "
        "road safety for your specific journey. "
        "Always follow speed limits and drive carefully!"
    )