"""The member's investor profile (what the AI Advisor tailors its guidance to) and the advice log (what it said).

* ``InvestorProfile`` — one per member: goals, horizon, monthly budget, savings band, constraints, and the answers to
  the risk questions; the risk score and band are computed by code (``services.investor_profile``), never by the AI.
* ``AdviceLog`` — one row per Advisor answer: the question, the answer, the profile it used, which data tools ran,
  the model, and flags from the automatic review (e.g. buy/sell instructions). The member can read their own log.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class InvestorProfile(Base):
    __tablename__ = "investor_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    goals: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # e.g. ["retirement", "income"]
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)  # under_1y | 1_3y | 3_7y | over_7y
    monthly_budget_aed: Mapped[int | None] = mapped_column(Integer)
    savings_band: Mapped[str | None] = mapped_column(String(16))  # under_50k | 50k_250k | 250k_1m | over_1m
    experience: Mapped[str] = mapped_column(String(16), nullable=False)  # none | some | experienced
    constraints: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # e.g. ["sharia", "no_crypto"]
    answers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(16), nullable=False)  # cautious | balanced | growth | adventurous
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AdviceLog(Base):
    __tablename__ = "advice_log"
    __table_args__ = (Index("ix_advice_log_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[dict | None] = mapped_column(JSON)  # the profile as it was when the answer was given
    symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    mode: Mapped[str] = mapped_column(String(12), nullable=False)  # ai | data_only
    model: Mapped[str | None] = mapped_column(String(80))
    flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


__all__ = ["AdviceLog", "InvestorProfile"]
