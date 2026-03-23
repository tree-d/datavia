# Datavia Soil Pipeline

Soil data pipeline for the Datavia geospatial data integration system.
Integrates two remote sources — **SoilGrids** (WCS API) and **HiHydroSoil**
(HTTP GeoTIFF catalogue) — through a single `SoilPipeline` interface backed by
a `CompositeDownloader` that routes each coverage to the correct backend.
All data is registered as individual single-band GeoTIFF layers in PostGIS.
Downloads are incremental: only missing coverages are fetched on each call.

## Installation

```bash
# Recommended: Install core + soil pipeline
pip install datavia[soil]

# Alternative: Install packages separately (equivalent result)
pip install datavia-soil  # Automatically installs datavia core + soilgrids as dependencies

# Development: Build from source
git clone https://github.com/tree-d/datavia.git
cd datavia
./build_all.sh
pip install dist/datavia-*.whl dist/datavia_soil-*.whl
```

## Usage

```python
from datavia import Datavia
from datavia.soil import SoilPipeline
import numpy as np

# Initialize pipeline (defaults: clay, sand, silt, ph, carbon +
# field_capacity, wilting_point, porosity, hydraulic_conductivity)
soil = SoilPipeline()

# Create controller and initialise all components
dv = Datavia(pipelines=[soil])
dv()

# Download missing coverages (incremental — safe to call repeatedly)
soil.update_data()

# Get soil data for coordinates (longitude, latitude in EPSG:4326)
coords = np.array([[10.0, 50.0], [11.0, 51.0]])

# get_data() returns dict[str, np.ndarray] keyed by coverage ID,
# e.g. {"clay_0-5cm_mean": array([...]), "sand_0-5cm_mean": array([...]), ...}
soil_data = soil.get_data(coords=coords, crs_coords="EPSG:4326")

# SoilGrids values are integer-scaled — convert to common units:
print(f"Clay:  {soil_data['clay_0-5cm_mean'] / 10:.1f} %")      # g/kg → %
print(f"Sand:  {soil_data['sand_0-5cm_mean'] / 10:.1f} %")
print(f"Silt:  {soil_data['silt_0-5cm_mean'] / 10:.1f} %")
print(f"pH:    {soil_data['ph_0-5cm_mean'] / 10:.2f}")           # pH×10 → pH
print(f"SOC:   {soil_data['carbon_0-5cm_mean'] / 10:.1f} g/kg")  # dg/kg → g/kg

# HiHydroSoil values are stored as integers × 10 000 → multiply by 0.0001
print(f"Field capacity:         {soil_data['field_capacity_0-5cm_mean'] * 0.0001:.4f} cm³/cm³")
print(f"Wilting point:          {soil_data['wilting_point_0-5cm_mean'] * 0.0001:.4f} cm³/cm³")
print(f"Porosity:               {soil_data['porosity_0-5cm_mean'] * 0.0001:.4f} cm³/cm³")
print(f"Hydraulic conductivity: {soil_data['hydraulic_conductivity_0-5cm_mean'] * 0.0001:.4f} cm/day")
```

### Requesting specific properties and depths

```python
# Single property, single depth → returns np.ndarray directly (not a dict)
clay_values = soil.get_data(
    coords=coords,
    properties=["clay"],
    depths=["0-5cm"],
    crs_coords="EPSG:4326",
)

# Reconfigure the pipeline for a different property set
soil.configure(
    properties=["clay", "ph", "field_capacity"],
    depths=["0-5cm", "5-15cm"],
)
soil.update_data()  # download any newly requested coverages
```

### Checking available data

```python
# Properties currently stored in the local database
print(soil.get_available_properties())
# ['clay', 'carbon', 'field_capacity', 'hydraulic_conductivity', ...]
```

## Properties

