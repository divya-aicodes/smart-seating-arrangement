"""Shared Flask extensions.

Keeping extension objects outside the application module avoids circular imports and
allows the model metadata to be used by Flask-Migrate and isolated tests.
"""

from flask_migrate import Migrate
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase
import sqlite3


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
login_manager = LoginManager()


def rate_limit_key():
    """Prefer an authenticated identity; fall back to the effective client address."""
    from flask_login import current_user

    if current_user.is_authenticated:
        return f"user:{current_user.get_id()}"
    return f"ip:{get_remote_address()}"


limiter = Limiter(key_func=rate_limit_key)
csrf = CSRFProtect()


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    """SQLite disables foreign-key enforcement unless it is enabled per connection."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
