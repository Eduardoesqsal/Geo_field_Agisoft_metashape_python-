from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import backend.runtime as runtime
from backend.routes import router


def create_app():
    app = FastAPI(title=runtime.app_title)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app()
