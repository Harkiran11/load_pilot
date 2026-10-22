from decimal import localcontext

from schema.analytics.recommendation_comparison import CohortMetrics, RecommendationComparison
from service.pricing.economics import CALCULATION_CONTEXT, money


def compare_recommendations(request):
    with localcontext(CALCULATION_CONTEXT):
        def metrics(row):
            return CohortMetrics(completed_loads=row.completed_loads, realized_profit=money(row.realized_profit),
                profit_per_completed_load=money(row.realized_profit / row.completed_loads), empty_miles=money(row.empty_miles),
                empty_miles_per_completed_load=money(row.empty_miles / row.completed_loads),
                utilization_pct=money(row.occupied_unit_hours / row.available_unit_hours * 100))
        baseline, candidate = metrics(request.baseline), metrics(request.candidate)
        return RecommendationComparison(evidence=request, baseline=baseline, candidate=candidate,
            candidate_minus_baseline={field: getattr(candidate, field) - getattr(baseline, field)
                for field in ('realized_profit', 'profit_per_completed_load', 'empty_miles',
                              'empty_miles_per_completed_load', 'utilization_pct')})
