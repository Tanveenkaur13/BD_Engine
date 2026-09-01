# Load .env before anything reads os.environ. Importing the package is the one
# thing every entry point does — uvicorn app.main:app, run_pipeline.py and
# seed_research.py all go through here — so this is the only hook that catches
# all of them.
from . import env as _env

_env.load()