| Canonical name               | Source      | Mapped unit (raw)          | ÷ factor | Conventional unit       | Notes                        |
|------------------------------|-------------|----------------------------|----------|-------------------------|------------------------------|
| `clay`                       | SoilGrids   | g/kg                       | 10       | g/100g (%)              |                              |
| `sand`                       | SoilGrids   | g/kg                       | 10       | g/100g (%)              |                              |
| `silt`                       | SoilGrids   | g/kg                       | 10       | g/100g (%)              |                              |
| `ph`                         | SoilGrids   | pH×10                      | 10       | pH                      | API name: `phh2o`            |
| `carbon`                     | SoilGrids   | dg/kg                      | 10       | g/kg                    | API name: `soc`              |
| `bdod`                       | SoilGrids   | cg/cm³                     | 100      | kg/dm³                  | Bulk density                 |
| `cec`                        | SoilGrids   | mmol(c)/kg                 | 10       | cmol(c)/kg              | Cation exchange capacity     |
| `cfvo`                       | SoilGrids   | cm³/dm³ (vol‰)             | 100      | cm³/100cm³ (vol%)       | Coarse fragments             |
| `nitrogen`                   | SoilGrids   | cg/kg                      | 100      | g/kg                    |                              |
| `ocd`                        | SoilGrids   | hg/m³                      | 10       | kg/m³                   | Organic carbon density       |
| `ocs`                        | SoilGrids   | t/ha                       | 10       | kg/m²                   | Organic carbon stock         |
| `wv0010`, `wv0033`, `wv1500` | SoilGrids   | 10⁻³ cm³/cm³               | 10       | 10⁻² cm³/cm³ (v%)       | Volumetric water content     |
| `field_capacity`             | HiHydroSoil | int (×10⁴)                 | 10 000   | cm³/cm³                 | API name: `WCpF2`            |
| `wilting_point`              | HiHydroSoil | int (×10⁴)                 | 10 000   | cm³/cm³                 | API name: `WCpF4.2`          |
| `porosity`                   | HiHydroSoil | int (×10⁴)                 | 10 000   | cm³/cm³                 | API name: `WCsat`            |
| `hydraulic_conductivity`     | HiHydroSoil | int (×10⁴)                 | 10 000   | cm/day                  | API name: `Ksat`             |

### SoilGrids depth layers
`"0-5cm"`, `"0-30cm"`, `"5-15cm"`, `"15-30cm"`, `"30-60cm"`, `"60-100cm"`, `"100-200cm"`
(default: `["0-5cm", "5-15cm"]`)

### HiHydroSoil depth layers
`"0-5cm"`, `"5-15cm"`, `"15-30cm"`, `"30-60cm"`, `"60-100cm"`, `"100-200cm"`
(default: all six)

### Statistics
SoilGrids: `"Q0.05"`, `"Q0.5"`, `"Q0.95"`, `"mean"`, `"uncertainty"` (default: `"mean"`)
HiHydroSoil: `"mean"` only

## Data Sources

| Source        | Protocol     | Resolution | Coverage          |
|---------------|--------------|------------|-------------------|
| SoilGrids     | WCS API      | 250 m      | Germany (bbox)    |
| HiHydroSoil   | HTTP vsicurl | 250 m      | Germany (clip)    |

- **SoilGrids**: [ISRIC – World Soil Information](https://www.isric.org/)
- **HiHydroSoil**: [BioDT OpenDAP catalogue](http://opendap.biodt.eu/grasslands-pdt/soilMapsHiHydroSoil/)

## Data Licensing & Attribution

### SoilGrids

SoilGrids maps are published under the
[Creative Commons Attribution 4.0 International Licence (CC-BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
Any publication or product that uses SoilGrids data **must cite**:

> Poggio, L., de Sousa, L. M., Batjes, N. H., Heuvelink, G. B. M., Kempen, B.,
> Ribeiro, E., and Rossiter, D.: SoilGrids 2.0: producing soil information for the
> globe with quantified spatial uncertainty,
> *SOIL*, 7, 217–240, 2021.
> [https://doi.org/10.5194/soil-7-217-2021](https://doi.org/10.5194/soil-7-217-2021)

### HiHydroSoil

HiHydroSoil v2.0 is free to use and redistribute **with attribution**
(see [License\_HHSv2.txt](http://opendap.biodt.eu/grasslands-pdt/soilMapsHiHydroSoil/License_HHSv2.txt)).
Any publication or product that uses HiHydroSoil data **must include**:

> Simons, G.W.H., R. Koster, P. Droogers. 2020.
> HiHydroSoil v2.0 – A high resolution soil map of global hydraulic properties.
> FutureWater Report 213.

## Dependencies

- `datavia` (core system)
- `soilgrids` (SoilGrids API client)
- Standard geospatial dependencies (rasterio, numpy, etc.)