import os
import re


DEFAULT_PROJECT_NAME = "test_agisoft"
DEFAULT_CAMERA_MODEL = "mavic_3_rgb"


def sanitizar_nombre_proyecto(nombre):
    nombre = (nombre or "").strip()
    nombre = re.sub(r"[^A-Za-z0-9._-]+", "_", nombre)
    nombre = nombre.strip("._-")
    return nombre or DEFAULT_PROJECT_NAME


def normalizar_modelo_camara(modelo):
    modelo = (modelo or DEFAULT_CAMERA_MODEL).strip().lower().replace(" ", "_")
    alias = {
        "mavic3rgb": "mavic_3_rgb",
        "mavic_3_rgb": "mavic_3_rgb",
        "mavic_3_pro": "mavic_3_rgb",
        "mavic_3": "mavic_3_rgb",
        "rgb": "mavic_3_rgb",
        "mavic3m": "mavic_3m",
        "mavic_3_multispectral": "mavic_3m",
        "mavic3multispectral": "mavic_3m",
        "mavic_3m": "mavic_3m",
        "rededge": "rededge_m",
        "rededge_m": "rededge_m",
        "rededge-m": "rededge_m",
        "rededgem": "rededge_m",
        "micasense_rededge_m": "rededge_m",
        "micasense_rededge-m": "rededge_m",
    }
    return alias.get(modelo, DEFAULT_CAMERA_MODEL)
