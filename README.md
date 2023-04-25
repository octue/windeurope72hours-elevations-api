# WindEurope 72 Hours Challenge: Octue Elevations API

This is part of the 72 Hours of Digitalisation at WindEurope challenge - [read more here!](https://medium.com/@thclark/72-hours-of-digitalisation-at-windeurope-7aa786be729d)

## Summary

A serverless REST API that returns the ground elevations of the coordinates sent to it. The API accepts any of the
following as inputs:

- H3 cells (a [hierarchical, hexagonal coordinate system](https://h3geo.org/) that combines position with resolution in
  a single index)
- Latitude/longitude coordinates
- A polygon defined by a set of latitude/longitude coordinates (the output is the elevations of the H3 cells within the
  polygon are returned)

Elevations are defined as meters above the coordinate reference system defined in the underlying dataset (see data
sources section) as:

```
Horizontal WGS84-G1150 (EPSG 4326) (DGED & DTED format), (EPSG 3035) for continental Europe and UTM , (EPSG 32740,
32622, 32738, 32620) for the French DOMs (INSPIRE format), Vertical EGM2008 (EPSG 3855).
```

## Usage

### Method 1 - H3 cells

Request the elevations of a list of H3 cells:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"h3_cells": [630949280935159295, 630949280220393983]}' \
  https://europe-west1-windeurope72-private.cloudfunctions.net/elevations-api
```

**Notes**

- The H3 cells must be given in their integer form (not their hexadecimal string form)
- Requests of this form are limited to 15 cells per request.

### Method 2 - Latitude/longitude coordinates

Request the elevations of a list of latitude/longitude coordinates:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"coordinates": [[54.53097, 5.96836]]}' \
  https://europe-west1-windeurope72-private.cloudfunctions.net/elevations-api
```

**Notes**

- The latitude and longitude coordinates must be given in decimal degrees
- A `resolution` field can also be included - this should be one of the H3 resolution levels. The default of 12 is used if
  not included.
- Requests of this form are limited to 15 cells per request.

### Method 3 - H3 cells within a polygon

Request the elevations of the H3 cells contained within a polygon defined by a list of latitude/longitude coordinates:

```shell
curl \
  --header "Content-Type: application/json" \
  --request POST \
  --data '{"polygon": [[54.53097, 5.96836], [54.53075, 5.96435], [54.52926, 5.96432], [54.52903, 5.96888]]}' \
  https://europe-west1-windeurope72-private.cloudfunctions.net/elevations-api
```

**Notes**

- The latitude and longitude coordinates must be given in decimal degrees
- A `resolution` field can also be included:
  - This should be one of the H3 resolution levels
  - The default of 12 is used if not included
  - The returned cells will be of this resolution
- Requests of this form are limited to polygons that contain up to 1500 cells per request. You can reduce the number of
  cells within a polygon by decreasing the resolution.

### Response

A typical response will look like:

```json
{
  "data": {
    "elevations": { "631574537555217407": 33.0 }
  },
  "schema_info": "https://strands.octue.com/octue/h3-elevations-output",
  "schema_uri": "https://jsonschema.registry.octue.com/octue/h3-elevations-output/0.1.2.json"
}
```

As the API's database is lazily loaded (see output data section below), it will respond to any requests for coordinates
it hasn't seen before by asking you to come back after a short estimated wait time (240s) while the database is
populated. The response will look like:

```json
{
  "data": {
    "elevations": {},
    "later": [631574537555217407],
    "estimated_wait_time": 240
  },
  "schema_info": "https://strands.octue.com/octue/h3-elevations-output",
  "schema_uri": "https://jsonschema.registry.octue.com/octue/h3-elevations-output/0.1.2.json"
}
```

Resend the same request after the wait time has passed to get the elevations.

### Data schema

The exact JSON schema for API inputs and outputs are below.

#### Input

- Information: https://strands.octue.com/octue/h3-elevations-input
- JSON schema: https://jsonschema.registry.octue.com/octue/h3-elevations-input/0.1.0.json

Note that data is only accepted via `POST` request.

#### Output

- Information: https://strands.octue.com/octue/h3-elevations-output
- JSON schema: https://jsonschema.registry.octue.com/octue/h3-elevations-output/0.1.2.json

## Output data

### Data storage

The data served by the API is stored in a Neo4j graph database, which is "lazily" populated by our
[elevations populator data service](https://github.com/octue/windeurope72hours-elevations-populator). The populator
works by extracting the elevations of the centerpoints of high resolution H3 cells at a 30m spatial resolution from the
underlying data source (see data sources section below); elevations for lower resolution cells are calculated by
averaging each cell's immediate children's elevations. In the database, cells, elevations, and data sources are nodes
connected by edges that define their relationships to each other.

#### Why store a copy of the data?

The original data is available only via latitude/longitude coordinates at a single resolution in a format that's
difficult to automatically use. To facilitate the quick data access at multiple resolutions using the H3 coordinate
system, we created an intermediate graph database that efficiently stores the relationships between H3 cells and can be
easily and quickly queried.

#### Why lazily populate instead of loading the whole dataset?

We chose lazy-loading to reduce the up-front cloud computation and storage costs of populating trillions of data points.
Once an elevation has been added to the database, however, it is permanently available.

### Limitations

As in the original dataset:

- The elevations of all oceans appear as 0m
- The elevations of a small number of large bodies of water (e.g. the Caspian Sea) appear as having a constant negative
  non-zero elevation
- We are currently only able to provide elevations for H3 cells between resolution 8 and 12. However:
  - We're likely to be able to decrease the minimum resolution to 6 or below with time
  - We're unlikely to be able to increase the maximum resolution beyond 12 - with the underlying dataset, it's not
    possible to provide meaningful elevations for cells with resolutions higher than 12 because they have a higher
    spatial resolution than the dataset

## Data sources

The underlying dataset we used to provide the elevations is the Copernicus DEM - Global and European Digital Elevation
Model (COP-DEM) GLO-30 dataset:

- DOI: https://doi.org/10.5270/ESA-c5d3d65
- Direct link: https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model

We accessed it via the AWS S3 mirror, which provides easy access to the dataset's GeoTIFF files:

- Information: https://copernicus-dem-30m.s3.amazonaws.com/readme.html
- URL: https://copernicus-dem-30m.s3.amazonaws.com
- S3 URI: `s3://copernicus-dem-30m/`
