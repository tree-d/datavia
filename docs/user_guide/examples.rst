Examples
========

This section provides comprehensive examples for different use cases.

Example 1: City Elevation Profile
---------------------------------

Get elevation data for major German cities and create a profile:

.. code-block:: python

    #!/usr/bin/env python3
    """
    Create an elevation profile for major German cities.
    """
    import numpy as np
    import matplotlib.pyplot as plt
    from datavia.getter import get_data, DataSource

    def city_elevation_profile():
        """Create elevation profile for German cities."""
        
        # Define major German cities
        cities = {
            "Hamburg": [9.9937, 53.5511],
            "Berlin": [13.4050, 52.5200],
            "Cologne": [6.9603, 50.9375],
            "Frankfurt": [8.6821, 50.1109],
            "Stuttgart": [9.1829, 48.7758],
            "Munich": [11.5820, 48.1351],
        }
        
        # Convert to coordinate array
        coords = np.array(list(cities.values()))
        city_names = list(cities.keys())
        
        # Get elevation data
        elevations = get_data(coords, DataSource.TOPOGRAPHY)
        
        # Create visualization
        plt.figure(figsize=(12, 6))
        bars = plt.bar(city_names, elevations, color='skyblue', edgecolor='navy')
        plt.ylabel('Elevation (m)')
        plt.title('Elevation Profile of Major German Cities')
        plt.xticks(rotation=45)
        
        # Add value labels on bars
        for bar, elevation in zip(bars, elevations):
            plt.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 5,
                    f'{elevation:.0f}m', ha='center', va='bottom')
        
        plt.tight_layout()
        plt.show()
        
        # Print summary
        print("German Cities Elevation Summary:")
        print("-" * 40)
        for city, coord, elevation in zip(city_names, coords, elevations):
            print(f"{city:>10}: {elevation:>6.1f}m (at {coord[1]:.2f}°N, {coord[0]:.2f}°E)")  # Fixed: coord[1]=lat, coord[0]=lon

    if __name__ == "__main__":
        city_elevation_profile()

Example 2: Hiking Trail Elevation
---------------------------------

Analyze elevation along a hiking trail:

.. code-block:: python

    #!/usr/bin/env python3
    """
    Create elevation profile for a hiking trail.
    """
    import numpy as np
    import matplotlib.pyplot as plt
    from datavia.getter import get_data, DataSource

    def hiking_trail_elevation():
        """Analyze elevation along a trail from Munich to Zugspitze area."""
        
        # Define trail points
        trail_coords = np.array([
            [11.5820, 48.1351],  # Munich
            [11.6000, 48.1500],  # Munich suburbs
            [11.7000, 48.2000],  # Leaving Munich
            [11.8000, 48.2500],  # Approaching Alps
            [11.9000, 48.3000],  # Alpine foothills
            [10.9500, 47.4500],  # Near Zugspitze region
        ])
        
        # Get elevation data
        elevations = get_data(trail_coords, DataSource.TOPOGRAPHY)
        
        # Calculate distances (simplified - using coordinate differences)
        distances = [0]
        for i in range(1, len(trail_coords)):
            lat_diff = trail_coords[i][0] - trail_coords[i-1][0]
            lon_diff = trail_coords[i][1] - trail_coords[i-1][1]
            # Rough distance calculation (not accurate for real navigation!)
            dist = np.sqrt(lat_diff**2 + lon_diff**2) * 111  # ~111 km per degree
            distances.append(distances[-1] + dist)
        
        # Create elevation profile
        plt.figure(figsize=(12, 6))
        plt.plot(distances, elevations, 'b-o', linewidth=2, markersize=6)
        plt.fill_between(distances, elevations, alpha=0.3)
        plt.xlabel('Distance (km)')
        plt.ylabel('Elevation (m)')
        plt.title('Hiking Trail Elevation Profile: Munich to Alpine Region')
        plt.grid(True, alpha=0.3)
        
        # Add elevation markers
        for i, (dist, elev) in enumerate(zip(distances, elevations)):
            plt.annotate(f'{elev:.0f}m', (dist, elev), 
                        textcoords="offset points", xytext=(0,10), ha='center')
        
        plt.tight_layout()
        plt.show()
        
        # Print trail statistics
        print("Trail Elevation Statistics:")
        print("-" * 30)
        print(f"Starting elevation: {elevations[0]:.1f}m")
        print(f"Ending elevation: {elevations[-1]:.1f}m")
        print(f"Elevation gain: {elevations[-1] - elevations[0]:.1f}m")
        print(f"Maximum elevation: {np.max(elevations):.1f}m")
        print(f"Total distance: {distances[-1]:.1f}km")

    if __name__ == "__main__":
        hiking_trail_elevation()

