from fastapi import APIRouter

from app.api.v1 import (
    audit,
    auth,
    benchmarks,
    business,
    business_health,
    business_lists,
    data_quality,
    goals,
    health,
    imports,
    integrations,
    invitations,
    jobs,
    kpis,
    me,
    onboarding,
    organizations,
    seasons,
    settings,
    trading,
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
api_router.include_router(seasons.router)
api_router.include_router(settings.router)
api_router.include_router(benchmarks.router)
api_router.include_router(onboarding.router)
api_router.include_router(imports.router)
api_router.include_router(imports.sources_router)
api_router.include_router(jobs.router)
api_router.include_router(integrations.router)
api_router.include_router(kpis.router)
api_router.include_router(business_health.router)
api_router.include_router(trading.sales_router)
api_router.include_router(trading.expenses_router)
api_router.include_router(trading.customers_router)
api_router.include_router(trading.suppliers_router)
api_router.include_router(trading.products_router)
api_router.include_router(trading.stock_router)
api_router.include_router(data_quality.router)
