import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Plain local run (no Docker, works on Windows too). In Docker/Kubernetes
    # gunicorn starts the app instead and this block is not used.
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")),
            debug=False, threaded=True)