Example 3: Topographic Grid Analysis
------------------------------------

Create a topographic grid for a region:

.. code-block:: python

    #!/usr/bin/env python3
    """
    Create a topographic grid analysis for a region.
    """
    import numpy as np
    import matplotlib.pyplot as plt
    from datavia.getter import get_data, DataSource

    def topographic_grid_analysis():
        """Create a topographic grid around Munich."""
        
        # Define grid around Munich
        munich_lon, munich_lat = 11.5820, 48.1351
        
        # Create a grid (be careful not to make it too large!)
        lat_range = np.linspace(munich_lat - 0.1, munich_lat + 0.1, 5)
        lon_range = np.linspace(munich_lon - 0.1, munich_lon + 0.1, 5)
        
        # Create coordinate mesh
        coords = []
        for lat in lat_range:
            for lon in lon_range:
                coords.append([lon, lat])

        coords = np.array(coords)
        
        # Get elevation data
        elevations = get_data(coords, DataSource.TOPOGRAPHY)
        
        # Reshape for plotting
        elevation_grid = elevations.reshape(len(lat_range), len(lon_range))
        
        # Create topographic map
        plt.figure(figsize=(10, 8))
        contour = plt.contourf(lon_range, lat_range, elevation_grid, 
                              levels=15, cmap='terrain')
        plt.colorbar(contour, label='Elevation (m)')
        
        # Add contour lines
        plt.contour(lon_range, lat_range, elevation_grid, 
                   levels=15, colors='black', alpha=0.5, linewidths=0.5)
        
        # Mark Munich center
        plt.plot(munich_lon, munich_lat, 'r*', markersize=15, label='Munich')
        
        plt.xlabel('Longitude')
        plt.ylabel('Latitude')
        plt.title('Topographic Map around Munich')
        plt.legend()
        plt.tight_layout()
        plt.show()
        
        # Print grid statistics
        print("Topographic Grid Statistics:")
        print("-" * 30)
        print(f"Grid size: {len(lat_range)} x {len(lon_range)}")
        print(f"Total points: {len(coords)}")
        print(f"Elevation range: {np.min(elevations):.1f}m - {np.max(elevations):.1f}m")
        print(f"Mean elevation: {np.mean(elevations):.1f}m")

    if __name__ == "__main__":
        topographic_grid_analysis()

Example 4: Real-World Integration
---------------------------------

Integrate Datavia with a real application:

