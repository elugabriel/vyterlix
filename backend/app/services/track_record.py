"""How each kind of action has worked for this business, from the outcomes recorded so far."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.actions import BusinessIntervention
from app.models.outcomes import InterventionOutcome
from app.outcomes import rules


@dataclass
class Record:
    successful: int = 0
    partially_successful: int = 0
    unsuccessful: int = 0
    inconclusive: int = 0

    @property
    def score(self) -> int:
        """Inconclusive results say nothing either way, so they do not count."""
        return rules.history_score(self.successful, self.partially_successful, self.unsuccessful)

    @property
    def decided(self) -> int:
        return self.successful + self.partially_successful + self.unsuccessful


def load(db: Session) -> dict[str, Record]:
    """The record for each library action that has been tried here (by its code)."""
    rows = db.execute(
        select(BusinessIntervention.library_code, InterventionOutcome.outcome, func.count())
        .join(InterventionOutcome, InterventionOutcome.intervention_id == BusinessIntervention.id)
        .where(BusinessIntervention.library_code.is_not(None))
        .group_by(BusinessIntervention.library_code, InterventionOutcome.outcome)
    ).all()
    records: dict[str, Record] = {}
    for code, outcome, count in rows:
        setattr(records.setdefault(code, Record()), outcome, count)
    return records
