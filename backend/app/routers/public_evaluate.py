"""Router for public, login-free plant evaluations."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.evaluation import PlantEvaluationCreate, PlantEvaluationResponse
from app.services.evaluation_service import create_evaluation

router = APIRouter(prefix="/public/evaluate", tags=["public-evaluate"])


@router.post(
    "/session/{session_token}/evaluations",
    response_model=PlantEvaluationResponse,
)
def handle_create_evaluation(
    session_token: str,
    data: PlantEvaluationCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    return create_evaluation(db, session_token, data, ip, user_agent)
