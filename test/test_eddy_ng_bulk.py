from types import SimpleNamespace

import pytest

from klippy.extras.ldc1612_ng import LDC1612_ng


@pytest.mark.parametrize(
    "samples,overflows,expected,errors",
    [
        pytest.param([(1.0, 1234)], 0, [(1.0, 1234)], 0, id="clean-batch"),
        pytest.param(
            [(1.0, 1234), (1.1, 0x10000000), (1.2, 5678)],
            3,
            [(1.0, 1234), (1.2, 5678)],
            1,
            id="sensor-error-and-batch-overflow",
        ),
    ],
)
def test_eddy_ng_consumes_native_bulk_reader_result(
    samples, overflows, expected, errors
):
    sensor = LDC1612_ng.__new__(LDC1612_ng)
    sensor._verbose = False
    sensor._ffreader = SimpleNamespace(
        pull_samples=lambda: (samples, overflows)
    )

    assert sensor._process_batch(1.3) == {
        "data": expected,
        "errors": errors,
        "overflows": overflows,
    }
