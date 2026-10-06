import os
import sys
import tempfile
from pathlib import Path

os.environ["BUDGET_DATA_DIR"] = tempfile.mkdtemp(prefix="budget-test-")
os.environ["BUDGET_DEV_USER"] = "test:Test"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "budget_tracker"))

import pytest  # noqa: E402

from app import prefs  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.routers import misc  # noqa: E402
from app.seed import seed  # noqa: E402

misc.REFRESH_RATES = False  # no background network calls from the settings API


@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    seed(s)
    # Existing tests were written for the Turkey setup; generic tests save their own (e.g. USD/EUR) preferences
    prefs.save(s, base_currency="TRY", currencies=["TRY", "USD", "EUR"], region="tr", setup_done=True)
    yield s
    s.close()
