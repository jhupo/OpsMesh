from fastapi import Depends
from sqlalchemy.orm import Session

from opsmesh.governance.policies.service import AdminPolicyService
from opsmesh.shared.db.session import get_db_session


def admin_policy_service(session: Session = Depends(get_db_session)) -> AdminPolicyService:
    return AdminPolicyService(session)
