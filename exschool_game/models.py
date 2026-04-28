from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketDecision:
    agent_change: int
    marketing_investment: float
    price: float
    subscribed_market_report: bool = True


@dataclass
class SimulationInput:
    round_id: str
    loan_delta: float
    workers: int
    engineers: int
    worker_salary: float
    engineer_salary: float
    management_investment: float
    quality_investment: float
    research_investment: float
    products_planned: int
    market_decisions: dict[str, MarketDecision]


@dataclass
class CampaignSimulationInput:
    rounds: dict[str, SimulationInput]


@dataclass(frozen=True)
class CampaignState:
    current_cash: float
    current_debt: float
    workers: int
    engineers: int
    worker_salary: float
    engineer_salary: float
    market_agents_after: dict[str, int]
    previous_management_index: float
    previous_quality_index: float
    worker_avg_salary: float
    engineer_avg_salary: float
    worker_recent: int
    worker_mature: int
    worker_experienced: int
    engineer_recent: int
    engineer_mature: int
    engineer_experienced: int
    component_capacity: float
    product_capacity: float
    component_inventory: float
    product_inventory: float
    active_patents: int
    accumulated_research_investment: float
    last_round_id: str | None
