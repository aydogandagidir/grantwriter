"""Seed the RAG corpus with 10 placeholder Horizon Europe RIA proposals.

Run from ``apps/api/`` with the database reachable via
``TEST_DATABASE_URL`` or ``DATABASE_URL`` (in that order):

    poetry run python -m scripts.seed_corpus               # offline embeddings
    poetry run python -m scripts.seed_corpus --offline     # explicit offline
    poetry run python -m scripts.seed_corpus --no-truncate # keep existing corpus

If ``OPENAI_API_KEY`` is set and ``--offline`` is NOT passed, the script
uses the real ``text-embedding-3-large`` embedder (~$0.01 for these 10
proposals). Otherwise it falls back to :class:`DeterministicEmbedder`
so local dev / CI can verify ingestion without an API key.

The placeholder data is deliberately fictitious — the real EC publications
ingest pipeline lands in S2.D7+.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

import asyncpg
from src.rag.base import Embedder
from src.rag.corpus_manager import CorpusManager
from src.rag.embedder import DeterministicEmbedder, OpenAIEmbedder

logger = logging.getLogger("seed_corpus")


SAMPLE_PROPOSALS: list[dict[str, Any]] = [
    {
        "external_id": "HORIZON-CL4-DT-001-2024",
        "title": "Distributed Twin Manufacturing Reference Architecture",
        "topic_id": "HORIZON-CL4-2024-DIGITAL-EMERGING-01",
        "funded_year": 2024,
        "budget_eur": 5_800_000,
        "metadata": {"placeholder": True, "domain": "manufacturing"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "The action delivers a federated digital-twin runtime for "
                "discrete manufacturing, advancing TRL from 4 to 6 across "
                "three pilots in EU automotive, aerospace, and shipbuilding.\n\n"
                "## 1.2 Methodology\n\n"
                "We combine OPC UA over TSN edge nodes with a federated-"
                "learning aggregator. A theory-of-change links lossless "
                "sensor capture → on-device pre-processing → federated "
                "model updates → enterprise-level dashboards.\n\n"
                "## 1.3 State of the art\n\n"
                "Prior art focuses on monolithic cloud twins; latency and "
                "bandwidth costs price-out high-frequency manufacturing.\n\n"
                "## 1.4 Open science practices\n\n"
                "All datasets released under CC-BY-4.0; code under EUPL-1.2."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "KIP1: 25% reduction in unplanned downtime across pilot lines.\n"
                "KIP2: 18% energy-cost reduction at the aggregated WP level.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "Standards-track contributions to OPC UA Information Model "
                "for Federated Twins (FX/FA WG)."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Six work packages, 36 months, EUR 5.8M total budget.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coordinator: Fraunhofer IPK. Industrial partners: BMW, "
                "Airbus, Damen Shipyards.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Eight partners across DE / NL / IT / FR / ES."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL5-CE-002-2024",
        "title": "Battery-Recycling Pilot Network for SMEs",
        "topic_id": "HORIZON-CL5-2024-CIRCULAR-01",
        "funded_year": 2024,
        "budget_eur": 4_200_000,
        "metadata": {"placeholder": True, "domain": "circular_economy"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "Industrial pilots for hydrometallurgical lithium recovery "
                "in SME-scale recyclers, reaching TRL 7 for sealed-cell "
                "feedstock.\n\n"
                "## 1.2 Methodology\n\n"
                "Solvent-extraction process tuned to mixed NMC/LFP feedstock; "
                "pilot capacity 50 t/day across two sites.\n\n"
                "## 1.3 State of the art\n\n"
                "Current EU recyclers dependent on imported pyrometallurgy.\n\n"
                "## 1.4 Open science practices\n\n"
                "DMP committed; pilot LCA datasets open via Zenodo."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "Reduces critical raw material imports by ~12% in pilot region.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "DNSH compliance verified per all six EU environmental "
                "objectives."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Five work packages over 30 months.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coordinator: SME consortium leader Eramet Norway.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Six partners across NO / SE / FR."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL4-AI-003-2025",
        "title": "Trustworthy AI Models for Industrial QC",
        "topic_id": "HORIZON-CL4-2025-DIGITAL-AI-01",
        "funded_year": 2025,
        "budget_eur": 6_400_000,
        "metadata": {"placeholder": True, "domain": "ai"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "Edge-deployed CV models with formally verified safety "
                "properties for textile and automotive QC.\n\n"
                "## 1.2 Methodology\n\n"
                "Marabou-based verification of YOLOv9-tiny variants.\n\n"
                "## 1.3 State of the art\n\n"
                "Prior verification work limited to MNIST-scale models.\n\n"
                "## 1.4 Open science practices\n\n"
                "All proofs and trained checkpoints public."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "Safety-critical CV adoption across regulated industries.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "Industrial standards body engagement (IEC TC 65)."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Seven WPs over 42 months.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coord: ETH Zürich; partners: TU Eindhoven, Politecnico Milano.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Six academic + four industrial partners."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL6-AGRI-004-2024",
        "title": "Precision Viticulture Decision Support",
        "topic_id": "HORIZON-CL6-2024-FARM-01",
        "funded_year": 2024,
        "budget_eur": 3_900_000,
        "metadata": {"placeholder": True, "domain": "agritech"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "Hyperspectral-imaging DSS for vineyard disease detection.\n\n"
                "## 1.2 Methodology\n\n"
                "UAV-mounted multispectral capture + CNN-based disease scoring.\n\n"
                "## 1.3 State of the art\n\n"
                "Sat-imagery products too coarse for SME vineyards.\n\n"
                "## 1.4 Open science practices\n\n"
                "Annotated imagery dataset released CC-BY-NC."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "10% reduction in fungicide use across pilot vineyards.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "DNSH evaluated against all six EU env objectives."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Four work packages, 30 months.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coord: INRAE; partners: small wineries in IT and PT.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Five partners across FR / IT / PT."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL4-PHOT-005-2025",
        "title": "Silicon-Photonics Chiplet Interposer",
        "topic_id": "HORIZON-CL4-2025-DIGITAL-EMERGING-02",
        "funded_year": 2025,
        "budget_eur": 7_100_000,
        "metadata": {"placeholder": True, "domain": "semiconductors"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "Chip-to-chip optical interposer reaching 1.6 Tbps/lane.\n\n"
                "## 1.2 Methodology\n\n"
                "Co-packaging CMOS photonic transceivers with HBM memory.\n\n"
                "## 1.3 State of the art\n\n"
                "Hyperscaler solutions tied to single-foundry processes.\n\n"
                "## 1.4 Open science practices\n\n"
                "Photonic IP blocks released under CERN-OHL-S."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "EU sovereignty in HPC interconnect tech.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "Pilot fab transition to high-volume manufacturing in WP6."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Eight WPs over 48 months, EUR 7.1M.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coord: imec; industrial partners: STMicro, Infineon.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Twelve partners across BE / DE / NL / IT / FR."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL4-CYB-006-2024",
        "title": "Quantum-Safe Cryptographic Library for IoT",
        "topic_id": "HORIZON-CL4-2024-CYBERSEC-02",
        "funded_year": 2024,
        "budget_eur": 4_900_000,
        "metadata": {"placeholder": True, "domain": "cybersecurity"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "Lightweight post-quantum library targeting Cortex-M and "
                "RISC-V microcontrollers.\n\n"
                "## 1.2 Methodology\n\n"
                "FIPS 203 ML-KEM and FIPS 204 ML-DSA implementations.\n\n"
                "## 1.3 State of the art\n\n"
                "Reference implementations exceed IoT memory budgets.\n\n"
                "## 1.4 Open science practices\n\n"
                "Side-channel test vectors public."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "EU IoT vendors crypto-agile by 2027.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "ETSI cybersecurity TC engagement."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Six WPs over 36 months.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coord: Inria; partners: Arm, NXP, KU Leuven.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Eight partners across FR / NL / BE / DE."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL5-ENER-007-2024",
        "title": "Long-Duration Energy Storage with Iron-Air Batteries",
        "topic_id": "HORIZON-CL5-2024-ENERGY-01",
        "funded_year": 2024,
        "budget_eur": 8_300_000,
        "metadata": {"placeholder": True, "domain": "energy"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "100-hour iron-air storage at grid-scale, EUR 20/kWh CAPEX.\n\n"
                "## 1.2 Methodology\n\n"
                "Reversible-rust electrochemistry with novel electrolyte.\n\n"
                "## 1.3 State of the art\n\n"
                "Lithium-ion cost trajectory plateauing at $90/kWh.\n\n"
                "## 1.4 Open science practices\n\n"
                "Cell-level performance datasets via OpenAIRE."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "Enables 80% renewables penetration in pilot grids.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "DSO partner engagement plan in WP7."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Nine WPs over 48 months.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coord: SINTEF; partners: Form Energy EU subsidiary.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Ten partners across NO / DE / FI / FR."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL3-DRR-008-2025",
        "title": "Wildfire Risk Forecasting with Multi-Modal Earth Observation",
        "topic_id": "HORIZON-CL3-2025-DRS-01",
        "funded_year": 2025,
        "budget_eur": 4_100_000,
        "metadata": {"placeholder": True, "domain": "civil_protection"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "Sub-100 m fire-spread forecasts at 6-hour horizon.\n\n"
                "## 1.2 Methodology\n\n"
                "Sentinel-2 + Copernicus EMS + ERA5 fed into transformer "
                "spatio-temporal model.\n\n"
                "## 1.3 State of the art\n\n"
                "Operational systems coarser than 1 km.\n\n"
                "## 1.4 Open science practices\n\n"
                "Operational forecasts open access via emergency data hub."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "10% reduction in wildfire-affected area in pilot regions.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "Civil-protection agency engagement plan."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Five WPs over 36 months.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coord: ECMWF; partners: Greek and Portuguese civil "
                "protection agencies.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Seven partners across UK / EL / PT / IT."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL1-HEA-009-2024",
        "title": "Federated Health-Data Network for Rare Disease Research",
        "topic_id": "HORIZON-CL1-2024-HEALTH-01",
        "funded_year": 2024,
        "budget_eur": 5_500_000,
        "metadata": {"placeholder": True, "domain": "health"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "Privacy-preserving federated analytics across 12 EU "
                "rare-disease registries.\n\n"
                "## 1.2 Methodology\n\n"
                "Secure multi-party computation + differential privacy.\n\n"
                "## 1.3 State of the art\n\n"
                "Existing federations limited to single disease groups.\n\n"
                "## 1.4 Open science practices\n\n"
                "Schema + analysis primitives released open source."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "Diagnostic odyssey reduced by ~18 months for 4 rare diseases.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "ERN coordination plan."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Six WPs over 48 months.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coord: EMBL-EBI; partners: 12 ERN reference centres.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Sixteen partners across UK / DE / FR / NL / IT / ES."
            ),
        },
    },
    {
        "external_id": "HORIZON-CL5-MOB-010-2025",
        "title": "Hydrogen Fuel-Cell Heavy Duty Logistics Pilots",
        "topic_id": "HORIZON-CL5-2025-MOBILITY-01",
        "funded_year": 2025,
        "budget_eur": 9_700_000,
        "metadata": {"placeholder": True, "domain": "mobility"},
        "sections": {
            "excellence": (
                "## 1.1 Objectives and ambition\n\n"
                "100-truck hydrogen logistics fleet trial across 4 EU "
                "corridors.\n\n"
                "## 1.2 Methodology\n\n"
                "70 MPa hydrogen refueling integrated with green hydrogen "
                "off-take agreements.\n\n"
                "## 1.3 State of the art\n\n"
                "Fragmented refueling network limits long-haul adoption.\n\n"
                "## 1.4 Open science practices\n\n"
                "Operational telemetry open under DAaaS license."
            ),
            "impact": (
                "## 2.1 Pathways towards impact\n\n"
                "1.2 MtCO2/yr abatement at fleet steady state.\n\n"
                "## 2.2 Measures to maximise impact\n\n"
                "Pan-European corridor regulatory engagement."
            ),
            "implementation": (
                "## 3.1 Work plan and resources\n\n"
                "Eight WPs over 48 months.\n\n"
                "## 3.2 Capacity of the participants\n\n"
                "Coord: Volvo Trucks; partners: Shell New Energies, DAF.\n\n"
                "## 3.3 Consortium as a whole\n\n"
                "Fourteen partners across SE / NL / DE / BE / DK."
            ),
        },
    },
]

_EXPECTED_PROPOSAL_COUNT = 10
assert len(SAMPLE_PROPOSALS) == _EXPECTED_PROPOSAL_COUNT, "docstring promises 10 proposals"


def _resolve_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set TEST_DATABASE_URL or DATABASE_URL before running this seed script.")
    return url


def _build_embedder(*, offline: bool) -> Embedder:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not offline and api_key:
        logger.info("Using OpenAIEmbedder (text-embedding-3-large).")
        return OpenAIEmbedder(api_key=api_key)
    logger.info("Using DeterministicEmbedder (offline mode).")
    return DeterministicEmbedder(seed_namespace="seed_corpus_v1")


async def _truncate(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM successful_proposal_chunks")
        await conn.execute("DELETE FROM successful_proposals_corpus")


async def run(*, offline: bool, truncate: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    pool = await asyncpg.create_pool(_resolve_database_url(), min_size=1, max_size=2)
    assert pool is not None

    try:
        if truncate:
            logger.info("Truncating successful_proposal[s_corpus|_chunks] tables.")
            await _truncate(pool)

        embedder = _build_embedder(offline=offline)
        manager = CorpusManager(pool=pool, embedder=embedder)

        ingested = 0
        for proposal in SAMPLE_PROPOSALS:
            corpus_id = await manager.add_proposal(
                programme_id="horizon_eu_ria",
                source="EC_publications",
                **proposal,
            )
            ingested += 1
            logger.info(
                "ingested",
                extra={"corpus_id": str(corpus_id), "title": proposal["title"]},
            )

        async with pool.acquire() as conn:
            chunk_count = await conn.fetchval("SELECT count(*) FROM successful_proposal_chunks")
            non_null = await conn.fetchval(
                "SELECT count(*) FROM successful_proposal_chunks WHERE embedding IS NOT NULL"
            )
        logger.info(
            "seed_complete",
            extra={"proposals": ingested, "chunks": chunk_count, "non_null_embeddings": non_null},
        )
        if chunk_count != non_null:
            logger.error(
                "embedding_gap",
                extra={"chunks": chunk_count, "non_null_embeddings": non_null},
            )
            return 1
        return 0
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the RAG corpus with sample HE proposals.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force the deterministic embedder even if OPENAI_API_KEY is set.",
    )
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Append to existing corpus instead of wiping it first.",
    )
    args = parser.parse_args()
    return asyncio.run(run(offline=args.offline, truncate=not args.no_truncate))


if __name__ == "__main__":
    sys.exit(main())
