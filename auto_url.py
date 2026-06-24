import json, os, requests, time, subprocess, re

RENDER_API_KEY = os.environ.get("RENDER_API_KEY")
SERVICE_ID = os.environ.get("RENDER_SERVICE_ID")
NGROK_URL = "http://127.0.0.1:4040/api/tunnels"

if not RENDER_API_KEY or not SERVICE_ID:
    print("Faltan RENDER_API_KEY o RENDER_SERVICE_ID")
    exit(1)

def obtener_ngrok_url():
    for _ in range(30):
        try:
            r = requests.get(NGROK_URL, timeout=3)
            data = r.json()
            for tunnel in data.get("tunnels", []):
                if tunnel.get("proto") == "https":
                    return tunnel["public_url"]
        except Exception:
            time.sleep(2)
    return None

def actualizar_render(url):
    headers = {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "serviceId": SERVICE_ID,
        "envVars": [
            {"key": "API_BASE_URL", "value": url}
        ]
    }
    r = requests.put(
        f"https://api.render.com/v1/services/{SERVICE_ID}/env-vars",
        headers=headers, json=body
    )
    return r.ok

if __name__ == "__main__":
    url = obtener_ngrok_url()
    if url:
        print(f"URL ngrok: {url}")
        if actualizar_render(url):
            print("Render actualizado")
        else:
            print("Error al actualizar Render")
    else:
        print("No se pudo obtener URL de ngrok")
