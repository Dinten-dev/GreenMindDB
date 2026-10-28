"""Early-access administration under the existing platform-operator policy."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.access import AccessInvitation
from app.models.form_submission import FormSubmission
from app.models.user import Role, User
from app.routers.administration import require_management_admin
from app.services.invitations import invitation_view, send_invitation

router = APIRouter(prefix="/admin", tags=["access-invitations"])


def require_invitation_admin(user: User = Depends(require_management_admin)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(403, "Platform administrator role required")
    return user


class InvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: UUID


@router.get("/access-requests")
def access_requests(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user: User = Depends(require_invitation_admin),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    predicate = FormSubmission.form_type == "early_access"
    total = db.scalar(select(func.count()).select_from(FormSubmission).where(predicate))
    submissions = (
        db.execute(
            select(FormSubmission)
            .where(predicate)
            .order_by(FormSubmission.created_at.desc(), FormSubmission.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    items = []
    for submission in submissions:
        latest = db.execute(
            select(AccessInvitation)
            .where(AccessInvitation.submission_id == submission.id)
            .order_by(AccessInvitation.created_at.desc(), AccessInvitation.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        items.append(
            {
                "id": str(submission.id),
                "email": submission.email,
                "name": submission.name,
                "company": submission.company,
                "country": submission.country,
                "message": submission.message,
                "created_at": submission.created_at,
                "invitation": invitation_view(latest) if latest else None,
            }
        )
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def _dispatch(db, submission_id, creator, previous_id=None):
    invitation, accepted = send_invitation(db, submission_id, creator, previous_id)
    if not accepted:
        raise HTTPException(
            502, {"message": "Invitation dispatch failed", "invitation_id": str(invitation.id)}
        )
    return invitation_view(invitation)


@router.post("/invitations", status_code=201)
def invite(
    data: InvitationRequest,
    user: User = Depends(require_invitation_admin),
    db: Session = Depends(get_db),
):
    return _dispatch(db, data.submission_id, user)


@router.post("/invitations/{invitation_id}/resend", status_code=201)
def resend(
    invitation_id: UUID,
    user: User = Depends(require_invitation_admin),
    db: Session = Depends(get_db),
):
    previous = db.get(AccessInvitation, invitation_id)
    if previous is None:
        raise HTTPException(404, "Invitation not found")
    return _dispatch(db, previous.submission_id, user, invitation_id)
