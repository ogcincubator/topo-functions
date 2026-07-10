import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TESTS_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = TESTS_DIR / "output"

# Each submodule's tests persist their generated GeoJSON into their own
# subdirectory so outputs don't collide or get mixed up between modules.
RDF_OUTPUT_DIR = OUTPUT_DIR / "topo_rdf_geojson"
JSON_OUTPUT_DIR = OUTPUT_DIR / "topo2geojson"

for _dir in (RDF_OUTPUT_DIR, JSON_OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)