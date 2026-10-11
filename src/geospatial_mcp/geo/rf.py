"""Radio propagation and link-budget mathematics.

How far a transmitter reaches for a given power, frequency and environment is
an everyday question for mobile network operators, wireless ISPs, utilities
running SCADA telemetry and IoT fleets.

Every model here is empirical or closed-form, published in the open literature
and implemented from the formulas. Each carries its documented validity range,
and :func:`model_warnings` reports when a caller has strayed outside it --
because an empirical model used out of range returns a confident number that
happens to be meaningless, and nothing else in the stack will catch that.

These are statistical models over an idealised environment. They answer "how
far does this radio reach in terrain of this character?", never "does this
specific link clear that specific ridge?" -- which needs a terrain profile and
a diffraction model this package does not provide.

References:
  ITU-R P.525, "Calculation of free-space attenuation".
  Okumura et al. (1968); Hata, IEEE Trans. Veh. Technol. 29(3), 317-325, 1980.
  COST Action 231 final report, "Digital mobile radio towards future
  generation systems", Ch. 4 (COST 231-Hata).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..errors import GeoInputError

__all__ = [
    "ENVIRONMENTS",
    "MODELS",
    "SPEED_OF_LIGHT",
    "LinkBudget",
    "cell_radius_km",
    "cost231_hata_path_loss_db",
    "free_space_path_loss_db",
    "hata_path_loss_db",
    "link_budget",
    "model_warnings",
    "path_loss_db",
    "two_ray_path_loss_db",
    "wavelength_m",
]

SPEED_OF_LIGHT = 299_792_458.0

#: Environment -> (COST231 metro correction dB, typical shadow-fade margin dB).
ENVIRONMENTS: dict[str, tuple[float, float]] = {
    "open": (0.0, 4.0),
    "rural": (0.0, 6.0),
    "suburban": (0.0, 9.0),
    "urban": (3.0, 13.0),
    "dense_urban": (3.0, 16.0),
}

MODELS = ("free_space", "two_ray", "hata", "cost231_hata")

#: Published validity ranges, used by :func:`model_warnings`.
_VALIDITY: dict[str, dict[str, tuple[float, float]]] = {
    "hata": {
        "frequency_mhz": (150.0, 1500.0),
        "tx_height_m": (30.0, 200.0),
        "rx_height_m": (1.0, 10.0),
        "distance_km": (1.0, 20.0),
    },
    "cost231_hata": {
        "frequency_mhz": (1500.0, 2000.0),
        "tx_height_m": (30.0, 200.0),
        "rx_height_m": (1.0, 10.0),
        "distance_km": (1.0, 20.0),
    },
}


def _require_positive_frequency(frequency_mhz: float) -> None:
    """Guard every log10(frequency) call site.

    Without this a zero or negative frequency surfaces as a bare
    ``ValueError: math domain error``, which tells the caller nothing about
    which argument was wrong or what a good value looks like.
    """
    if frequency_mhz <= 0:
        raise GeoInputError.with_example(
            got=frequency_mhz,
            problem="Frequency must be positive.",
            example="1800  (LTE band 3)",
        )


def wavelength_m(frequency_mhz: float) -> float:
    """Wavelength in metres for a frequency in MHz."""
    _require_positive_frequency(frequency_mhz)
    return SPEED_OF_LIGHT / (frequency_mhz * 1e6)


def free_space_path_loss_db(frequency_mhz: float, distance_km: float) -> float:
    """ITU-R P.525 free-space path loss.

    The theoretical floor: no terrain, no obstruction, no ground reflection.
    Valid at any frequency and distance, which makes it the right default for
    satellite, microwave line-of-sight and short indoor links.
    """
    if distance_km <= 0:
        raise GeoInputError.with_example(
            got=distance_km,
            problem="Distance must be positive.",
            example="5.0",
        )
    _require_positive_frequency(frequency_mhz)
    return 32.44 + 20.0 * math.log10(frequency_mhz) + 20.0 * math.log10(distance_km)


def two_ray_path_loss_db(
    frequency_mhz: float, distance_km: float, tx_height_m: float, rx_height_m: float
) -> float:
    """Two-ray flat-earth ground-reflection model.

    Beyond the breakpoint distance, received power falls as d^-4 rather than
    d^-2 because the direct and ground-reflected rays arrive out of phase.
    Below the breakpoint it degenerates to free space. Useful for rural
    point-to-point links over flat ground and for vehicle-to-vehicle.
    """
    if min(tx_height_m, rx_height_m) <= 0:
        raise GeoInputError.with_example(
            got=(tx_height_m, rx_height_m),
            problem="Antenna heights must be positive.",
            example="30 and 1.5",
        )
    distance_m = distance_km * 1000.0
    breakpoint_m = 4.0 * tx_height_m * rx_height_m / wavelength_m(frequency_mhz)
    if distance_m < breakpoint_m:
        return free_space_path_loss_db(frequency_mhz, distance_km)
    return 40.0 * math.log10(distance_m) - (
        20.0 * math.log10(tx_height_m) + 20.0 * math.log10(rx_height_m)
    )


def _require_positive_height(tx_height_m: float, rx_height_m: float) -> None:
    """Antenna heights feed log10 as well."""
    if min(tx_height_m, rx_height_m) <= 0:
        raise GeoInputError.with_example(
            got=(tx_height_m, rx_height_m),
            problem="Antenna heights must be positive.",
            example="45 for the base station and 1.5 for the handset",
        )


def _hata_mobile_correction(frequency_mhz: float, rx_height_m: float, environment: str) -> float:
    """Receiver-height correction factor a(h_m)."""
    _require_positive_frequency(frequency_mhz)
    if environment in {"urban", "dense_urban"} and frequency_mhz >= 300.0:
        return 3.2 * (math.log10(11.75 * rx_height_m) ** 2) - 4.97
    return (1.1 * math.log10(frequency_mhz) - 0.7) * rx_height_m - (
        1.56 * math.log10(frequency_mhz) - 0.8
    )


def hata_path_loss_db(
    frequency_mhz: float,
    distance_km: float,
    tx_height_m: float,
    rx_height_m: float,
    environment: str = "urban",
) -> float:
    """Okumura-Hata path loss, 150-1500 MHz.

    The standard model for VHF/UHF: broadcast, public-safety radio, LoRa and
    other sub-GHz IoT.
    """
    _require_positive_height(tx_height_m, rx_height_m)
    correction = _hata_mobile_correction(frequency_mhz, rx_height_m, environment)
    loss = (
        69.55
        + 26.16 * math.log10(frequency_mhz)
        - 13.82 * math.log10(tx_height_m)
        - correction
        + (44.9 - 6.55 * math.log10(tx_height_m)) * math.log10(distance_km)
    )
    if environment == "suburban":
        loss -= 2.0 * (math.log10(frequency_mhz / 28.0) ** 2) + 5.4
    elif environment in {"open", "rural"}:
        loss -= 4.78 * (math.log10(frequency_mhz) ** 2) - 18.33 * math.log10(frequency_mhz) + 40.94
    return loss


def cost231_hata_path_loss_db(
    frequency_mhz: float,
    distance_km: float,
    tx_height_m: float,
    rx_height_m: float,
    environment: str = "urban",
) -> float:
    """COST 231-Hata path loss, 1500-2000 MHz.

    The PCS-band extension of Hata: GSM 1800, UMTS, LTE bands 1-3.
    """
    _require_positive_height(tx_height_m, rx_height_m)
    metro_correction = ENVIRONMENTS.get(environment, (0.0, 9.0))[0]
    correction = _hata_mobile_correction(frequency_mhz, rx_height_m, environment)
    return (
        46.3
        + 33.9 * math.log10(frequency_mhz)
        - 13.82 * math.log10(tx_height_m)
        - correction
        + (44.9 - 6.55 * math.log10(tx_height_m)) * math.log10(distance_km)
        + metro_correction
    )


def path_loss_db(
    model: str,
    frequency_mhz: float,
    distance_km: float,
    tx_height_m: float,
    rx_height_m: float,
    environment: str = "urban",
) -> float:
    """Dispatch to a named propagation model."""
    if model == "free_space":
        return free_space_path_loss_db(frequency_mhz, distance_km)
    if model == "two_ray":
        return two_ray_path_loss_db(frequency_mhz, distance_km, tx_height_m, rx_height_m)
    if model == "hata":
        return hata_path_loss_db(frequency_mhz, distance_km, tx_height_m, rx_height_m, environment)
    if model == "cost231_hata":
        return cost231_hata_path_loss_db(
            frequency_mhz, distance_km, tx_height_m, rx_height_m, environment
        )
    raise GeoInputError.with_example(
        got=model,
        problem=f"Unknown propagation model. Valid: {', '.join(MODELS)}.",
        example="cost231_hata",
    )


def model_warnings(
    model: str,
    *,
    frequency_mhz: float,
    tx_height_m: float,
    rx_height_m: float,
    distance_km: float,
) -> list[str]:
    """Report parameters outside the model's published validity range.

    An empirical model evaluated outside its range still returns a number. This
    is what stops that number being reported as though it meant something.
    """
    ranges = _VALIDITY.get(model)
    if not ranges:
        return []
    supplied = {
        "frequency_mhz": frequency_mhz,
        "tx_height_m": tx_height_m,
        "rx_height_m": rx_height_m,
        "distance_km": distance_km,
    }
    warnings: list[str] = []
    for name, (low, high) in ranges.items():
        value = supplied[name]
        if not low <= value <= high:
            warnings.append(
                f"{name}={value:g} is outside the published validity range of the "
                f"{model} model ({low:g}-{high:g}); treat the result as indicative only."
            )
    return warnings


def cell_radius_km(
    max_path_loss_db: float,
    *,
    model: str = "cost231_hata",
    frequency_mhz: float = 1800.0,
    tx_height_m: float = 45.0,
    rx_height_m: float = 1.5,
    environment: str = "urban",
    lower_km: float = 0.01,
    upper_km: float = 100.0,
) -> float:
    """Distance at which path loss reaches the maximum allowable value.

    Bisection on a monotonically increasing function. Widened from the previous
    implementation's hard-coded 0.1-20 km window, which silently clamped: a
    high-power rural link that should reach 35 km came back as exactly 20, and
    a short dense-urban cell as exactly 0.1.
    """
    low, high = lower_km, upper_km
    if (
        path_loss_db(model, frequency_mhz, high, tx_height_m, rx_height_m, environment)
        < max_path_loss_db
    ):
        return high  # saturated: reaches beyond the search window
    if (
        path_loss_db(model, frequency_mhz, low, tx_height_m, rx_height_m, environment)
        > max_path_loss_db
    ):
        return low
    for _ in range(80):
        mid = (low + high) / 2.0
        if (
            path_loss_db(model, frequency_mhz, mid, tx_height_m, rx_height_m, environment)
            < max_path_loss_db
        ):
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


@dataclass(frozen=True)
class LinkBudget:
    """The result of a link-budget calculation."""

    eirp_dbm: float
    max_allowable_path_loss_db: float
    max_path_loss_with_margin_db: float
    cell_radius_km: float
    cell_area_km2: float
    model: str
    warnings: list[str] = field(default_factory=list)


def link_budget(
    *,
    frequency_mhz: float = 1800.0,
    tx_power_dbm: float = 43.0,
    tx_gain_dbi: float = 15.0,
    rx_gain_dbi: float = 0.0,
    losses_db: float = 3.0,
    rx_sensitivity_dbm: float = -105.0,
    tx_height_m: float = 45.0,
    rx_height_m: float = 1.5,
    environment: str = "urban",
    shadow_margin_db: float | None = None,
    model: str = "cost231_hata",
) -> LinkBudget:
    """Compute EIRP, maximum allowable path loss and the resulting cell radius.

    ``shadow_margin_db`` defaults to a typical value for the environment: more
    cluttered environments need a larger margin for the same edge reliability.
    """
    if environment not in ENVIRONMENTS:
        raise GeoInputError.with_example(
            got=environment,
            problem=f"Unknown environment. Valid: {', '.join(ENVIRONMENTS)}.",
            example="suburban",
        )
    margin = ENVIRONMENTS[environment][1] if shadow_margin_db is None else shadow_margin_db
    eirp = tx_power_dbm + tx_gain_dbi - losses_db
    mapl = eirp + rx_gain_dbi - rx_sensitivity_dbm
    mapl_with_margin = mapl - margin
    radius = cell_radius_km(
        mapl_with_margin,
        model=model,
        frequency_mhz=frequency_mhz,
        tx_height_m=tx_height_m,
        rx_height_m=rx_height_m,
        environment=environment,
    )
    # Area of the regular hexagon inscribed in the cell circle, (3*sqrt(3)/2) r^2:
    # what each site serves when cells tile a plan without gaps. It is not the
    # circle's area (pi r^2), which double-counts wherever neighbours overlap.
    area = 2.598 * radius**2
    return LinkBudget(
        eirp_dbm=eirp,
        max_allowable_path_loss_db=mapl,
        max_path_loss_with_margin_db=mapl_with_margin,
        cell_radius_km=radius,
        cell_area_km2=area,
        model=model,
        warnings=model_warnings(
            model,
            frequency_mhz=frequency_mhz,
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
            distance_km=radius,
        ),
    )
