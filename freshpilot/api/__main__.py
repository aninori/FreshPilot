"""Start the local API from any working directory."""

import os
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parents[2])
    uvicorn.run("freshpilot.api.app:app", host="127.0.0.1", port=8000)
