"""RF propagation against published closed-form values."""

from __future__ import annotations

import math

import pytest

from geospatial_mcp.errors import GeoInputError
from geospatial_mcp.geo import rf


class TestFreeSpace:
    def test_matches_published_value(self):
        """2.4 GHz at 1 km is a textbook 100.05 dB."""
        assert rf.free_space_path_loss_db(2400, 1.0) == pytest.approx(100.05, abs=0.02)

    def test_doubling_distance_adds_6db(self):
        near = rf.free_space_path_loss_db(1800, 1.0)
        far = rf.free_space_path_loss_db(1800, 2.0)
        assert far - near == pytest.approx(20 * math.log10(2), abs=1e-9)

    def test_doubling_frequency_adds_6db(self):
        low = rf.free_space_path_loss_db(900, 5.0)
        high = rf.free_space_path_loss_db(1800, 5.0)
        assert high - low == pytest.approx(20 * math.log10(2), abs=1e-9)

    def test_rejects_nonsense_inputs(self):
        with pytest.raises(GeoInputError):
            rf.free_space_path_loss_db(1800, 0)
        with pytest.raises(GeoInputError):
            rf.free_space_path_loss_db(0, 1)


class TestLinkBudget:
    def test_eirp_and_mapl_arithmetic(self):
        budget = rf.link_budget(
            tx_power_dbm=43,
            tx_gain_dbi=15,
            losses_db=3,
            rx_sensitivity_dbm=-105,
            rx_gain_dbi=0,
            environment="urban",
            shadow_margin_db=13,
        )
        assert budget.eirp_dbm == pytest.approx(55.0)
        assert budget.max_allowable_path_loss_db == pytest.approx(160.0)
        assert budget.max_path_loss_with_margin_db == pytest.approx(147.0)

    def test_more_clutter_means_smaller_cells(self):
        radii = [
            rf.link_budget(environment=env, shadow_margin_db=0).cell_radius_km
            for env in ("rural", "suburban", "urban", "dense_urban")
        ]
        assert radii == sorted(radii, reverse=True), radii

    def test_radius_is_not_clamped_at_20km(self):
        """A bisection window that stops at 20 km silently clamps longer links."""
        budget = rf.link_budget(
            tx_power_dbm=60,
            tx_gain_dbi=25,
            rx_sensitivity_dbm=-120,
            environment="rural",
            model="hata",
            frequency_mhz=450,
            tx_height_m=100,
            shadow_margin_db=0,
        )
        assert budget.cell_radius_km > 20.0

    def test_cell_area_is_the_hexagon_not_the_circle(self):
        """Documented as the tiling hexagon; four isolated cells cover more than 4x this."""
        budget = rf.link_budget(frequency_mhz=1800, environment="suburban")
        r = budget.cell_radius_km
        assert budget.cell_area_km2 == pytest.approx(3 * math.sqrt(3) / 2 * r**2, rel=1e-3)
        assert budget.cell_area_km2 < math.pi * r**2

    def test_out_of_range_parameters_are_reported(self):
        """COST231-Hata is only valid from 1500-2000 MHz."""
        budget = rf.link_budget(frequency_mhz=900, model="cost231_hata")
        assert budget.warnings
        assert any("validity range" in w for w in budget.warnings)

    def test_in_range_parameters_are_silent(self):
        budget = rf.link_budget(frequency_mhz=1800, model="cost231_hata", tx_height_m=45)
        assert not [w for w in budget.warnings if "frequency_mhz" in w]

    def test_unknown_environment_names_the_valid_ones(self):
        with pytest.raises(GeoInputError) as exc:
            rf.link_budget(environment="atlantis")
        assert "suburban" in str(exc.value)


class TestModelDispatch:
    @pytest.mark.parametrize("model", rf.MODELS)
    def test_every_model_returns_a_finite_loss(self, model):
        loss = rf.path_loss_db(model, 1800, 5.0, 45.0, 1.5, "urban")
        assert math.isfinite(loss) and loss > 0

    def test_loss_increases_with_distance_for_every_model(self):
        for model in rf.MODELS:
            near = rf.path_loss_db(model, 1800, 1.0, 45.0, 1.5, "urban")
            far = rf.path_loss_db(model, 1800, 10.0, 45.0, 1.5, "urban")
            assert far > near, model

    def test_unknown_model_names_the_valid_ones(self):
        with pytest.raises(GeoInputError, match="cost231_hata"):
            rf.path_loss_db("magic", 1800, 5.0, 45.0, 1.5)


class TestInputGuards:
    """Every log10 call site must reject bad input with a usable message.

    Regression test: a zero frequency once surfaced as a bare
    ``ValueError: math domain error`` from inside math.log10.
    """

    @pytest.mark.parametrize("frequency", [0, -1, -1800])
    def test_non_positive_frequency_is_rejected_everywhere(self, frequency):
        for call in (
            lambda: rf.free_space_path_loss_db(frequency, 1.0),
            lambda: rf.wavelength_m(frequency),
            lambda: rf.hata_path_loss_db(frequency, 5.0, 45.0, 1.5),
            lambda: rf.cost231_hata_path_loss_db(frequency, 5.0, 45.0, 1.5),
            lambda: rf.two_ray_path_loss_db(frequency, 5.0, 45.0, 1.5),
        ):
            with pytest.raises(GeoInputError, match="Frequency must be positive"):
                call()

    @pytest.mark.parametrize("heights", [(0, 1.5), (45, 0), (-10, 1.5)])
    def test_non_positive_heights_are_rejected(self, heights):
        tx, rx = heights
        for call in (
            lambda: rf.hata_path_loss_db(900, 5.0, tx, rx),
            lambda: rf.cost231_hata_path_loss_db(1800, 5.0, tx, rx),
            lambda: rf.two_ray_path_loss_db(1800, 5.0, tx, rx),
        ):
            with pytest.raises(GeoInputError, match="height"):
                call()
