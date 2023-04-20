# WindEurope 72 Hours Challenge: Elevations API

## Summary

A REST API deployed as a Google Cloud Function that returns the ground elevations of the coordinates sent to it.
The API accepts any of the following as inputs:

- H3 cells (a [hierarchical, hexagonal coordinate system](https://h3geo.org/) that combines position with resolution in
  a single index)
- Latitude/longitude coordinates
- A polygon defined by a set of latitude/longitude coordinates (the output is the elevations of the H3 cells within the
  polygon are returned)

The elevation unit is meters.

## Usage

### H3 cells

Request the elevations of a list of H3 cells:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"h3_cells": [630949280935159295, 630949280220393983]}' \
  https://europe-west1-windeurope72hours.cloudfunctions.net/elevations-api
```

**Notes**

- The H3 cells must be given in their integer form (not their hexadecimal string form)
- Requests of this form are limited to 15 cells per request.

### Latitude/longitudes

Request the elevations of a list of latitude/longitude coordinates:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"coordinates": [[54.53097, 5.96836]]}' \
  https://europe-west1-windeurope72hours.cloudfunctions.net/elevations-api
```

**Notes**

- The latitude and longitude coordinates must be given in decimal degrees
- A `resolution` field can also be included - this should be one of the H3 resolution levels. The default of 12 is used if
  not included.
- Requests of this form are limited to 15 cells per request.

### H3 cells within a polygon

Request the elevations of the H3 cells contained within a polygon defined by a list of latitude/longitude coordinates:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"polygon": [[54.53097, 5.96836], [54.53075, 5.96435], [54.52926, 5.96432], [54.52903, 5.96888]]}' \
  https://europe-west1-windeurope72hours.cloudfunctions.net/elevations-api
```

**Notes**

- The latitude and longitude coordinates must be given in decimal degrees
- A `resolution` field can also be included:
  - This should be one of the H3 resolution levels
  - The default of 12 is used if not included
  - The returned cells will be of this resolution
- Requests of this form are limited to polygons that contain up to 1500 cells per request. You can reduce the number of
  cells within a polygon by decreasing the resolution.

## Input schema

- Information: https://strands.octue.com/octue/h3-elevations-input
- JSON schema: https://jsonschema.registry.octue.com/octue/h3-elevations-input/0.1.0.json

Note that data is only accepted via `POST` request.

## Output schema

- Information: https://strands.octue.com/octue/h3-elevations-output
- JSON schema: https://jsonschema.registry.octue.com/octue/h3-elevations-output/0.1.0.json

## Data

The data served by this API is stored in a Neo4j graph database, which is lazily populated by the [elevations populator
data service](https://github.com/octue/windeurope72hours-elevations-populator). Elevations for high resolution H3 cells
are extracted for the cell centerpoints at a 30m resolution; elevations for lower resolution cells are calculated by
averaging each cell's children's elevations.
