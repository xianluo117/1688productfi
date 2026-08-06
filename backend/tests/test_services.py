from decimal import Decimal

from app.services import calculate_metrics, normalize_group_name


def test_calculate_metrics_uses_real_days_at_or_below_360():
    days, daily, average = calculate_metrics(360, 120, 600)
    assert days == 360
    assert daily == Decimal("0.333333")
    assert average == Decimal("5.000000")


def test_calculate_metrics_uses_220_days_above_360():
    days, daily, average = calculate_metrics(449, 43, 43)
    assert days == 220
    assert daily == Decimal("0.195455")
    assert average == Decimal("1.000000")


def test_zero_orders_is_safe():
    days, daily, average = calculate_metrics(0, 0, 10)
    assert days == 0
    assert daily == Decimal("0")
    assert average is None


def test_group_name_normalization():
    assert normalize_group_name("  夏季   女装 ") == "夏季 女装"
