from fastapi import FastAPI

import backend.runtime as runtime
from backend.routes import router


def create_app():
    app = FastAPI(title=runtime.app_title)
    app.include_router(router)
    return app


app = create_app()
