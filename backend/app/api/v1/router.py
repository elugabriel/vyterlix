from fastapi import APIRouter

from app.api.v1 import (
    audit,
    auth,
    business,
    business_lists,
    goals,
    health,
    invitations,
    me,
    organizations,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(organizations.router)
api_router.include_router(invitations.org_router)
api_router.include_router(invitations.invitee_router)
api_router.include_router(audit.router)
api_router.include_router(business.industries_router)
api_router.include_router(business.profile_router)
api_router.include_router(goals.router)
api_router.include_router(business_lists.suggestions_router)
api_router.include_router(business_lists.router)
