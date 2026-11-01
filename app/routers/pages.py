"""Server-rendered page routes."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Render the chat application shell."""
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )
