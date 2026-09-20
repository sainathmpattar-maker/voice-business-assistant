"""
deploy_render.py — Creates and configures the Polaris backend service on Render via REST API.
"""
import json
import time
import requests

RENDER_API_KEY = "rnd_IbODhFoi3EAwRpZWQKBHeyV1aQbI"
HEADERS = {
    "Authorization": f"Bearer {RENDER_API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def main():
    # 1. Get Owner ID
    print("Fetching Render owners / user info...")
    res = requests.get("https://api.render.com/v1/owners", headers=HEADERS)
    if not res.ok:
        print(f"Error fetching owners: {res.status_code} {res.text}")
        return
    owners = res.json()
    if not owners:
        print("No owners found.")
        return
    owner_id = owners[0]["owner"]["id"]
    print(f"Found owner ID: {owner_id} ({owners[0]['owner'].get('email') or owners[0]['owner'].get('name')})")

    # 2. Check if polaris-backend service already exists
    print("Checking existing services...")
    res = requests.get(f"https://api.render.com/v1/services?ownerId={owner_id}", headers=HEADERS)
    existing_service = None
    if res.ok:
        for s in res.json():
            service = s.get("service", {})
            if service.get("name") == "polaris-backend":
                existing_service = service
                break

    # Read env vars from backend/.env
    env_map = {}
    with open("backend/.env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_map[k.strip()] = v.strip().strip('"').strip("'")

    database_url = env_map.get("DATABASE_URL", "")
    groq_api_key = env_map.get("GROQ_API_KEY", "")
    gemini_api_key = env_map.get("GEMINI_API_KEY", "")

    env_vars = [
        {"key": "DATABASE_URL", "value": database_url},
        {"key": "GROQ_API_KEY", "value": groq_api_key},
        {"key": "GEMINI_API_KEY", "value": gemini_api_key},
        {"key": "ALLOWED_ORIGINS", "value": "https://frontend-ochre-delta-63.vercel.app,https://frontend-jdkmpt28f-a-69a9.vercel.app,http://localhost:5173"},
        {"key": "MERCHANT_ID", "value": "1"},
        {"key": "LOG_LEVEL", "value": "INFO"},
    ]

    if existing_service:
        service_id = existing_service["id"]
        service_url = existing_service.get("serviceDetails", {}).get("url")
        print(f"Service polaris-backend already exists with ID: {service_id}, URL: {service_url}")
    else:
        print("Creating web service polaris-backend on Render...")
        payload = {
            "type": "web_service",
            "name": "polaris-backend",
            "ownerId": owner_id,
            "repo": "https://github.com/sainathmpattar-maker/voice-business-assistant",
            "branch": "master",
            "rootDir": "backend",
            "autoDeploy": "yes",
            "serviceDetails": {
                "env": "python",
                "plan": "free",
                "region": "oregon",
                "envSpecificDetails": {
                    "buildCommand": "pip install -r requirements.txt && python seed.py",
                    "startCommand": "uvicorn main:app --host 0.0.0.0 --port $PORT"
                },
                "envVars": env_vars
            }
        }
        res = requests.post("https://api.render.com/v1/services", headers=HEADERS, json=payload)
        if not res.ok:
            print(f"Error creating service: {res.status_code} {res.text}")
            return
        service_data = res.json()
        print("Service creation response:", json.dumps(service_data, indent=2))
        service_obj = service_data.get("service", service_data)
        service_id = service_obj.get("id")
        service_url = service_obj.get("serviceDetails", {}).get("url")
        print(f"Service created successfully! ID: {service_id}, URL: {service_url}")

    # 3. Monitor Deploy Status
    print(f"\nMonitoring deployment for service {service_id}...")
    for _ in range(60):
        time.sleep(10)
        res = requests.get(f"https://api.render.com/v1/services/{service_id}/deploys?limit=1", headers=HEADERS)
        if res.ok and res.json():
            latest_deploy = res.json()[0]["deploy"]
            status = latest_deploy.get("status")
            print(f"Deploy status: {status} (ID: {latest_deploy.get('id')})")
            if status == "live":
                print("\nBackend is LIVE on Render!")
                print(f"Render Service URL: {service_url}")
                break
            elif status in ["build_failed", "update_failed", "canceled"]:
                print(f"Deploy failed with status: {status}")
                break
        else:
            print("Fetching deploy status...")

    # Fetch updated service details for URL
    res = requests.get(f"https://api.render.com/v1/services/{service_id}", headers=HEADERS)
    if res.ok:
        svc = res.json()
        service_url = svc.get("serviceDetails", {}).get("url")
        print(f"\nFinal Backend URL: {service_url}")

if __name__ == "__main__":
    main()
