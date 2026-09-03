from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user_from_request
from backend.app.database import get_db
from backend.app.models import User


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    return get_current_user_from_request(request, db)


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    from backend.app.auth import require_admin

    require_admin(user)
    return user
