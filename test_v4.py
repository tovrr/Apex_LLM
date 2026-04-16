import json
import os
import requests

# Local API endpoint
url = "http://127.0.0.1:8000/chat"

# API key from environment
api_key = os.getenv("APEX_API_KEY")
if not api_key:
    raise RuntimeError("APEX_API_KEY is required in environment")

headers = {
    "Content-Type": "application/json",
    "X-API-Key": api_key,
}

# SRE trap question
payload = {
    "question": "Probleme SRE : J'ai une API avec un cache LRU de taille 50. Je recois 200 requetes par seconde avec 150 cles uniques reparties uniformement. Quel est mon probleme principal et comment le resoudre mathematiquement ?",
    "mots_max": 400,
    "model_tier": "fast",
}

print("Envoi de la requete a Apex V4...")
response = requests.post(url, headers=headers, json=payload, timeout=60)

if response.status_code == 200:
    data = response.json()
    print("\nReponse d'Apex:\n")
    print(data.get("reponse_apex", "Pas de reponse trouvee."))
    print("\n--- raw json ---")
    print(json.dumps(data, ensure_ascii=False, indent=2))
else:
    print(f"Erreur {response.status_code}: {response.text}")
