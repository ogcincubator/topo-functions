import pytest

from topo_validator.model import errors_only
from topo_validator.validator import validate_topology


@pytest.mark.parametrize(
    "fixture_name",
    [
        "unit_cube",
        "two_adjacent_cubes",
        "nested_cubes",
        "hollow_cube",
    ],
)
def test_in_memory_sample_validates(request, fixture_name):
    data = request.getfixturevalue(fixture_name)
    issues = validate_topology(data)

    assert not errors_only(issues)