"""The RF toolset: link budgets and cell-tower lookup.

These are the daily tools of mobile network operators, wireless ISPs and
utilities running SCADA telemetry or IoT fleets: how far a radio reaches for a
given power and frequency, and what is already on the air nearby.
"""

# NOTE: deliberately no `from __future__ import annotations` here.
# Response models are defined inside register(), and PEP 563 string
# annotations cannot be resolved from a function-local scope -- the SDK
# raises InvalidSignature. Eager evaluation resolves them correctly.

from typing import Annotated, Any, Literal

from pydantic import Field

from .._sdk import MCPServer, compute, network
from ..geo import rf
from ..models import GeoModel, ResponseMeta
from ._shared import meta, require_metres, resolve

__all__ = ["register"]


def register(mcp: MCPServer) -> None:
    class LinkBudgetResult(GeoModel):
        eirp_dbm: float
        max_allowable_path_loss_db: float
        shadow_margin_db: float
        max_path_loss_with_margin_db: float
        cell_radius_km: float
        cell_area_km2: float = Field(
            description=(
                "Hexagonal area one site serves when cells tile a plan: 2.598 * r^2. "
                "One isolated circular cell covers pi * r^2."
            )
        )
        model: str
        environment: str
        inputs: dict[str, Any]
        warnings: list[str] = []
        distance_km: float | None = Field(default=None, exclude_if=lambda v: v is None)
        path_loss_db: float | None = Field(default=None, exclude_if=lambda v: v is None)
        received_power_dbm: float | None = Field(default=None, exclude_if=lambda v: v is None)
        power_margin_db: float | None = Field(
            default=None,
            exclude_if=lambda v: v is None,
            description="Received power above sensitivity, before the shadow margin.",
        )
        fade_margin_db: float | None = Field(
            default=None,
            exclude_if=lambda v: v is None,
            description="Power margin remaining after subtracting shadow_margin_db.",
        )
        closes_on_power: bool | None = Field(
            default=None,
            exclude_if=lambda v: v is None,
            description="fade_margin_db >= 0. Terrain clearance is a separate check.",
        )
        meta: ResponseMeta

    @mcp.tool(annotations=compute())
    def rf_link_budget(
        frequency_mhz: Annotated[
            float, Field(gt=0, description="Carrier frequency in MHz.")
        ] = 1800.0,
        tx_power_dbm: Annotated[float, Field(description="Transmit power in dBm.")] = 43.0,
        tx_gain_dbi: Annotated[float, Field(description="Transmit antenna gain in dBi.")] = 15.0,
        rx_gain_dbi: Annotated[float, Field(description="Receive antenna gain in dBi.")] = 0.0,
        losses_db: Annotated[
            float, Field(description="Feeder, connector and body losses in dB.")
        ] = 3.0,
        rx_sensitivity_dbm: Annotated[
            float, Field(description="Receiver sensitivity in dBm.")
        ] = -105.0,
        tx_height_m: Annotated[
            float, Field(gt=0, description="Transmit antenna height above ground.")
        ] = 45.0,
        rx_height_m: Annotated[
            float, Field(gt=0, description="Receive antenna height above ground.")
        ] = 1.5,
        environment: Annotated[
            Literal["open", "rural", "suburban", "urban", "dense_urban"],
            Field(description="Clutter type; sets the metro correction and default fade margin."),
        ] = "urban",
        model: Annotated[
            Literal["free_space", "two_ray", "hata", "cost231_hata"],
            Field(
                description=(
                    "Propagation model. free_space: theoretical minimum, any frequency. "
                    "two_ray: flat rural ground reflection. hata: 150-1500 MHz. "
                    "cost231_hata: 1500-2000 MHz."
                )
            ),
        ] = "cost231_hata",
        shadow_margin_db: Annotated[
            float | None,
            Field(
                description=(
                    "Shadow-fading margin in dB. Defaults to a typical value for the environment."
                )
            ),
        ] = None,
        distance_km: Annotated[
            float | None,
            Field(
                gt=0,
                allow_inf_nan=False,
                description=(
                    "Known link distance in km. Returns path loss, received power and the "
                    "fade margin after the separate shadow margin. For point-to-point use free_space."
                ),
            ),
        ] = None,
    ) -> LinkBudgetResult:
        """Calculate maximum allowable path loss and the resulting cell radius.

        Works out EIRP, the loss budget available to the link, and how far that
        reaches under the chosen propagation model. Use it to size cell coverage,
        compare frequency bands, or check whether a proposed link closes on power.

        For a fixed point-to-point link, pass model="free_space" and distance_km
        for received power and fade margin. Check clearance separately with
        terrain_profile and antenna heights. Free-space power closure assumes
        an unobstructed path and does not establish a working radio link.

        cell_area_km2 is the hexagonal area one site serves when cells tile a
        network plan (2.598 * r^2), not the footprint of one isolated circular
        cell (pi * r^2) -- about 17% smaller, so summing it undercounts the
        coverage of sites that do not overlap.

        Rejects an empirical model outside its frequency range and names a valid
        model to retry. Other out-of-range empirical parameters carry warnings.

        This is a statistical model over an idealised environment, not a specific
        path: it answers "how far does this radio reach here?", not "does this
        exact link clear that ridge?". For the ground between two points, use
        terrain_profile. For what is already transmitting nearby, use rf_towers.
        """
        budget = rf.link_budget(
            frequency_mhz=frequency_mhz,
            tx_power_dbm=tx_power_dbm,
            tx_gain_dbi=tx_gain_dbi,
            rx_gain_dbi=rx_gain_dbi,
            losses_db=losses_db,
            rx_sensitivity_dbm=rx_sensitivity_dbm,
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
            environment=environment,
            shadow_margin_db=shadow_margin_db,
            model=model,
            distance_km=distance_km,
        )
        margin = budget.max_allowable_path_loss_db - budget.max_path_loss_with_margin_db
        return LinkBudgetResult(
            eirp_dbm=round(budget.eirp_dbm, 2),
            max_allowable_path_loss_db=round(budget.max_allowable_path_loss_db, 2),
            shadow_margin_db=round(margin, 2),
            max_path_loss_with_margin_db=round(budget.max_path_loss_with_margin_db, 2),
            cell_radius_km=round(budget.cell_radius_km, 4),
            cell_area_km2=round(budget.cell_area_km2, 4),
            model=model,
            environment=environment,
            inputs={
                "frequency_mhz": frequency_mhz,
                "tx_power_dbm": tx_power_dbm,
                "tx_gain_dbi": tx_gain_dbi,
                "rx_gain_dbi": rx_gain_dbi,
                "losses_db": losses_db,
                "rx_sensitivity_dbm": rx_sensitivity_dbm,
                "tx_height_m": tx_height_m,
                "rx_height_m": rx_height_m,
            },
            warnings=budget.warnings,
            distance_km=distance_km,
            path_loss_db=round(budget.path_loss_db, 2) if budget.path_loss_db is not None else None,
            received_power_dbm=(
                round(budget.received_power_dbm, 2)
                if budget.received_power_dbm is not None
                else None
            ),
            power_margin_db=(
                round(budget.power_margin_db, 2) if budget.power_margin_db is not None else None
            ),
            fade_margin_db=(
                round(budget.fade_margin_db, 2) if budget.fade_margin_db is not None else None
            ),
            closes_on_power=budget.closes_on_power,
            meta=meta(
                offline=True,
                notes=[
                    "Power closure depends on the stated radio settings, losses and shadow margin. "
                    "Free space is a minimum-loss assumption. No terrain, diffraction, rain or "
                    "additional clutter loss is included. Use terrain_profile for path clearance."
                ]
                if distance_km is not None
                else [],
            ),
        )

    # ------------------------------------------------------------------------
    class TowerResult(GeoModel):
        latitude: float
        longitude: float
        radius_m: float
        provider: str
        count: int
        towers: list[dict[str, Any]]
        meta: ResponseMeta

    @mcp.tool(annotations=network())
    def rf_towers(
        location: Annotated[
            str, Field(description="Search centre, as coordinates or a place name.")
        ],
        radius_m: Annotated[
            float, Field(gt=0, le=10000, description="Search radius in metres.")
        ] = 1000.0,
    ) -> TowerResult:
        """Find known cell towers near a location.

        Returns crowdsourced tower records with radio technology (GSM/UMTS/LTE/NR),
        operator MCC-MNC and approximate position. Useful for coverage benchmarking,
        IoT and fixed-wireless site qualification, and understanding why signal is
        poor at a specific address.

        Uses OpenCelliD when OPENCELLID_API_KEY is set, otherwise the keyless
        BeaconDB. Coverage is crowdsourced and sparse outside dense urban areas --
        an empty result means no contributor has mapped the area, not that no
        towers exist.
        """
        from ..net.providers import cells

        require_metres(radius_m, name="radius_m", example="1000  (a 1 km search radius)")
        loc = resolve(location)
        lat, lon = loc.as_tuple()
        towers, provider, cached = cells.towers_near(lat, lon, radius_m)
        notes = []
        if not towers:
            notes.append(
                "No tower records here. Crowdsourced databases are sparse outside "
                "dense urban areas; this is an absence of data, not of towers."
            )
        return TowerResult(
            latitude=round(lat, 6),
            longitude=round(lon, 6),
            radius_m=radius_m,
            provider=provider,
            count=len(towers),
            towers=towers,
            meta=meta(provider, cached=cached, notes=notes, resolved=[loc]),
        )