.. code-block:: python

    #!/usr/bin/env python3
    """
    Real-world example: Flight path elevation analysis.
    """
    import numpy as np
    import pandas as pd
    from datavia.getter import get_data, DataSource

    class FlightPathAnalyzer:
        """Analyze elevation along flight paths."""
        
        def __init__(self):
            self.flight_data = []
        
        def add_waypoint(self, name, lat, lon):
            """Add a waypoint to the flight path."""
            self.flight_data.append({
                'name': name,
                'lat': lat,
                'lon': lon
            })
        
        def analyze_elevation_clearance(self, min_clearance=500):
            """Analyze elevation clearance along flight path."""
            if len(self.flight_data) < 2:
                raise ValueError("Need at least 2 waypoints")
            
            # Convert to coordinates (longitude, latitude) - CORRECTED ORDER
            coords = np.array([[wp['lon'], wp['lat']] for wp in self.flight_data])  # Fixed: lon first, lat second
            
            # Get ground elevation
            ground_elevations = get_data(coords, DataSource.TOPOGRAPHY)
            
            # Create detailed analysis
            results = []
            for i, (wp, elevation) in enumerate(zip(self.flight_data, ground_elevations)):
                # Typical cruise altitude for domestic flights
                cruise_altitude = 10000  # 10km typical cruise
                clearance = cruise_altitude - elevation
                
                results.append({
                    'waypoint': wp['name'],
                    'lat': wp['lat'],
                    'lon': wp['lon'],
                    'ground_elevation': elevation,
                    'cruise_altitude': cruise_altitude,
                    'clearance': clearance,
                    'safe': clearance >= min_clearance
                })
            
            return pd.DataFrame(results)
        
        def print_analysis(self):
            """Print flight path elevation analysis."""
            df = self.analyze_elevation_clearance()
            
            print("Flight Path Elevation Analysis")
            print("=" * 50)
            print(f"{'Waypoint':<15} {'Ground(m)':<10} {'Clearance(m)':<12} {'Safe':<6}")
            print("-" * 50)
            
            for _, row in df.iterrows():
                safety = "✓" if row['safe'] else "✗"
                print(f"{row['waypoint']:<15} {row['ground_elevation']:<10.1f} "
                      f"{row['clearance']:<12.1f} {safety:<6}")
            
            # Summary
            min_clearance = df['clearance'].min()
            min_location = df.loc[df['clearance'].idxmin(), 'waypoint']
            
            print("-" * 50)
            print(f"Minimum clearance: {min_clearance:.1f}m at {min_location}")
            print(f"All waypoints safe: {'Yes' if df['safe'].all() else 'No'}")

    def flight_path_example():
        """Example flight path analysis."""
        
        # Create analyzer
        analyzer = FlightPathAnalyzer()
        
        # Add waypoints for a domestic German flight
        analyzer.add_waypoint("Berlin", 52.5200, 13.4050)
        analyzer.add_waypoint("Leipzig", 51.3397, 12.3731)
        analyzer.add_waypoint("Nuremberg", 49.4521, 11.0767)
        analyzer.add_waypoint("Munich", 48.1351, 11.5820)
        
        # Analyze and print results
        analyzer.print_analysis()

    if __name__ == "__main__":
        flight_path_example()

Example 5: Performance Testing
------------------------------

Test performance with different coordinate batch sizes:

.. code-block:: python

    #!/usr/bin/env python3
    """
    Performance testing example.
    """
    import time
    import numpy as np
    from datavia.getter import get_data, DataSource

    def performance_test():
        """Test performance with different batch sizes."""
        
        print("Datavia Performance Testing")
        print("=" * 40)
        
        # Test different batch sizes
        batch_sizes = [1, 10, 50, 100, 500]
        
        for batch_size in batch_sizes:
            # Generate random coordinates within Germany
            lons = np.random.uniform(6.0, 15.0, batch_size)
            lats = np.random.uniform(47.5, 55.0, batch_size)
            coords = np.column_stack([lons, lats])
            
            # Time the operation
            start_time = time.time()
            elevations = get_data(coords, DataSource.TOPOGRAPHY)
            end_time = time.time()
            
            duration = end_time - start_time
            points_per_second = batch_size / duration if duration > 0 else float('inf')
            
            print(f"Batch size {batch_size:>3}: "
                  f"{duration:>6.3f}s ({points_per_second:>6.1f} points/sec)")
        
        print("\nPerformance recommendations:")
        print("- Batch multiple coordinates for better performance")
        print("- Typical performance: 100-1000 points/second")
        print("- Network latency affects small batches more")

    if __name__ == "__main__":
        performance_test()

Running the Examples
--------------------

All examples can be run directly:

.. code-block:: bash

    # Copy any example to a file and run it
    pixi run python your_example.py

Make sure you have the required dependencies:

.. code-block:: bash

    pip install matplotlib pandas  # For visualization examples

Next Steps
----------

* Read the :doc:`../api/getter` for detailed API documentation
* Learn about :doc:`../developer/architecture`
* Explore the source code in the repository
