from datavia.soil import SoilPipeline

test = SoilPipeline()
test()
test.update_data()

print(test.get_data(coords=[[13.4050, 52.5200]], crs_coords="EPSG:4326"))
