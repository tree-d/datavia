import numpy as np
from datavia.weather import WeatherPipeline


def weather_analysis():
    pipeline = WeatherPipeline(
        config={
            "source": "ERA5_land",
            "variables": [
                "2m_temperature",
                "temperature_2m_max",
                "temperature_2m_min",
                "total_precipitation",
            ],
            "date_start": "2024-06-01",
            "date_end": "2024-06-30",
        }
    )
    pipeline()
    pipeline.update_data()

    # Define study area (Leipzig region) in UTM zone 32N.
    # 525862.57175766, 5687658.4535247
    easting_min, easting_max = 300000, 340000  # UTM Easting range
    northing_min, northing_max = 5670000, 5710000  # UTM Northing range

    easting_grid = np.linspace(easting_min, easting_max, 200)
    northing_grid = np.linspace(northing_min, northing_max, 200)

    easting_mesh, northing_mesh = np.meshgrid(easting_grid, northing_grid)
    coords = np.column_stack([easting_mesh.flatten(), northing_mesh.flatten()])

    # print(pipeline.get_available_properties())
    temperature = pipeline.get_data(
        coords=coords,
        datetime_utc="2024-06-15T12:00:00Z",
        variable="2m_temperature",
        crs_coords="EPSG:25833",
    )
    print(temperature)


if __name__ == "__main__":
    weather_analysis()
