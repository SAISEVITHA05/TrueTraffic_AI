# 🚦 TrueTraffic AI

**AI-powered road safety and route intelligence platform for Visakhapatnam.**

TrueTraffic AI helps commuters choose routes based on both speed *and* safety — not speed alone. It analyzes historical accident data, computes real-time risk scores for road segments, and uses Gemini AI to explain *why* a route is risky in plain language, alongside a human-in-the-loop admin layer for special events and VIP movements.

Built for the **APAC GenAI Hackathon 2026**, under the Urban Mobility and Transportation track of the Decision Intelligence Platform problem statement.

---

## 🧠 What it does

- Recommends **three route options** — Fastest, Safest, and AI-Recommended — instead of one black-box answer
- Computes **live risk scores** per road segment based on historical accident data, time of day, weather, and day of week
- Uses **Gemini AI** to generate a plain-language explanation of why a route is safer or riskier
- Shows a **live interactive risk map** of Visakhapatnam, color-coded by road risk level
- Provides a **city dashboard** with accident trends, peak hours, dangerous roads, and severity breakdowns from 50,000+ historical records
- Includes an **admin panel** for human-in-the-loop overrides — administrators can flag roads for VIP movements, road work, or events, temporarily overriding AI recommendations (Responsible AI: humans handle exceptions, AI handles patterns)

---

## 🏗️ Architecture

```
Client (Route Planner / Dashboard / Admin Panel)
        ↓
Backend orchestration layer
        ↓
┌─────────────────────────────────────────────┐
│  BigQuery          → accident history query  │
│  Risk model         → live risk scoring       │
│  Gemini AI          → natural-language        │
│                        route explanations     │
└─────────────────────────────────────────────┘
        ↓
Rendered result: risk map + 3 route options + AI explanation
```

---

## 🛠️ Tech stack

| Layer | Technology |
|---|---|
| Risk scoring | Custom Python risk model (time-of-day, weather, day-of-week factors) |
| Historical data | Google BigQuery (50,000+ Vizag accident records), CSV fallback |
| AI reasoning | Gemini AI (`google-generativeai`) |
| App / UI | Streamlit |
| Maps | Folium + streamlit-folium |
| Data processing | pandas |

---

## 📁 Project structure

```
truetraffic_ai/
├── backend/
│   ├── streamlit_app.py      # Main app (UI + orchestration)
│   ├── risk_model.py         # Risk scoring logic
│   ├── route_engine.py       # Builds fastest/safest/recommended routes
│   ├── gemini_agent.py       # Gemini AI explanation layer
│   ├── bigquery_client.py    # BigQuery upload/query helpers
│   ├── benchmark.py          # pandas vs RAPIDS cuDF benchmark
│   └── generate_data.py      # Synthetic accident data generator
├── data/
│   ├── vizag_roads.json      # Road segments, coordinates, base risk
│   ├── vizag_accidents.csv   # 50,000+ historical accident records
│   └── special_events.json   # Active admin overrides/restrictions
├── requirements.txt
└── README.md
```

---

## ⚙️ Setup

1. **Clone the repo and install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set environment variables** — create a `.env` file in the project root:
   ```
   GEMINI_API_KEY=your_gemini_api_key
   GOOGLE_CLOUD_PROJECT=your_gcp_project_id   # optional, for BigQuery
   ```

3. **Run locally:**
   ```bash
   cd backend
   python -m streamlit run streamlit_app.py
   ```
   The app opens at `http://localhost:8501`.

---

## 🚀 Deployment

Deployed via [Streamlit Community Cloud](https://share.streamlit.io):

1. Push this repo to GitHub
2. On Streamlit Cloud: **New app** → select repo → set main file path to `backend/streamlit_app.py`
3. Add `GEMINI_API_KEY` (and `GOOGLE_CLOUD_PROJECT` if using BigQuery) under **Advanced settings → Secrets**
4. Deploy

**Live app:** _[add your deployed Streamlit URL here]_

---

## 🔐 Admin access

The admin panel (event overrides) uses a demo password: `admin123`

---

## 📊 Dataset

`vizag_accidents.csv` contains 50,000+ synthetically generated but realistically distributed accident records across 10 major Visakhapatnam roads, with fields for road name, date, hour, weather, cause, severity, vehicles involved, injuries, fatalities, and computed risk score.

---

## 🙋 Team / Hackathon

Built for the APAC GenAI Hackathon 2026 — Decision Intelligence Platform track, Urban Mobility and Transportation.
