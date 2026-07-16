"""Central HTTP exception mapping for domain errors.

Register all handlers in main.py via register_exception_handlers(app).

Domain → HTTP mapping:
  WorkflowError              → 400 Bad Request
  PermissionDenied           → 403 Forbidden
  IllegalTransition          → 409 Conflict
  sqlalchemy.exc.IntegrityError → 409 Conflict (concurrent duplicate-approval race)
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.versioning.lifecycle import IllegalTransition, PermissionDenied
from app.core.workflow.rules import WorkflowError


def register_exception_handlers(app: FastAPI) -> None:
    """Attach domain-exception→HTTP handlers to *app*."""

    @app.exception_handler(WorkflowError)
    async def workflow_error_handler(request: Request, exc: WorkflowError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(request: Request, exc: PermissionDenied) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(IllegalTransition)
    async def illegal_transition_handler(request: Request, exc: IllegalTransition) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Conflict: duplicate or constraint violation."})
