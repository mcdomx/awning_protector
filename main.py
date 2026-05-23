import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "8767"))
    uvicorn.run("app.api:app", host="0.0.0.0", port=port, reload=False)
